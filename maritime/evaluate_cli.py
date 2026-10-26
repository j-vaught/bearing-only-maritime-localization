"""Standalone evaluator joining a search estimate with hidden truth."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from maritime.evaluation.metrics import evaluate_estimate
from maritime.schemas import EvaluationTruth, PositionEstimate
from maritime.serialization import read_contract


def evaluate_files(
    estimate_path: str | Path,
    truth_path: str | Path,
    *,
    success_position_threshold_m: float = 250.0,
    success_min_correct_associations: int = 3,
) -> dict[str, object]:
    """Load evaluator inputs and return metrics; truth is confined to this API."""
    if Path(estimate_path).resolve() == Path(truth_path).resolve():
        raise ValueError("Estimate and truth must be stored in separate contract files.")
    estimate = read_contract(estimate_path, PositionEstimate)
    truth = read_contract(truth_path, EvaluationTruth)
    metrics = evaluate_estimate(
        estimate,
        truth,
        success_position_threshold_m=success_position_threshold_m,
        success_min_correct_associations=success_min_correct_associations,
    )
    return asdict(metrics)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--estimate", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--success-position-threshold-m", type=float, default=250.0)
    parser.add_argument("--success-min-correct-associations", type=int, default=3)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--schema-version" in arguments:
        marker = arguments.index("--schema-version")
        if marker + 1 >= len(arguments) or arguments[marker + 1] != "2":
            raise SystemExit("--schema-version must be 2 when specified.")
        v2_arguments = arguments[:marker] + arguments[marker + 2 :]
        from maritime.accuracy_cli import evaluate_v2_main

        return evaluate_v2_main(v2_arguments)
    parser = build_parser()
    args = parser.parse_args(argv)
    output_path = args.output.expanduser().resolve()
    if output_path in {
        args.estimate.expanduser().resolve(),
        args.truth.expanduser().resolve(),
    }:
        parser.error("evaluation output must be separate from estimate and truth inputs")
    try:
        metrics = evaluate_files(
            args.estimate,
            args.truth,
            success_position_threshold_m=args.success_position_threshold_m,
            success_min_correct_associations=args.success_min_correct_associations,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        document = json.dumps(metrics, indent=2, sort_keys=True) + "\n"
        args.output.write_text(document, encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(metrics, sort_keys=True))
    return 0


def _entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    _entrypoint()
