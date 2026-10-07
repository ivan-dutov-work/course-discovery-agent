from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from course_discovery.conversation.selector import CONTINUE_THRESHOLD, NEW_TOPIC_THRESHOLD, NO_SIGNAL_THRESHOLD

LABELS = Path(__file__).parent / "labels"
BASELINES = Path(__file__).parent / "baselines"
SCORES = BASELINES / "thread_selector_scores.json"
BANDS = BASELINES / "thread_selector_bands.json"
SPLITS = ("train", "dev", "test")
TUNING_SPLITS = ("train", "dev")
CLASSES = ("continue", "new_topic", "no_signal")


@dataclass(frozen=True)
class Rate:
    hits: int
    total: int

    def __str__(self) -> str:
        return "n/a" if not self.total else f"{self.hits / self.total:.2f} ({self.hits}/{self.total})"


def load_pairs(source: str) -> list[dict]:
    pairs = yaml.safe_load((LABELS / "thread_pairs.yaml").read_text())
    if source == "intended":
        intended = {row["id"]: row["label"] for row in yaml.safe_load((LABELS / "thread_pairs.intended.yaml").read_text())}
        return [{**pair, "label": intended[pair["id"]]} for pair in pairs if pair["id"] in intended]
    missing = [pair["id"] for pair in pairs if pair["label"] is None]
    if missing:
        raise SystemExit(f"{len(missing)} pairs have no label yet: {', '.join(missing[:5])}...")
    return pairs


Scores = dict[str, dict[str, float | None]]


def decide(score: dict[str, float | None] | None, continue_threshold: float) -> str:
    score = score or {}
    signal, follows = score.get("signal"), score.get("continue")
    if signal is not None and signal >= NO_SIGNAL_THRESHOLD:
        return "no_signal"
    return "continue" if follows is not None and follows >= continue_threshold else "new_topic"


def rates(pairs: list[dict], scores: Scores, continue_threshold: float) -> dict[str, dict[str, Rate]]:
    result = {}
    for positive in CLASSES:
        tp = fn = tn = fp = 0
        for pair in pairs:
            predicted = decide(scores.get(pair["id"]), continue_threshold)
            if pair["label"] == positive:
                tp += predicted == positive
                fn += predicted != positive
            else:
                tn += predicted != positive
                fp += predicted == positive
        result[positive] = {"tpr": Rate(tp, tp + fn), "tnr": Rate(tn, tn + fp)}
    return result


def fit_threshold(pairs: list[dict], scores: Scores) -> float:
    if any(pair["split"] not in TUNING_SPLITS for pair in pairs):
        raise SystemExit("fitting may only use the train and dev splits")
    best, best_value = CONTINUE_THRESHOLD, -1.0
    for step in range(int(NEW_TOPIC_THRESHOLD * 100) + 5, 100, 5):
        threshold = step / 100
        by_class = rates(pairs, scores, threshold)["continue"]
        parts = [r.hits / r.total for r in by_class.values() if r.total]
        value = sum(parts) / len(parts) if parts else 0.0
        if value >= best_value:
            best, best_value = threshold, value
    return best


def check_no_leak(tuned_on: list[str], pairs: list[dict]) -> None:
    test_ids = {pair["id"] for pair in pairs if pair["split"] == "test"}
    leaked = sorted(test_ids & set(tuned_on))
    if leaked:
        raise SystemExit(f"test-split ids appear in the tuning inputs: {', '.join(leaked[:5])}")


def render(pairs: list[dict], scores: Scores, continue_threshold: float, source: str) -> str:
    lines = [f"labels: {source}   continue threshold: {continue_threshold:.2f}"]
    for split in (*SPLITS, "all"):
        subset = [pair for pair in pairs if split == "all" or pair["split"] == split]
        counts = {c: sum(pair["label"] == c for pair in subset) for c in CLASSES}
        lines.append(f"\n{split}: {len(subset)} pairs (" + ", ".join(f"{counts[c]} {c}" for c in CLASSES) + ")")
        lines.append(f"  {'positive class':<16}{'TPR':<14}TNR")
        for positive, row in rates(subset, scores, continue_threshold).items():
            lines.append(f"  {positive:<16}{str(row['tpr']):<14}{row['tnr']}")
    return "\n".join(lines)


def collect_scores(pairs: list[dict]) -> Scores:
    from course_discovery.conversation import JevThreadSelector, ParkedThread

    selector = JevThreadSelector()
    scores: Scores = {}
    for pair in pairs:
        parked = pair["parked"]
        thread = ParkedThread(parked["topic"], parked.get("filters", {}), parked["age_hours"] * 3600)
        scores[pair["id"]] = {}
        for name, ask in (("signal", selector.signal_score), ("continue", selector.score)):
            try:
                scores[pair["id"]][name] = ask(pair["message"], thread)
            except Exception as exc:  # noqa: BLE001
                print(f"{pair['id']} {name}: {type(exc).__name__}", file=sys.stderr)
                scores[pair["id"]][name] = None
    return scores


def load_bands() -> dict:
    if BANDS.exists():
        return json.loads(BANDS.read_text())
    return {"continue_threshold": CONTINUE_THRESHOLD, "tuned_on": []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.calibrate_thread_selector")
    parser.add_argument("--labels", choices=("owner", "intended"), default="owner")
    parser.add_argument("--rescore", action="store_true", help="reuse stored scores, no model calls")
    parser.add_argument("--splits", help="comma-separated splits to score live; other stored scores are kept")
    parser.add_argument("--fit", action="store_true", help="fit the continue threshold on train and dev")
    args = parser.parse_args(argv)
    pairs = load_pairs(args.labels)
    if args.rescore:
        scores = json.loads(SCORES.read_text())["scores"]
    elif args.splits:
        wanted = set(args.splits.split(","))
        if not wanted <= set(SPLITS):
            raise SystemExit(f"unknown splits: {', '.join(sorted(wanted - set(SPLITS)))}")
        stored = json.loads(SCORES.read_text())
        scores = {**stored["scores"], **collect_scores([pair for pair in pairs if pair["split"] in wanted])}
        SCORES.write_text(json.dumps({**stored, "scores": scores}, indent=2))
    else:
        from course_discovery.conversation.selector import DEFAULT_MODEL

        scores = collect_scores(pairs)
        SCORES.write_text(json.dumps({"model": DEFAULT_MODEL, "date": date.today().isoformat(), "scores": scores}, indent=2))
    bands = load_bands()
    if args.fit:
        tuning = [pair for pair in pairs if pair["split"] in TUNING_SPLITS]
        bands = {"continue_threshold": fit_threshold(tuning, scores), "tuned_on": [pair["id"] for pair in tuning]}
        BANDS.write_text(json.dumps(bands, indent=2))
    check_no_leak(bands["tuned_on"], pairs)
    print(render(pairs, scores, bands["continue_threshold"], args.labels))
    return 0


if __name__ == "__main__":
    sys.exit(main())
