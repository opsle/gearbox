"""Portable Agent Gearbox core.

The runtime executes one admitted gear exactly once. Raw evidence stays in a
private state directory; the caller receives one compact terminal result.
Provider-specific helper transports and Context Firewall adapters are external.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

VERSION = "0.1.0"
REQUEST_SCHEMA = "opsle.gearbox.request.v1"
POLICY_SCHEMA = "opsle.gearbox.policy.v1"
RESULT_SCHEMA = "opsle.gearbox.result.v1"
VALUE_SCHEMA = "opsle.value-receipt.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
SENSITIVE_PATH = re.compile(
    r"(^|/)(\.env(?:\..*)?|auth\.json|credentials?|secrets?|id_[er]sa|.*\.pem)$",
    re.IGNORECASE,
)


class GearboxError(RuntimeError):
    """A fail-closed request, policy, execution, or evidence error."""


class HelperTransport(Protocol):
    """External bounded cognitive transport.

    Implementations must return a mapping with final, stdout, stderr,
    provider_sessions, commands, terminated, model, effort, and transport_id.
    Gearbox never retries or selects an alternate transport.
    """

    def execute(
        self,
        *,
        request: dict[str, Any],
        workspace: Path,
        timeout_seconds: int,
    ) -> dict[str, Any]: ...


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, value: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GearboxError(f"invalid JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise GearboxError(f"JSON value is not an object: {path}")
    return value


def require_keys(
    value: dict[str, Any], required: set[str], allowed: set[str], label: str
) -> None:
    missing = required - set(value)
    unknown = set(value) - allowed
    if missing:
        raise GearboxError(f"{label} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise GearboxError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")


def repository_identity(value: object) -> Path:
    if not isinstance(value, str):
        raise GearboxError("allowed_context.repository must be a path")
    repository = Path(value).resolve()
    try:
        top = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise GearboxError("repository identity is unavailable") from exc
    if Path(top).resolve() != repository:
        raise GearboxError("repository must be the exact Git root")
    return repository


def validate_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise GearboxError("context paths must be nonempty strings")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise GearboxError(f"context path is not normalized: {value}")
    if SENSITIVE_PATH.search(value):
        raise GearboxError(f"sensitive context path is forbidden: {value}")
    return value


def validate_selection(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GearboxError("context selections must be objects")
    kind = value.get("kind")
    common = {"path", "kind"}
    if kind == "new":
        require_keys(value, common, common, "new context selection")
        return {"path": validate_relative_path(value["path"]), "kind": "new"}
    if kind not in {"file", "lines", "symbols"}:
        raise GearboxError("selection kind must be file, lines, symbols, or new")
    required = common | {"source_sha256"}
    allowed = set(required)
    if kind == "lines":
        required.add("ranges")
        allowed.add("ranges")
    if kind == "symbols":
        required.add("symbols")
        allowed.add("symbols")
    require_keys(value, required, allowed, f"{kind} context selection")
    source_hash = value["source_sha256"]
    if not isinstance(source_hash, str) or not SHA256_RE.fullmatch(source_hash):
        raise GearboxError("source_sha256 must be a lowercase SHA-256")
    normalized: dict[str, Any] = {
        "path": validate_relative_path(value["path"]),
        "kind": kind,
        "source_sha256": source_hash,
    }
    if kind == "lines":
        ranges = value["ranges"]
        if not isinstance(ranges, list) or not 1 <= len(ranges) <= 64:
            raise GearboxError("line selections must contain 1 to 64 ranges")
        output: list[dict[str, int]] = []
        previous_end = 0
        for item in ranges:
            if not isinstance(item, dict):
                raise GearboxError("line ranges must be objects")
            require_keys(item, {"start_line", "end_line"}, {"start_line", "end_line"}, "line range")
            start = item["start_line"]
            end = item["end_line"]
            if (
                not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or start < 1
                or end < start
                or start <= previous_end
            ):
                raise GearboxError("line ranges must be positive, ordered, and non-overlapping")
            output.append({"start_line": start, "end_line": end})
            previous_end = end
        normalized["ranges"] = output
    if kind == "symbols":
        symbols = value["symbols"]
        if (
            not isinstance(symbols, list)
            or not 1 <= len(symbols) <= 64
            or not all(isinstance(item, str) and SYMBOL_RE.fullmatch(item) for item in symbols)
            or len(set(symbols)) != len(symbols)
        ):
            raise GearboxError("symbols must be 1 to 64 unique qualified names")
        normalized["symbols"] = sorted(symbols)
    return normalized


def load_policy(authority: object) -> tuple[dict[str, Any], str]:
    if not isinstance(authority, dict):
        raise GearboxError("authority must be an object")
    require_keys(authority, {"policy_path", "policy_sha256"}, {"policy_path", "policy_sha256"}, "authority")
    path_value = authority["policy_path"]
    expected_hash = authority["policy_sha256"]
    if not isinstance(path_value, str) or not Path(path_value).is_absolute():
        raise GearboxError("authority.policy_path must be absolute")
    if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
        raise GearboxError("authority.policy_sha256 must be a lowercase SHA-256")
    path = Path(path_value)
    try:
        if path.is_symlink() or not path.is_file():
            raise GearboxError("authority policy must be a regular non-symlink file")
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise GearboxError("authority policy must be a regular file")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
            ):
                raise GearboxError("authority policy drifted while read")
            policy_bytes = b"".join(chunks)
        finally:
            os.close(descriptor)
        actual_hash = sha256_bytes(policy_bytes)
    except OSError as exc:
        raise GearboxError("authority policy is unavailable") from exc
    if actual_hash != expected_hash:
        raise GearboxError("authority policy hash drifted")
    try:
        policy = json.loads(policy_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GearboxError("authority policy is not valid UTF-8 JSON") from exc
    if not isinstance(policy, dict):
        raise GearboxError("authority policy is not an object")
    require_keys(policy, {"schema", "authority_id", "gears"}, {"schema", "authority_id", "gears"}, "policy")
    if policy["schema"] != POLICY_SCHEMA:
        raise GearboxError("unsupported policy schema")
    if not isinstance(policy["authority_id"], str) or not policy["authority_id"]:
        raise GearboxError("policy authority_id is invalid")
    gears = policy["gears"]
    if not isinstance(gears, dict) or not gears:
        raise GearboxError("policy gears must be a nonempty object")
    for name, gear in gears.items():
        if not isinstance(name, str) or not name or not isinstance(gear, dict):
            raise GearboxError("policy gear entries are malformed")
        kind = gear.get("kind")
        if kind == "deterministic":
            required = {
                "kind", "task_types", "argv_exact", "executable_path",
                "executable_sha256",
            }
            require_keys(gear, required, required, f"policy gear {name}")
            argv = gear["argv_exact"]
            if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
                raise GearboxError(f"policy gear {name} argv_exact is invalid")
            executable_value = gear["executable_path"]
            if not isinstance(executable_value, str) or not executable_value:
                raise GearboxError(f"policy gear {name} executable_path is invalid")
            executable_path = Path(executable_value)
            if not executable_path.is_absolute():
                executable_path = path.parent / executable_path
            executable = executable_path.resolve()
            executable_hash = gear["executable_sha256"]
            if (
                executable_path.is_symlink()
                or not executable.is_file()
                or not os.access(executable, os.X_OK)
                or not isinstance(executable_hash, str)
                or not SHA256_RE.fullmatch(executable_hash)
            ):
                raise GearboxError(f"policy gear {name} executable identity is invalid")
            if sha256_file(executable) != executable_hash:
                raise GearboxError(f"policy gear {name} executable hash drifted")
            gear["resolved_executable"] = str(executable)
        elif kind == "helper":
            required = {
                "kind", "task_types", "model", "effort", "max_provider_sessions",
                "transport_id",
            }
            require_keys(gear, required, required, f"policy gear {name}")
            if (
                not isinstance(gear["model"], str)
                or not isinstance(gear["effort"], str)
                or not isinstance(gear["transport_id"], str)
                or not gear["transport_id"]
            ):
                raise GearboxError(f"policy gear {name} model or effort is invalid")
            if gear["max_provider_sessions"] not in {0, 1}:
                raise GearboxError(f"policy gear {name} provider-session limit is invalid")
        else:
            raise GearboxError(f"policy gear {name} has unsupported kind")
        if not isinstance(gear["task_types"], list) or not gear["task_types"] or not all(
            isinstance(item, str) and item for item in gear["task_types"]
        ):
            raise GearboxError(f"policy gear {name} task_types are invalid")
    return policy, actual_hash


def validate_output_schema(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("type") != "object":
        raise GearboxError("output_contract.schema must describe an object")
    if not isinstance(value.get("required"), list) or not isinstance(value.get("properties"), dict):
        raise GearboxError("output_contract.schema requires properties and required")
    if len(canonical_json(value)) > 32 * 1024:
        raise GearboxError("output schema exceeds 32 KiB")
    return value


def validate_helper_output_schema(value: dict[str, Any]) -> None:
    standard = {"verdict", "summary", "changed_paths", "tests", "unresolved_issues"}
    required = value.get("required", [])
    properties = value.get("properties", {})
    if not standard.issubset(required) or not standard.issubset(properties):
        raise GearboxError("helper output schema must require the standard compact fields")
    verdict = properties["verdict"]
    if not isinstance(verdict, dict) or set(verdict.get("enum", [])) != {
        "PASS", "BLOCKED", "FAIL",
    }:
        raise GearboxError("helper verdict must be exactly PASS, BLOCKED, or FAIL")


def validate_request(value: object) -> tuple[dict[str, Any], dict[str, Any], str]:
    if not isinstance(value, dict):
        raise GearboxError("request must be an object")
    fields = {
        "schema", "task", "task_type", "requested_gear", "allowed_context",
        "output_contract", "authority", "budget",
    }
    require_keys(value, fields, fields, "request")
    if value["schema"] != REQUEST_SCHEMA:
        raise GearboxError("unsupported request schema")
    policy, policy_hash = load_policy(value["authority"])
    requested_gear = value["requested_gear"]
    if not isinstance(requested_gear, str) or requested_gear not in policy["gears"]:
        raise GearboxError("requested gear is not authorized")
    gear = policy["gears"][requested_gear]
    task_type = value["task_type"]
    if not isinstance(task_type, str) or task_type not in gear["task_types"]:
        raise GearboxError("task_type is not authorized for the requested gear")
    context = value["allowed_context"]
    if not isinstance(context, dict):
        raise GearboxError("allowed_context must be an object")
    require_keys(context, {"repository", "selections", "writable_paths"}, {"repository", "selections", "writable_paths"}, "allowed_context")
    repository = repository_identity(context["repository"])
    selections_raw = context["selections"]
    writable_raw = context["writable_paths"]
    if not isinstance(selections_raw, list) or len(selections_raw) > 100:
        raise GearboxError("context selections are invalid")
    if not isinstance(writable_raw, list) or len(writable_raw) > 100:
        raise GearboxError("writable paths are invalid")
    selections = [validate_selection(item) for item in selections_raw]
    writable = [validate_relative_path(item) for item in writable_raw]
    if len({item["path"] for item in selections}) != len(selections):
        raise GearboxError("each context path must have one selection")
    if len(set(writable)) != len(writable):
        raise GearboxError("writable paths must be unique")
    by_path = {item["path"]: item for item in selections}
    if any(path not in by_path for path in writable):
        raise GearboxError("writable paths require an explicit selection")
    for item in selections:
        source = repository / item["path"]
        if item["kind"] == "new":
            if item["path"] not in writable or source.exists():
                raise GearboxError("new context must be writable and absent")
        elif not source.exists():
            raise GearboxError(f"selected context source is missing: {item['path']}")
    for path in writable:
        if by_path[path]["kind"] not in {"file", "new"}:
            raise GearboxError("partial context selections cannot be writable")
    output = value["output_contract"]
    if not isinstance(output, dict):
        raise GearboxError("output_contract must be an object")
    require_keys(output, {"schema"}, {"schema"}, "output_contract")
    output_schema = validate_output_schema(output["schema"])
    budget = value["budget"]
    budget_fields = {
        "timeout_seconds", "max_raw_bytes", "max_return_bytes", "max_context_bytes",
        "max_commands", "max_provider_sessions",
    }
    if not isinstance(budget, dict):
        raise GearboxError("budget must be an object")
    require_keys(budget, budget_fields, budget_fields, "budget")
    bounds = {
        "timeout_seconds": (1, 7200),
        "max_raw_bytes": (2048, 64 * 1024 * 1024),
        "max_return_bytes": (2048, 64 * 1024),
        "max_context_bytes": (0, 2 * 1024 * 1024),
        "max_commands": (0, 50),
        "max_provider_sessions": (0, 1),
    }
    for field, (minimum, maximum) in bounds.items():
        item = budget.get(field)
        if not isinstance(item, int) or isinstance(item, bool) or not minimum <= item <= maximum:
            raise GearboxError(f"budget.{field} is outside its bound")
    if budget["max_provider_sessions"] != gear.get("max_provider_sessions", 0):
        raise GearboxError("provider-session budget does not match the authorized gear")
    if gear["kind"] == "deterministic":
        task = value["task"]
        if not isinstance(task, dict) or set(task) != {"argv"} or task["argv"] != gear["argv_exact"]:
            raise GearboxError("deterministic task argv does not exactly match policy")
        if selections or writable or budget["max_context_bytes"] != 0:
            raise GearboxError("deterministic gear cannot receive helper context")
        if budget["max_commands"] != 1:
            raise GearboxError("deterministic gear requires a one-command budget")
    else:
        if not isinstance(value["task"], str) or not 1 <= len(value["task"]) <= 20_000:
            raise GearboxError("helper task must be a bounded string")
        if not selections or budget["max_context_bytes"] < 1 or budget["max_commands"] < 1:
            raise GearboxError("helper gear requires bounded context and command budgets")
        validate_helper_output_schema(output_schema)
    normalized = {
        "schema": REQUEST_SCHEMA,
        "task": value["task"],
        "task_type": task_type,
        "requested_gear": requested_gear,
        "gear": gear,
        "allowed_context": {
            "repository": str(repository),
            "selections": sorted(selections, key=lambda item: item["path"]),
            "writable_paths": sorted(writable),
        },
        "output_contract": {"schema": output_schema},
        "authority": {
            "authority_id": policy["authority_id"],
            "policy_sha256": policy_hash,
        },
        "budget": dict(budget),
    }
    return normalized, policy, policy_hash


def read_source(repository: Path, relative: str) -> bytes:
    source = repository / relative
    try:
        resolved = source.resolve(strict=True)
    except OSError as exc:
        raise GearboxError(f"selected context source is unavailable: {relative}") from exc
    if resolved != source.absolute() or not resolved.is_relative_to(repository):
        raise GearboxError(f"context source crosses a symlink or repository boundary: {relative}")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(source, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise GearboxError(f"context file identity is unsafe: {relative}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise GearboxError(f"context source drifted while read: {relative}")
        value = b"".join(chunks)
        if len(value) != before.st_size:
            raise GearboxError(f"context source size drifted: {relative}")
        return value
    finally:
        os.close(descriptor)


def python_symbol_ranges(text: str, requested: list[str]) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise GearboxError("Python symbol selection requires parseable source") from exc
    found: dict[str, list[tuple[int, int]]] = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def record(self, node: ast.AST, name: str) -> None:
            qualified = ".".join([*self.stack, name])
            decorators = getattr(node, "decorator_list", [])
            start = min([node.lineno] + [item.lineno for item in decorators])
            end = getattr(node, "end_lineno", None)
            if not isinstance(end, int):
                raise GearboxError(f"symbol has no deterministic end: {qualified}")
            found.setdefault(qualified, []).append((start, end))

        def _visit_named(self, node: ast.AST, name: str) -> None:
            self.record(node, name)
            self.stack.append(name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._visit_named(node, node.name)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_named(node, node.name)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_named(node, node.name)

    Visitor().visit(tree)
    selected: list[dict[str, Any]] = []
    for name in requested:
        matches = found.get(name, [])
        if len(matches) != 1:
            raise GearboxError(f"symbol is ambiguous or missing: {name} ({len(matches)} matches)")
        start, end = matches[0]
        selected.append({"symbol": name, "start_line": start, "end_line": end})
    selected.sort(key=lambda item: (item["start_line"], item["end_line"], item["symbol"]))
    previous_end = 0
    for item in selected:
        if item["start_line"] <= previous_end:
            raise GearboxError("selected symbols overlap")
        previous_end = item["end_line"]
    return selected


def select_bytes(source: bytes, selection: dict[str, Any]) -> tuple[bytes, int, list[dict[str, Any]]]:
    kind = selection["kind"]
    if kind == "file":
        return source, len(source), []
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GearboxError("partial context selection requires UTF-8") from exc
    lines = text.splitlines(keepends=True)
    if kind == "lines":
        resolved = [dict(item) for item in selection["ranges"]]
        if any(item["end_line"] > len(lines) for item in resolved):
            raise GearboxError("line selection exceeds source")
    elif kind == "symbols":
        if Path(selection["path"]).suffix != ".py":
            raise GearboxError("symbol selection currently supports Python only")
        resolved = python_symbol_ranges(text, selection["symbols"])
    else:
        raise GearboxError(f"unsupported selection kind: {kind}")
    chunks: list[bytes] = []
    retained = 0
    for item in resolved:
        start = item["start_line"]
        end = item["end_line"]
        body = "".join(lines[start - 1 : end]).encode("utf-8")
        label = f" symbol={item['symbol']}" if "symbol" in item else ""
        chunks.append(f"<<<OPSLE_CONTEXT{label} lines={start}-{end}>>>\n".encode())
        chunks.append(body)
        chunks.append(b"\n<<<END_OPSLE_CONTEXT>>>\n")
        retained += len(body)
    return b"".join(chunks), retained, resolved


@dataclass
class StagedContext:
    workspace: Path
    entries: list[dict[str, Any]]
    packet_bytes: int
    source_bytes: int
    manifest_sha256: str


def stage_context(run_dir: Path, request: dict[str, Any]) -> StagedContext:
    repository = Path(request["allowed_context"]["repository"])
    workspace = run_dir / "workspace"
    workspace.mkdir(mode=0o700)
    entries: list[dict[str, Any]] = []
    packet_total = 0
    source_total = 0
    try:
        for selection in request["allowed_context"]["selections"]:
            relative = selection["path"]
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if selection["kind"] == "new":
                packet = b""
                source_hash = None
                source_size = 0
                resolved: list[dict[str, Any]] = []
            else:
                source = read_source(repository, relative)
                source_hash = sha256_bytes(source)
                if source_hash != selection["source_sha256"]:
                    raise GearboxError(f"context source hash drifted: {relative}")
                packet, _, resolved = select_bytes(source, selection)
                source_size = len(source)
            atomic_write(target, packet)
            entry = {
                "path": relative,
                "kind": selection["kind"],
                "source_sha256": source_hash,
                "source_bytes": source_size,
                "packet_sha256": sha256_bytes(packet),
                "packet_bytes": len(packet),
                "resolved_ranges": resolved,
                "writable": relative in request["allowed_context"]["writable_paths"],
            }
            entries.append(entry)
            packet_total += len(packet)
            source_total += source_size
        if packet_total > request["budget"]["max_context_bytes"]:
            raise GearboxError("context exceeds the hard byte ceiling")
        manifest = {
            "schema": "opsle.gearbox.context-manifest.v1",
            "entries": entries,
            "source_bytes": source_total,
            "packet_bytes": packet_total,
            "complete": True,
        }
        encoded = canonical_json(manifest)
        atomic_write(workspace / "GEARBOX_CONTEXT.json", encoded)
        return StagedContext(workspace, entries, packet_total, source_total, sha256_bytes(encoded))
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise


def revalidate_sources(request: dict[str, Any]) -> None:
    repository = Path(request["allowed_context"]["repository"])
    for selection in request["allowed_context"]["selections"]:
        if selection["kind"] == "new":
            continue
        actual = sha256_bytes(read_source(repository, selection["path"]))
        if actual != selection["source_sha256"]:
            raise GearboxError(f"context source drifted after staging: {selection['path']}")


def workspace_changes(staged: StagedContext) -> list[str]:
    manifest_path = staged.workspace / "GEARBOX_CONTEXT.json"
    if (
        not manifest_path.is_file()
        or manifest_path.is_symlink()
        or sha256_file(manifest_path) != staged.manifest_sha256
    ):
        raise GearboxError("helper changed the context manifest")
    expected = {entry["path"]: entry for entry in staged.entries}
    actual = {
        path.relative_to(staged.workspace).as_posix()
        for path in staged.workspace.rglob("*")
        if path.is_file() and path.name != "GEARBOX_CONTEXT.json"
    }
    unexpected = sorted(actual - set(expected))
    if unexpected:
        raise GearboxError(f"helper created unauthorized paths: {', '.join(unexpected)}")
    changed: list[str] = []
    for relative, entry in expected.items():
        path = staged.workspace / relative
        if not path.is_file() or path.is_symlink():
            raise GearboxError(f"helper removed or replaced context path: {relative}")
        current_hash = sha256_file(path)
        if current_hash != entry["packet_sha256"]:
            if not entry["writable"]:
                raise GearboxError(f"helper changed read-only context: {relative}")
            changed.append(relative)
    return changed


def validate_json_value(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    expected = schema.get("type")
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    if expected in types and (not isinstance(value, types[expected]) or expected in {"integer", "number"} and isinstance(value, bool)):
        raise GearboxError(f"helper final {path} has wrong type")
    if "enum" in schema and value not in schema["enum"]:
        raise GearboxError(f"helper final {path} is outside its enum")
    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise GearboxError(f"helper final {path} is missing: {', '.join(missing)}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise GearboxError(f"helper final {path} has unknown fields")
        for key, item in value.items():
            if key in properties:
                validate_json_value(item, properties[key], f"{path}.{key}")
    if isinstance(value, list):
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            raise GearboxError(f"helper final {path} exceeds maxItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_json_value(item, item_schema, f"{path}[{index}]")
    if isinstance(value, str) and isinstance(schema.get("maxLength"), int) and len(value) > schema["maxLength"]:
        raise GearboxError(f"helper final {path} exceeds maxLength")


def artifact(path: Path, run_dir: Path, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "locator": path.relative_to(run_dir.parent.parent).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def terminate_group(process: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


class GearboxRunner:
    def __init__(
        self,
        state_root: Path,
        *,
        helper_transport: HelperTransport | None = None,
        mechanism_revision: str | None = None,
    ) -> None:
        self.state_root = state_root.resolve()
        self.helper_transport = helper_transport
        self.mechanism_revision = mechanism_revision
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_root, 0o700)
        (self.state_root / "runs").mkdir(exist_ok=True, mode=0o700)

    def run(self, value: object) -> dict[str, Any]:
        request, _policy, policy_hash = validate_request(value)
        fingerprint = sha256_bytes(canonical_json(request))
        run_id = "g" + fingerprint[:24]
        run_dir = self.state_root / "runs" / run_id
        result_path = run_dir / "result.json"
        if result_path.exists():
            return read_object(result_path)
        if run_dir.exists():
            raise GearboxError("incomplete run exists; refusing duplicate execution")
        try:
            run_dir.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise GearboxError("concurrent run exists; refusing duplicate execution") from exc
        (run_dir / "raw").mkdir(mode=0o700)
        atomic_write(run_dir / "request.json", canonical_json(request))
        atomic_write(run_dir / "started.json", canonical_json({"run_id": run_id, "attempt": 1}))
        try:
            if request["gear"]["kind"] == "deterministic":
                partial = self._run_deterministic(run_dir, request)
            else:
                partial = self._run_helper(run_dir, request)
        except GearboxError as exc:
            raw_artifacts = [
                artifact(path, run_dir, f"RAW_{path.name.upper().replace('.', '_')}")
                for path in sorted((run_dir / "raw").glob("*"))
                if path.is_file()
            ]
            partial = {
                "status": "failed",
                "summary": str(exc),
                "exit_code": None,
                "changed_paths": [],
                "provider_sessions": 0,
                "commands": 0,
                "terminated": True,
                "wait_mode": "not_started_or_failed_closed",
                "artifacts": raw_artifacts,
                "escalation": {"required": True, "reason": "GEARBOX_FAIL_CLOSED"},
            }
        result = self._finalize(run_dir, run_id, request, partial)
        encoded = canonical_json(result)
        if len(encoded) > request["budget"]["max_return_bytes"]:
            raise GearboxError("compact result exceeds max_return_bytes")
        receipt = self._value_receipt(result, len(encoded), policy_hash)
        atomic_write(run_dir / "value-receipt.json", canonical_json(receipt))
        atomic_write(result_path, encoded)
        return result

    def _run_deterministic(self, run_dir: Path, request: dict[str, Any]) -> dict[str, Any]:
        stdout_path = run_dir / "raw/stdout.raw"
        stderr_path = run_dir / "raw/stderr.raw"
        repository = Path(request["allowed_context"]["repository"])
        executable = Path(request["gear"]["resolved_executable"])
        if sha256_file(executable) != request["gear"]["executable_sha256"]:
            raise GearboxError("deterministic executable drifted before execution")
        argv = [str(executable), *request["task"]["argv"][1:]]
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            process = subprocess.Popen(
                argv,
                cwd=repository,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                close_fds=True,
                start_new_session=True,
            )
            timed_out = False
            try:
                exit_code = process.wait(timeout=request["budget"]["timeout_seconds"])
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate_group(process)
                exit_code = process.returncode
        raw_bytes = stdout_path.stat().st_size + stderr_path.stat().st_size
        raw_limited = raw_bytes > request["budget"]["max_raw_bytes"]
        status = "completed" if exit_code == 0 and not timed_out and not raw_limited else "blocked"
        if timed_out or raw_limited:
            status = "failed"
        reason = None
        if timed_out:
            reason = "TIMEOUT"
        elif raw_limited:
            reason = "RAW_EVIDENCE_LIMIT"
        elif exit_code != 0:
            reason = "NONZERO_EXIT"
        return {
            "status": status,
            "summary": "deterministic command completed" if status == "completed" else "deterministic command requires escalation",
            "exit_code": exit_code,
            "changed_paths": [],
            "provider_sessions": 0,
            "commands": 1,
            "terminated": process.poll() is not None,
            "wait_mode": "blocking_subprocess_wait",
            "artifacts": [
                artifact(stdout_path, run_dir, "RAW_STDOUT"),
                artifact(stderr_path, run_dir, "RAW_STDERR"),
            ],
            "escalation": {"required": reason is not None, "reason": reason},
        }

    def _run_helper(self, run_dir: Path, request: dict[str, Any]) -> dict[str, Any]:
        if self.helper_transport is None:
            raise GearboxError("helper transport is unavailable; no fallback is permitted")
        staged = stage_context(run_dir, request)
        revalidate_sources(request)
        transport_request = {
            "schema": "opsle.gearbox.helper-request.v1",
            "task": request["task"],
            "task_type": request["task_type"],
            "gear": request["requested_gear"],
            "model": request["gear"]["model"],
            "effort": request["gear"]["effort"],
            "transport_id": request["gear"]["transport_id"],
            "context_manifest_sha256": staged.manifest_sha256,
            "output_contract": request["output_contract"],
            "budget": request["budget"],
        }
        try:
            transport = self.helper_transport.execute(
                request=transport_request,
                workspace=staged.workspace,
                timeout_seconds=request["budget"]["timeout_seconds"],
            )
        except Exception as exc:  # noqa: BLE001 - unknown transport failure must fail closed
            return {
                "status": "uncertain",
                "summary": f"helper transport failed with unknown execution state: {type(exc).__name__}",
                "exit_code": None,
                "changed_paths": [],
                "provider_sessions": None,
                "commands": None,
                "terminated": False,
                "wait_mode": "blocking_external_transport",
                "artifacts": [],
                "escalation": {"required": True, "reason": "TRANSPORT_STATE_UNKNOWN"},
                "provider_sessions_verified": False,
            }
        def failed_after_transport(message: str) -> dict[str, Any]:
            observed_sessions = (
                transport.get("provider_sessions")
                if isinstance(transport, dict)
                and isinstance(transport.get("provider_sessions"), int)
                and not isinstance(transport.get("provider_sessions"), bool)
                and transport.get("provider_sessions") >= 0
                else None
            )
            observed_commands = (
                transport.get("commands")
                if isinstance(transport, dict)
                and isinstance(transport.get("commands"), int)
                and not isinstance(transport.get("commands"), bool)
                and transport.get("commands") >= 0
                else None
            )
            raw_artifacts = [
                artifact(path, run_dir, f"RAW_{path.name.upper().replace('.', '_')}")
                for path in sorted((run_dir / "raw").glob("*"))
                if path.is_file()
            ]
            return {
                "status": "failed",
                "summary": message,
                "exit_code": None,
                "changed_paths": [],
                "provider_sessions": observed_sessions,
                "commands": observed_commands,
                "terminated": (
                    transport.get("terminated") is True
                    if isinstance(transport, dict)
                    else False
                ),
                "wait_mode": "blocking_external_transport",
                "artifacts": raw_artifacts,
                "escalation": {"required": True, "reason": "HELPER_FAIL_CLOSED"},
                "provider_sessions_verified": observed_sessions is not None,
            }
        required = {
            "final", "stdout", "stderr", "provider_sessions", "commands",
            "terminated", "model", "effort", "transport_id",
        }
        if not isinstance(transport, dict) or set(transport) != required:
            return failed_after_transport("helper transport result is malformed")
        if not isinstance(transport["stdout"], bytes) or not isinstance(transport["stderr"], bytes):
            return failed_after_transport("helper transport raw evidence must be bytes")
        stdout_path = run_dir / "raw/helper-stdout.raw"
        stderr_path = run_dir / "raw/helper-stderr.raw"
        atomic_write(stdout_path, transport["stdout"])
        atomic_write(stderr_path, transport["stderr"])
        raw_bytes = len(transport["stdout"]) + len(transport["stderr"])
        if raw_bytes > request["budget"]["max_raw_bytes"]:
            return failed_after_transport("helper exceeded raw evidence budget")
        expected_gear = request["gear"]
        if (
            transport["model"] != expected_gear["model"]
            or transport["effort"] != expected_gear["effort"]
            or transport["transport_id"] != expected_gear["transport_id"]
        ):
            return failed_after_transport("helper transport, model, or effort drifted")
        if not isinstance(transport["provider_sessions"], int) or not 0 <= transport["provider_sessions"] <= request["budget"]["max_provider_sessions"]:
            return failed_after_transport("helper exceeded provider-session budget")
        if not isinstance(transport["commands"], int) or not 0 <= transport["commands"] <= request["budget"]["max_commands"]:
            return failed_after_transport("helper exceeded command budget")
        if transport["terminated"] is not True:
            return failed_after_transport("helper termination is unverified")
        final = transport["final"]
        final_path = run_dir / "raw/helper-final.json"
        try:
            atomic_write(final_path, canonical_json(final))
            validate_json_value(final, request["output_contract"]["schema"])
            changed = workspace_changes(staged)
        except (GearboxError, TypeError, ValueError) as exc:
            return failed_after_transport(str(exc))
        if sorted(final.get("changed_paths", [])) != changed:
            return failed_after_transport(
                "helper changed-path claim does not match the staged workspace"
            )
        verdict = final.get("verdict") if isinstance(final, dict) else None
        status = {"PASS": "completed", "BLOCKED": "blocked", "FAIL": "failed"}.get(verdict, "uncertain")
        return {
            "status": status,
            "summary": final.get("summary", "helper returned") if isinstance(final, dict) else "helper returned",
            "exit_code": None,
            "changed_paths": changed,
            "provider_sessions": transport["provider_sessions"],
            "commands": transport["commands"],
            "terminated": True,
            "wait_mode": "blocking_external_transport",
            "artifacts": [
                artifact(stdout_path, run_dir, "RAW_HELPER_STDOUT"),
                artifact(stderr_path, run_dir, "RAW_HELPER_STDERR"),
                artifact(final_path, run_dir, "RAW_HELPER_FINAL"),
            ],
            "escalation": {
                "required": status != "completed",
                "reason": None if status == "completed" else "HELPER_TERMINAL_RESULT",
            },
            "context": {
                "source_bytes": staged.source_bytes,
                "packet_bytes": staged.packet_bytes,
                "manifest_sha256": staged.manifest_sha256,
            },
            "provider_sessions_verified": True,
        }

    def _finalize(
        self,
        run_dir: Path,
        run_id: str,
        request: dict[str, Any],
        partial: dict[str, Any],
    ) -> dict[str, Any]:
        result = {
            "schema": RESULT_SCHEMA,
            "run_id": run_id,
            "request_sha256": sha256_file(run_dir / "request.json"),
            "authority_id": request["authority"]["authority_id"],
            "policy_sha256": request["authority"]["policy_sha256"],
            "gear": request["requested_gear"],
            "gear_kind": request["gear"]["kind"],
            "status": partial["status"],
            "summary": partial["summary"][:1200],
            "exit_code": partial["exit_code"],
            "changed_paths": partial["changed_paths"],
            "artifacts": partial["artifacts"],
            "metrics": {
                "execution_attempts": 1,
                "fallback_attempts": 0,
                "provider_sessions": partial["provider_sessions"],
                "commands": partial["commands"],
                "helper_terminated": partial["terminated"],
                "primary_wait_mode": partial["wait_mode"],
                "raw_evidence_bytes": sum(item["bytes"] for item in partial["artifacts"]),
                "provider_sessions_verified": partial.get("provider_sessions_verified", True),
            },
            "escalation": partial["escalation"],
            "context": partial.get("context"),
            "value_receipt": f"runs/{run_id}/value-receipt.json",
        }
        result["result_sha256"] = sha256_bytes(canonical_json(result))
        return result

    def _value_receipt(
        self, result: dict[str, Any], compact_bytes: int, policy_hash: str
    ) -> dict[str, Any]:
        def measurement(
            identity: str,
            result_value: Any,
            unit: str,
            quality: str,
            *,
            operator: bool,
            safe_sum: bool,
            direction: str = "NEUTRAL",
        ) -> dict[str, Any]:
            return {
                "id": identity,
                "baseline": None,
                "result": result_value,
                "delta": None,
                "unit": unit,
                "direction": direction,
                "class": quality,
                "source_verification": "VERIFIED" if quality == "EXACT" else "OBSERVED",
                "evidence_refs": ["compact_result"],
                "operator_display": operator,
                "aggregation": {"safe": safe_sum, "method": "SUM" if safe_sum else None},
                "derivation": None,
                "limitations": [],
            }

        metrics = result["metrics"]
        provider_sessions = metrics["provider_sessions"]
        provider_quality = "EXACT" if metrics["provider_sessions_verified"] else "OBSERVED"
        return {
            "schema": VALUE_SCHEMA,
            "mechanism": {
                "id": "opsle.gearbox",
                "name": "Agent Gearbox",
                "version": VERSION,
                "revision": self.mechanism_revision,
            },
            "operation": {
                "id": result["run_id"],
                "name": "bounded-gear-execution",
                "configuration_id": f"sha256:{policy_hash}",
                "policy_id": result["authority_id"],
            },
            "run": {"id": result["run_id"]},
            "measurements": [
                measurement("execution_attempts", 1, "count", "EXACT", operator=False, safe_sum=True),
                measurement("fallback_attempts", 0, "count", "EXACT", operator=False, safe_sum=True),
                measurement(
                    "provider_sessions",
                    provider_sessions,
                    "count",
                    provider_quality,
                    operator=True,
                    safe_sum=provider_quality == "EXACT" and isinstance(provider_sessions, int),
                ),
                measurement("raw_evidence_bytes", metrics["raw_evidence_bytes"], "byte", "EXACT", operator=False, safe_sum=True),
                measurement("model_visible_result_bytes", compact_bytes, "byte", "EXACT", operator=True, safe_sum=True),
                measurement("terminal_status", result["status"], "state", "OBSERVED", operator=True, safe_sum=False, direction="PROTECTION_SIGNAL"),
                measurement("primary_wait_mode", metrics["primary_wait_mode"], "state", "OBSERVED", operator=False, safe_sum=False),
            ],
            "evidence": [
                {
                    "id": "compact_result",
                    "kind": "CONTENT_HASH",
                    "locator": f"sha256:{result['result_sha256']}",
                    "trust": "VERIFIED",
                }
            ],
            "limitations": [
                "No token, cost, latency, correctness, or causal savings claim is made.",
                "A zero provider-session count is observed execution telemetry, not proof that a provider session would otherwise have occurred.",
                "Helper isolation and provider accounting depend on the separately supplied transport.",
                "Context Firewall reduction is an external integration and was not performed by this core run.",
            ],
        }


def operator_indicator(result: dict[str, Any], receipt: dict[str, Any]) -> str:
    measurements = {item["id"]: item["result"] for item in receipt["measurements"]}
    return (
        f"[Gearbox] {result['gear_kind']} {result['status']} | "
        f"provider_sessions={measurements['provider_sessions']} | "
        f"raw={measurements['raw_evidence_bytes']} B | "
        f"return={measurements['model_visible_result_bytes']} B"
    )
