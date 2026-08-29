"""Command-line entrypoint with canonical stdout and operator-only stderr."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .core import (
    GearboxError,
    GearboxRunner,
    canonical_json,
    operator_indicator,
    read_object,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--mechanism-revision")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        request = read_object(args.request)
        runner = GearboxRunner(args.state, mechanism_revision=args.mechanism_revision)
        result = runner.run(request)
        run_dir = args.state.resolve() / "runs" / result["run_id"]
        receipt_path = run_dir / "value-receipt.json"
        receipt = read_object(receipt_path)
        if args.receipt:
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(receipt_path, args.receipt)
        sys.stdout.buffer.write(canonical_json(result))
        print(operator_indicator(result, receipt), file=sys.stderr)
        return 0 if result["status"] == "completed" else 2
    except (GearboxError, OSError, json.JSONDecodeError) as exc:
        sys.stdout.buffer.write(canonical_json({
            "schema": "opsle.gearbox.error.v1",
            "status": "failed",
            "error": str(exc),
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
