#!/usr/bin/env python3
"""Does the analysis tag food groups right? Labelled fake meals through the bot's
own call and parser (stage 5, slice B). Needs a real OPENAI_API_KEY; paid.

    python scripts/eval_food_groups.py --one     # one call: the price
    python scripts/eval_food_groups.py           # the whole set

"must" is what a reply has to name; "maybe" is arguable and not scored either way.
Meals here are invented: a personal set stays out of the repo, passed by --set.
"""

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from app.services import food_groups, usage_meter
from app.services.openai_service import get_openai_service
from app.services.product_lookup_service import parse_nutrition

DEFAULT_SET = Path(__file__).parents[1] / "tests/fixtures/food_groups_eval.json"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--one", action="store_true")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET)
    args = parser.parse_args()
    meals = json.loads(args.set.read_text(encoding="utf-8"))
    if args.one:
        meals = meals[:1]

    service = get_openai_service()
    hit, missed, extra, failed = Counter(), Counter(), Counter(), 0
    with usage_meter.metering() as meter:
        for meal in meals:
            try:
                reply = parse_nutrition(await service.analyze_food_entry(meal["text"]))
            except Exception as e:
                failed += 1
                print(f"FAILED  {meal['text']}: {type(e).__name__}")
                continue
            got = set(food_groups.from_items(reply.get("food_items")))
            must, maybe = set(meal["must"]), set(meal["maybe"])
            hit.update(got & must)
            missed.update(must - got)
            extra.update(got - must - maybe)
            if must - got or got - must - maybe:
                print(
                    f"{meal['text']}: missed {sorted(must - got)}, "
                    f"extra {sorted(got - must - maybe)}"
                )

    print(
        f"\n{len(meals)} meals, {failed} failed, ${meter.usd:.4f}", meter.unpriced or ""
    )
    for key in food_groups.GROUPS:
        print(f"{key:8} hit {hit[key]:3}  missed {missed[key]:3}  extra {extra[key]:3}")


if __name__ == "__main__":
    asyncio.run(main())
