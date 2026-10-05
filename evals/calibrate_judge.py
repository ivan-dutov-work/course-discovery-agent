from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from tests.judge import CRITERIA

LABELS = Path(__file__).parent / "labels"
BASELINES = Path(__file__).parent / "baselines"


@dataclass(frozen=True)
class Rate:
    hits: int
    total: int

    @property
    def value(self) -> float | None:
        return self.hits / self.total if self.total else None

    def __str__(self) -> str:
        return "n/a" if self.value is None else f"{self.value:.2f} ({self.hits}/{self.total})"


def load_labels(source: str) -> dict[str, list[str]]:
    name = "judge_notes.intended.yaml" if source == "intended" else "judge_notes.yaml"
    rows = yaml.safe_load((LABELS / name).read_text())
    missing = [row["id"] for row in rows if row["fails"] is None]
    if missing:
        raise SystemExit(f"{len(missing)} items have no label yet: {', '.join(missing[:5])}...")
    return {row["id"]: row["fails"] for row in rows}


def load_items() -> list[dict]:
    return yaml.safe_load((LABELS / "judge_notes.yaml").read_text())


def score(labels: dict[str, list[str]], verdicts: dict[str, dict[str, str]]) -> dict[str, dict]:
    report = {}
    for criterion in CRITERIA:
        tp = fn = tn = fp = unknown = 0
        for item_id, fails in labels.items():
            verdict = verdicts[item_id][criterion]
            unknown += verdict == "unknown"
            if criterion in fails:
                tp += verdict == "fail"
                fn += verdict != "fail"
            else:
                tn += verdict == "pass"
                fp += verdict != "pass"
        report[criterion] = {
            "tpr": Rate(tp, tp + fn),
            "tnr": Rate(tn, tn + fp),
            "unknown": unknown,
        }
    return report


def render(report: dict[str, dict], source: str) -> str:
    lines = [f"labels: {source}", f"{'criterion':<14}{'TPR':<14}{'TNR':<14}unknown"]
    for criterion, row in report.items():
        lines.append(f"{criterion:<14}{str(row['tpr']):<14}{str(row['tnr']):<14}{row['unknown']}")
    return "\n".join(lines)


def run_judge(items: list[dict]) -> dict[str, dict]:
    from tests.judge import judge_notes

    results = {}
    for item in items:
        result = judge_notes(item["feedback"], item["notes_before"], item["notes_after"])
        results[item["id"]] = {"verdicts": result.verdicts, "reasons": result.reasons}
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.calibrate_judge")
    parser.add_argument("--labels", choices=("owner", "intended"), default="owner")
    parser.add_argument("--rescore", action="store_true", help="reuse stored verdicts, no model calls")
    args = parser.parse_args(argv)
    labels = load_labels(args.labels)
    stored = BASELINES / "judge_calibration.json"
    if args.rescore:
        results = json.loads(stored.read_text())["results"]
    else:
        from course_discovery.app.llm import JUDGE_MODEL

        results = run_judge(load_items())
        stored.write_text(
            json.dumps({"judge_model": JUDGE_MODEL, "date": date.today().isoformat(), "results": results}, indent=2)
        )
    report = score(labels, {item_id: row["verdicts"] for item_id, row in results.items()})
    print(render(report, args.labels))
    return 0


if __name__ == "__main__":
    sys.exit(main())
