"""Command-line adapter for :mod:`nhr9300.execution`."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .execution import WorkflowRequest, execute_workflow


WRITE_ACK = "SUPERVISED_WORKFLOW_READY"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a validated NHR workflow.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--simulate", action="store_true")
    mode.add_argument("--hardware", action="store_true")
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--resource", default=os.environ.get("NHR9300_RESOURCE"))
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("workflow-results"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    acknowledged = os.environ.get("NHR9300_WORKFLOW_ACK") == WRITE_ACK
    try:
        outcome = execute_workflow(
            WorkflowRequest(
                profile=args.profile,
                output=args.output,
                hardware=bool(args.hardware),
                resource=args.resource,
                preflight_only=bool(args.preflight_only),
                acknowledged=acknowledged,
            )
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Workflow report: {outcome.report_path}")
    print(f"Result: {'PASS' if outcome.passed else 'FAIL'}")
    return 0 if outcome.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
