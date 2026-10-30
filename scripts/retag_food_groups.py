#!/usr/bin/env python3
"""Tag past meals with food groups (stage 5, slice B). Run inside the app container.

    python scripts/retag_food_groups.py                 # one paid call: the price
    python scripts/retag_food_groups.py --apply         # tag all, if under $0.20
    python scripts/retag_food_groups.py --apply --limit-usd 0.5   # owner agreed

Prints counts and sums only, never a meal.
"""

import argparse
import sys

from app.db.session import SessionLocal
from app.services.food_groups_retag import LIMIT_USD, run_sync
from app.services.openai_service import get_openai_service


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit-usd", type=float, default=LIMIT_USD)
    args = parser.parse_args()
    with SessionLocal() as db:
        report = run_sync(
            db, get_openai_service(), apply=args.apply, limit_usd=args.limit_usd
        )
    print(report)
    if report.stopped or report.failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
