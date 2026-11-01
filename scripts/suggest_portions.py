#!/usr/bin/env python3
"""A guessed portion for the dishes that have numbers and no weight. Run inside the app
container.

    python scripts/suggest_portions.py                  # no call: the count and the price
    python scripts/suggest_portions.py --apply          # ask, if under $0.20
    python scripts/suggest_portions.py --apply --limit 50
    python scripts/suggest_portions.py --apply --limit-usd 0.5   # owner agreed

Each guess goes on the row it was asked about. Prints counts and sums only, never a dish.
"""

import argparse
import sys

from app.db.session import SessionLocal
from app.services.openai_service import get_openai_service
from app.services.portion_suggestions import LIMIT_DISHES, LIMIT_USD, run_sync


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=LIMIT_DISHES)
    parser.add_argument("--limit-usd", type=float, default=LIMIT_USD)
    args = parser.parse_args()
    report = run_sync(
        SessionLocal,
        get_openai_service(),
        apply=args.apply,
        limit=args.limit,
        limit_usd=args.limit_usd,
    )
    print(report)
    if report.stopped or report.failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
