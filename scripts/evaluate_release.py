"""Fail a release using a schema-2 evaluation artifact and the labelled dataset."""
import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.evaluation.release import verify_json_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--index-revision")
    parser.add_argument("--require-production-model", action="store_true", help="Reject demo/mock/unpinned embedding evidence.")
    parser.add_argument("--minimum-recall", type=float, default=.8)
    parser.add_argument("--minimum-citation-precision", type=float, default=.85)
    args = parser.parse_args(argv)
    try:
        gate, summary = asyncio.run(verify_json_report(args.report, args.dataset,
            index_revision=args.index_revision, minimum_recall=args.minimum_recall,
            minimum_citation_precision=args.minimum_citation_precision, require_production_model=args.require_production_model))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        print(f"release verification error: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"release_gate": asdict(gate), "recomputed_summary": asdict(summary)}, allow_nan=False))
    return int(not gate.passed)


if __name__ == "__main__":
    raise SystemExit(main())
