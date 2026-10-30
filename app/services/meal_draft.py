"""The meal draft at the confirm step: its numbers, its portion, its buttons.

Pure — no Telegram I/O, no DB. ``telegram.py`` keeps the draft in
``user_data["current_meal"]`` and calls into here to render it, to apply a tap
on one of its inline buttons, and to read a typed weight.

The draft remembers the analysis as it came (``base_nutrition`` at
``base_grams``) and every portion change rescales from that base, never from
the previous change, so tapping sizes back and forth cannot drift the numbers.

Calories and macros follow «🔢 Цифры» (stage 5, G5-R02): hidden unless ⚙️ shows
them, and a tap on the draft's own «🔢 Цифры» opens them for this draft. The
portion and its buttons stay either way — they are how a log is corrected.
"""

import re
import secrets
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.services import food_groups
from app.services.numbers import toggle_label

CALLBACK_PREFIX = "meal:"

MACROS = ("calories", "protein", "fats", "carbs")

# Size chips, relative to the analysis' own estimate (= "Средняя"). The photo
# estimate is the main source of error (the samosa: 250 ккал for ~140 г), so the
# chips span roughly what a person can tell apart by eye.
SIZES = {"s": ("Маленькая", 0.7), "m": ("Средняя", 1.0), "l": ("Большая", 1.4)}
# What a chip says before its grams: Telegram cuts about 12 characters at three
# buttons a row, and «Маленькая 245г» came out as «Маленькая 2…».
CHIP_WORDS = {"s": "Меньше ", "m": "", "l": "Больше "}

# A photo estimate is off by ±30–40%; the shown range is narrower on purpose —
# wide enough to read as an estimate, narrow enough to still mean something.
ESTIMATE_SPREAD = 0.2

MIN_GRAMS, MAX_GRAMS = 1.0, 5000.0

SAVE = "s"
SAVE_FAVORITE = "sf"
ASK_GRAMS = "w"
ASK_FOOD = "f"
CANCEL = "x"
SIZE = "p"
NUMBERS = "n"
GROUPS_OPEN = "g"
GROUP_TOGGLE = "gt"
GROUPS_CLOSE = "gb"

_GRAMS_RE = re.compile(
    r"^\s*(?:≈|~)?\s*(\d+(?:[.,]\d+)?)\s*"
    r"(кг|kg|г|гр|грамм[а-я]*|g|gr|grams?)?\.?\s*$",
    re.IGNORECASE,
)


GROUPS_TEXT = (
    "🏷 Группы в этой записи\n\n"
    "Отметь, что было на тарелке. Это видно только на карточках дня и недели."
)


def new_draft_id() -> str:
    """A short id stamped into every button, so a tap on an older draft's
    message is recognised as stale instead of acting on the current draft."""
    return secrets.token_hex(3)


def num(value, digits: int = 1) -> str:
    """A number without a trailing ".0": 250.0 → "250", 22.24 → "22.2"."""
    text = f"{round(float(value), digits):.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _round_grams(grams: float) -> float:
    step = 5 if grams < 100 else 10
    return float(max(step, round(grams / step) * step))


def calories_text(calories, *, estimate: bool) -> str:
    """ "≈ 400 ккал (320–480)" for an estimate, "400 ккал" otherwise."""
    if calories is None:
        return "—"
    try:
        kcal = float(calories)
    except (TypeError, ValueError):
        return str(calories)
    if not estimate or kcal <= 0:
        return f"{num(kcal, 0)} ккал"
    lo = round(kcal * (1 - ESTIMATE_SPREAD) / 10) * 10
    hi = round(kcal * (1 + ESTIMATE_SPREAD) / 10) * 10
    return f"≈ {num(kcal, 0)} ккал ({lo}–{hi})"


def macro_text(value, *, estimate: bool) -> str:
    if value is None:
        return "—"
    try:
        return f"{num(value, 0 if estimate else 1)}г"
    except (TypeError, ValueError):
        return str(value)


def parse_grams(text: Optional[str], *, unit_required: bool) -> Optional[float]:
    """Grams from a typed weight ("150", "150 г", "0,5 кг"), else None.

    ``unit_required`` is for a reply nobody asked to be a weight: a bare "2"
    there may mean two pieces, so only "200 г" counts.
    """
    match = _GRAMS_RE.match(text or "")
    if not match:
        return None
    unit = (match.group(2) or "").lower()
    if unit_required and not unit:
        return None
    grams = float(match.group(1).replace(",", "."))
    if unit in ("кг", "kg"):
        grams *= 1000
    if not MIN_GRAMS <= grams <= MAX_GRAMS:
        return None
    return grams


def start(
    draft: dict,
    *,
    header: str,
    badge: str,
    detail_lines: list,
    estimate: bool,
    number_notes: tuple = (),
    base_grams: Optional[float],
    per_100g: bool = False,
    chips: bool = False,
    basis: Optional[str] = None,
    numbers: bool = False,
) -> dict:
    """Stamp the view state onto a fresh draft (its nutrition already set).

    ``numbers`` is the ⚙️ setting: True shows calories and macros with no button.
    ``number_notes`` are caveats about those numbers; they show only with them.
    """
    nutrition = draft["nutrition"]
    draft.update(
        id=new_draft_id(),
        header=header,
        badge=badge,
        detail_lines=list(detail_lines),
        number_notes=list(number_notes),
        estimate=estimate,
        base_grams=base_grams if base_grams and base_grams > 0 else None,
        base_nutrition=dict(nutrition),
        base_signals=draft.get("resolution_signals"),
        per_100g=per_100g,
        chips=bool(chips and base_grams),
        basis=basis,
        size="m" if chips and base_grams else None,
        grams_by_user=False,
        awaiting=None,
        numbers=numbers,
        numbers_setting=numbers,
        groups=food_groups.from_items(nutrition.get("food_items")),
        groups_by_user=False,
        groups_open=False,
    )
    return draft


def toggle_group(draft: dict, key: str) -> bool:
    """A tap on one group in the groups screen. False if there is no such group."""
    if key not in food_groups.GROUPS:
        return False
    chosen = set(draft.get("groups") or [])
    chosen.symmetric_difference_update({key})
    draft["groups"] = food_groups.ordered(chosen)
    draft["groups_by_user"] = True
    return True


def stored_analysis(draft: dict) -> dict:
    """What a saved meal keeps: the analysis as it came, with the confirmed groups."""
    return food_groups.stamp(
        draft.get("base_nutrition") or draft.get("nutrition") or {},
        draft.get("groups") or [],
        by_user=bool(draft.get("groups_by_user")),
    )


def stored_nutrients(draft: dict) -> dict:
    """The numbers on screen, with the confirmed groups (a favorite copies these)."""
    return food_groups.stamp(
        draft.get("nutrition") or {},
        draft.get("groups") or [],
        by_user=bool(draft.get("groups_by_user")),
    )


def toggle_numbers(draft: dict) -> bool:
    """The draft's «🔢 Цифры»: open or close them. False if ⚙️ shows them anyway."""
    if draft.get("numbers_setting"):
        return False
    draft["numbers"] = not draft.get("numbers")
    return True


def can_rescale(draft: dict) -> bool:
    return bool(draft.get("base_grams"))


def set_size(draft: dict, size: str) -> bool:
    """Apply a size chip. False if the chip does not apply to this draft."""
    if size not in SIZES or not draft.get("chips") or not can_rescale(draft):
        return False
    if size == "m":
        # Back to the analysis as it came — its own portion text included.
        draft["nutrition"] = dict(draft["base_nutrition"])
        draft["resolution_signals"] = draft.get("base_signals")
        draft["grams_by_user"] = False
        draft["size"] = "m"
        return True
    set_grams(draft, chip_grams(draft, size))
    draft["size"] = size
    return True


def chip_grams(draft: dict, size: str) -> float:
    base = draft["base_grams"]
    return base if size == "m" else _round_grams(base * SIZES[size][1])


def set_grams(draft: dict, grams: float) -> None:
    """Rescale every macro to ``grams`` and record the weight as the user's.

    ``portion_grams`` moves with the numbers, in the nutrition AND in the
    resolution signals: the personal food DB divides the macros by it to get
    per-100g values, so a stale gram basis would teach it a wrong food.
    """
    base = draft["base_grams"]
    factor = grams / base
    nutrition = dict(draft["base_nutrition"])
    for key in MACROS:
        value = nutrition.get(key)
        if value is None:
            continue
        try:
            nutrition[key] = round(float(value) * factor, 1)
        except (TypeError, ValueError):
            pass
    nutrition["portion"] = f"{num(grams, 0)}г"
    nutrition["portion_grams"] = grams
    draft["nutrition"] = nutrition
    signals = draft.get("base_signals")
    if signals is not None:
        draft["resolution_signals"] = {
            **signals,
            "portion_grams": grams,
            "portion_source": "user",
            "portion_grams_estimated": base,
        }
    draft["grams_by_user"] = True
    draft["size"] = None
    draft["groups_open"] = False  # a typed weight redraws the draft itself


def _portion_line(draft: dict) -> str:
    nutrition = draft["nutrition"]
    if draft.get("grams_by_user"):
        grams = num(nutrition["portion_grams"], 0)
        if draft.get("size") in SIZES:
            return f"Порция: ≈ {grams}г ({SIZES[draft['size']][0].lower()})"
        return f"Порция: {grams}г (твой вес)"
    if draft.get("per_100g"):
        if draft.get("numbers"):
            return "Порция: не определена — числа на 100г, поправь вес кнопкой"
        return "Порция: не определена — поправь вес кнопкой"
    if draft.get("basis") and draft.get("base_grams"):
        grams = num(draft["base_grams"], 0)
        note = str(nutrition.get("portion") or "").strip()
        if not note or parse_grams(note, unit_required=True) is not None:
            return f"Порция: ≈ {grams}г ({draft['basis']})"
        # The model's own words ("1 штука, ~140г") say what was counted.
        if grams in note:
            return f"Порция: {note} ({draft['basis']})"
        return f"Порция: {note}, ≈ {grams}г ({draft['basis']})"
    return f"Порция: {nutrition.get('portion') or '—'}"


def text(draft: dict, footer: Optional[str] = None) -> str:
    """The confirm message for the draft as it stands; ``footer`` replaces the
    closing question once the draft is settled ("✅ Сохранено")."""
    if draft.get("groups_open") and not footer:
        return GROUPS_TEXT
    nutrition = draft["nutrition"]
    estimate = draft.get("estimate", False)
    parts = [draft["header"]]
    if draft.get("badge"):
        parts.append(f"Источник: {draft['badge']}")
    parts.extend(draft.get("detail_lines") or [])
    parts.append(f"Продукты: {', '.join(nutrition.get('foods') or [])}")
    if draft.get("numbers"):
        calories = calories_text(nutrition.get("calories"), estimate=estimate)
        parts.append(f"Калории: {calories}")
        for label, key in (
            ("Белки", "protein"),
            ("Жиры", "fats"),
            ("Углеводы", "carbs"),
        ):
            parts.append(
                f"{label}: {macro_text(nutrition.get(key), estimate=estimate)}"
            )
        parts.extend(draft.get("number_notes") or [])
    parts.append(_portion_line(draft))
    groups_line = food_groups.line(draft.get("groups"))
    if groups_line:
        parts.append(groups_line)
    if draft.get("cost_line"):
        parts.append(draft["cost_line"])
    parts.append("")
    if footer:
        parts.append(footer)
    elif draft.get("awaiting") == "grams":
        parts.append("Напиши вес в граммах, например 150.")
    elif draft.get("awaiting") == "food":
        parts.append(
            "Напиши, что это на самом деле, например «самса с картошкой» — "
            "фото останется."
        )
    else:
        parts.append("Всё верно? Сохрани или поправь кнопками ниже.")
    return "\n".join(parts)


def _cb(draft: dict, verb: str, arg: str = "") -> str:
    return f"{CALLBACK_PREFIX}{draft['id']}:{verb}" + (f":{arg}" if arg else "")


def _groups_markup(draft: dict) -> InlineKeyboardMarkup:
    """Three groups a row. The check goes last: a label that starts with its
    mark is the one ``custom_emoji`` can draw as an icon."""
    chosen = draft.get("groups") or []
    buttons = [
        InlineKeyboardButton(
            f"{mark} {label}" + (" ✓" if key in chosen else ""),
            callback_data=_cb(draft, GROUP_TOGGLE, key),
        )
        for key, (mark, label, _word) in food_groups.GROUPS.items()
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append(
        [InlineKeyboardButton("⬅️ К записи", callback_data=_cb(draft, GROUPS_CLOSE))]
    )
    return InlineKeyboardMarkup(rows)


def markup(draft: dict) -> InlineKeyboardMarkup:
    """Inline buttons under the draft. Sizes first (the main error of a photo
    estimate), then the two corrections, then save; cancel is the way out."""
    if draft.get("groups_open"):
        return _groups_markup(draft)
    rows = []
    if draft.get("chips") and can_rescale(draft):
        row = []
        for key in SIZES:
            mark = "✓ " if draft.get("size") == key else ""
            grams = num(chip_grams(draft, key), 0)
            row.append(
                InlineKeyboardButton(
                    f"{mark}{CHIP_WORDS[key]}{grams}г",
                    callback_data=_cb(draft, SIZE, key),
                )
            )
        rows.append(row)
    edit_row = []
    if can_rescale(draft):
        edit_row.append(
            InlineKeyboardButton("✏️ Поправить вес", callback_data=_cb(draft, ASK_GRAMS))
        )
    edit_row.append(
        InlineKeyboardButton("✏️ Поправить продукт", callback_data=_cb(draft, ASK_FOOD))
    )
    rows.append(edit_row)
    rows.append(
        [
            # Green (Bot API 9.4 ``style``) on the one button that records the
            # meal. PTB 21.9 predates the field, so it rides in api_kwargs; a
            # client without colours shows a plain button.
            InlineKeyboardButton(
                "✅ Сохранить",
                callback_data=_cb(draft, SAVE),
                api_kwargs={"style": "success"},
            ),
            InlineKeyboardButton(
                "⭐ И в избранное", callback_data=_cb(draft, SAVE_FAVORITE)
            ),
        ]
    )
    last = [
        InlineKeyboardButton("🏷 Группы", callback_data=_cb(draft, GROUPS_OPEN)),
        InlineKeyboardButton("✖️ Отмена", callback_data=_cb(draft, CANCEL)),
    ]
    if not draft.get("numbers_setting"):
        last.insert(
            1,
            InlineKeyboardButton(
                toggle_label(bool(draft.get("numbers"))),
                callback_data=_cb(draft, NUMBERS),
            ),
        )
    rows.append(last)
    return InlineKeyboardMarkup(rows)


def parse_callback(data: Optional[str]) -> Optional[tuple]:
    """``meal:<id>:<verb>[:<arg>]`` → (id, verb, arg), or None if malformed."""
    if not data or not data.startswith(CALLBACK_PREFIX):
        return None
    parts = data[len(CALLBACK_PREFIX) :].split(":")
    if len(parts) not in (2, 3) or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1], parts[2] if len(parts) == 3 else ""
