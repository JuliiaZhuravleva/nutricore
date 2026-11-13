#!/usr/bin/env python3
"""What the 🍲 tidy-up would do, from a CSV of dishes: read-only, no database, no model.

    python scripts/tidy_dry_run.py dishes.csv [--near near.csv] [--today 2026-11-15]

``dishes.csv`` columns: id, name, kcal, by_user, grams, items, days, starred,
star_removed. ``days`` is the local days eaten in the window, ``;``-separated ISO dates;
``by_user``/``starred``/``star_removed`` are t/f. ``near.csv``: one, other, distance
(stored embeddings, as ``crud_personal_food.close_pairs`` reads them). The same pure
``dish_tidy.plan`` the weekly pass runs, with no declines (a first Sunday).

Prints the dish names: run it on your own data only.
"""

import argparse
import csv
import sys
from datetime import UTC, date, datetime


def _bool(raw: str) -> bool:
    return raw.strip().lower() in ("t", "true", "1", "yes")


def _float(raw: str):
    return float(raw) if raw.strip() else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dishes")
    parser.add_argument("--near")
    parser.add_argument("--today", default=date.today().isoformat())
    args = parser.parse_args()

    from app.services import dish_tidy  # placeholder env is enough: no DB is opened

    today = date.fromisoformat(args.today)
    dishes = {}
    with open(args.dishes, newline="") as handle:
        for row in csv.DictReader(handle):
            dish = dish_tidy.Dish(
                id=int(row["id"]),
                name=row["name"],
                kcal=_float(row.get("kcal", "")),
                by_user=_bool(row.get("by_user", "")),
                grams=_float(row.get("grams", "")),
                items=int(row.get("items") or 0),
                days=frozenset(
                    date.fromisoformat(d)
                    for d in (row.get("days") or "").split(";")
                    if d
                ),
                starred=_bool(row.get("starred", "")),
                star_removed=_bool(row.get("star_removed", "")),
            )
            dishes[dish.id] = dish
    near = {}
    if args.near:
        with open(args.near, newline="") as handle:
            for row in csv.DictReader(handle):
                one, other = sorted((int(row["one"]), int(row["other"])))
                if one in dishes and other in dishes:
                    near[(one, other)] = float(row["distance"])
    now = datetime.now(UTC)
    found = dish_tidy.plan(list(dishes.values()), near, {}, now, today=today)
    heads = {
        dish_tidy.SAME: "Объединится само:",
        dish_tidy.MERGE: "Будет предложено объединить:",
        dish_tidy.STAR: "Будет предложено в ⭐:",
    }
    shown = None
    for n, row in enumerate(found.rows, 1):
        if row.kind != shown:
            shown = row.kind
            print(f"\n{heads[row.kind]}")
        mark = "✓" if row.ticked else " "
        if row.kind == dish_tidy.STAR:
            print(f" {mark} {n}. {row.names[0]} — {row.days} дн.")
        else:
            close = near.get(tuple(sorted((row.source_id, row.target_id))))
            via = f"  [embedding {close:.3f}]" if close is not None else ""
            print(f" {mark} {n}. «{row.names[0]}» → «{row.names[1]}»{via}")
    if found.empty:
        print("Ничего: одинаковых и похожих нет.")
    print(f"\n{len(dishes)} блюд, {len(found.rows)} строк.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
