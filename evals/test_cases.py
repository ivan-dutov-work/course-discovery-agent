from __future__ import annotations

import pytest

from evals.cases import Case, load_cases
from evals.run import LEVELS, run_case, skipped

CASES = [pytest.param(case, id=case.id) for level in LEVELS for case in load_cases(level)]


@pytest.mark.parametrize("case", CASES)
def test_case(case: Case) -> None:
    reason = skipped(case)
    if reason:
        pytest.skip(reason)
    failed = [f"[{v.grader}] {v.reason}" for v in run_case(case).verdicts if not v.passed]
    assert not failed, "; ".join(failed)
