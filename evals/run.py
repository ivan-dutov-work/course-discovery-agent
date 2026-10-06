from __future__ import annotations

import argparse
import os
import sys

from evals.cases import Case, load_cases
from evals.graders import grade
from evals.report import Result, render
from evals.runners import RUNNERS

LEVELS = ("l1a", "l3")


def skipped(case: Case) -> str | None:
    if "postgres" in case.requires and not os.getenv("TEST_DATABASE_URL"):
        return "TEST_DATABASE_URL not set"
    return None


def run_case(case: Case) -> Result:
    return Result(case.id, case.level, grade(case, RUNNERS[case.level](case)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.run")
    parser.add_argument("--level", action="append", choices=LEVELS)
    parser.add_argument("--case", help="run one case id")
    args = parser.parse_args(argv)
    results = []
    for level in args.level or LEVELS:
        for case in load_cases(level):
            if args.case and case.id != args.case:
                continue
            if skipped(case) is None:
                results.append(run_case(case))
    print(render(results))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
