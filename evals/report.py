from __future__ import annotations

from dataclasses import dataclass

from evals.graders import Verdict


@dataclass(frozen=True)
class Result:
    case_id: str
    level: str
    verdicts: list[Verdict]

    @property
    def passed(self) -> bool:
        return all(v.passed for v in self.verdicts)


def render(results: list[Result]) -> str:
    lines = []
    for level in sorted({r.level for r in results}):
        subset = [r for r in results if r.level == level]
        passed = sum(r.passed for r in subset)
        lines.append(f"{level}: {passed}/{len(subset)} passed")
        for result in subset:
            for verdict in result.verdicts:
                if not verdict.passed:
                    lines.append(f"  FAIL {result.case_id} [{verdict.grader}] {verdict.reason}")
    return "\n".join(lines)
