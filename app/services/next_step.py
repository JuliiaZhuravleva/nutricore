"""One next step on today's card (stage 5, slices F and K, G5-R11, G5-R20, G5-R27).

The person chooses a direction from 2–3 options; until then there is no step. Inside
it, rules, no model and no learning: the evidence that an algorithm should optimise
eating on its own is very low. The order is the research's: drop what the user
never wants suggested, then one addition from the week's least-present group, then
benefit × chance of doing it ÷ effort. A step only ever adds, to any food at all,
ready-made included. Benefit and chance are the project's own rough marks. The step
stays from day to day until it is changed (G5-R29).

The energy mode is the user's alone to switch and limits the step's size. Two
«😴 Не сейчас» in a row make the next step smaller, without a word about it, and
that counter drives nothing else: a step not done is not counted or shown anywhere.

Pure, no Telegram and no database: these functions take ``User.settings`` and return
a new dict. The screen is ``stats_screen``. ``NO_SUGGEST`` and ``RELIABLE`` are read
here and filled by slice G.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

STATE = "next_step"  # in ``User.settings``: today's one step
ENERGY = "energy"  # in ``User.settings``; absent means normal
FOCUS = "focus"  # in ``User.settings``: the chosen direction, {"id": ..., ...}
NO_SUGGEST = "no_suggest"  # foods never offered (G5-R13)
RELIABLE = "reliable_foods"  # what the low mode reaches for first (G5-R12)

NORMAL, LOW, SIMPLE = "normal", "low", "simple"
OPEN, DONE, LATER = "open", "done", "later"
TAKE, UNDO, PUT_OFF, BRING_BACK, SWAP = "d", "u", "l", "b", "w"
VERBS = (TAKE, UNDO, PUT_OFF, BRING_BACK, SWAP)
_LIMITS = {NORMAL: 10, LOW: 2, SIMPLE: 1}  # minutes
_SHRUNK = {10: 2, 2: 1, 1: 1}
_SHRINK_AFTER = 2
EARLY, PROTEIN, PLANTS, EXTRAS = "early", "protein", "plants", "extras"
RECORDS = "records"  # chosen too: records only, no step
RECORDS_BUTTON = "📋 Пока только записи"
_OPTIONS_MAX = 3
_NEARLY_DAILY = 6  # days of the week: this group needs no step to bring it

# mode → (button, toast, the line on the card, or None)
MODES = {
    NORMAL: ("🔋 Силы: обычно", "🔋 Обычно: шаги до 10 минут", None),
    LOW: (
        "🪫 Силы: мало",
        "🪫 Мало сил: шаги на минуту-две, из готового",
        "🪫 Режим: мало сил",
    ),
    SIMPLE: (
        "🛋 Силы: минимум",
        "🛋 Минимум сил: шаг — только если хочется. Любая еда считается.",
        "🛋 Режим: минимум сил",
    ),
}
_CYCLE = {NORMAL: LOW, LOW: SIMPLE, SIMPLE: NORMAL}


@dataclass(frozen=True)
class Step:
    id: str
    groups: tuple  # the first one is its picture
    minutes: int
    title: str  # reads on after «Один шаг:», so it starts in lower case
    note: str = ""
    foods: tuple = ()  # the word starts «не предлагать» and reliable foods match
    benefit: int = 2
    chance: int = 2
    cue: bool = False  # tied to a moment of the day, the «earlier» direction's

    @property
    def rank(self) -> float:
        return self.benefit * self.chance / self.minutes


# Most are the low-effort anchors of docs/researches/vegan-slices-evidence.md §8. Fruit,
# vegetables, seeds and any nuts are the project's additions: that list has no such items.
STEPS = (
    Step("hummus", ("legumes", "anchor"), 1, "готовый хумус к хлебу",
         "Из магазина — тоже считается.", ("хумус", "нут"), 3, 3),
    Step("soy", ("calcium", "anchor"), 1, "стакан соевого напитка с кальцием",
         "", ("соя", "соевый"), 3, 2),
    Step("fruit", ("fruit",), 1, "банан или яблоко под рукой",
         "", ("банан", "яблоко"), 2, 3),
    Step("walnut", ("omega", "nuts"), 1, "пара грецких орехов к чаю",
         "", ("орех",), 2, 3),
    Step("bread", ("grain",), 1, "цельнозерновой хлеб к еде",
         "", ("хлеб",), 2, 3),
    Step("nuts", ("nuts",), 2, "горсть орехов к перекусу",
         "Можно прямо из пачки.", ("орех",), 2, 3),
    Step("seeds", ("nuts",), 2, "ложка семечек в салат или кашу",
         "", ("семечки", "семена"), 2, 2),
    Step("tahini", ("calcium", "nuts"), 2, "ложка тахини к тарелке",
         "", ("тахини", "кунжут"), 2, 2),
    Step("peanut", ("anchor", "nuts"), 2, "арахисовая паста на хлеб",
         "", ("арахис",), 2, 3),
    Step("berries", ("fruit",), 2, "горсть ягод к каше или йогурту",
         "Замороженные тоже подходят.", ("ягоды",), 2, 2),
    Step("greens", ("veg",), 2, "горсть готовых овощей к тарелке",
         "Из банки или пакета — тоже считается.", ("овощи",), 2, 2),
    Step("flax", ("omega",), 2, "ложка молотого льна в кашу или йогурт",
         "", ("лен", "льна"), 2, 2),
    Step("beans", ("legumes", "anchor"), 5, "нут или фасоль из банки к гарниру",
         "Открыть, промыть, добавить.", ("нут", "фасоль"), 3, 2),
    Step("edamame", ("legumes", "anchor"), 5, "замороженный эдамаме к тарелке",
         "", ("эдамаме", "соя"), 3, 1),
    Step("oats", ("grain",), 5, "овсянка или другая цельная каша к еде",
         "", ("овсянка", "каша"), 2, 2),
    Step("tofu", ("anchor", "legumes"), 10, "тофу на сковороду к любому блюду",
         "", ("тофу", "соя"), 3, 2),
    Step("frozen", ("veg",), 10, "замороженные овощи на сковороду к готовому блюду",
         "", ("овощи",), 3, 2),
    # A moment, not an hour: an hour says nothing about a day that starts late.
    # Any food counts here, so these name none and ``groups`` is only their picture.
    Step("wake_coffee", ("fruit",), 1, "когда встанешь, что-то готовое к кофе",
         "Подойдёт любая еда.", cue=True),
    Step("wake_home", ("grain",), 1, "когда встанешь, пара кусочков того, что дома",
         "", cue=True),
    Step("wake_first", ("nuts",), 2, "первым делом после сна — любой перекус",
         "Из пачки тоже считается.", cue=True),
)  # fmt: skip
_BY_ID = {step.id: step for step in STEPS}


@dataclass(frozen=True)
class Focus:
    """A direction the person can choose: its steps, and what its count counts."""

    id: str
    button: str
    label: str  # the count's words on the week: «Овощи или фрукты: 4 дня»
    groups: frozenset = frozenset()
    cue: bool = False


FOCUSES = {
    focus.id: focus
    for focus in (
        Focus(EARLY, "🕐 Поесть один раз пораньше", "Еда до", cue=True),
        Focus(
            PROTEIN,
            "🥫 Добавить готовое с белком",
            "Белковая еда",
            frozenset({"legumes", "anchor"}),
        ),
        Focus(
            PLANTS,
            "🥕 Добавить овощ или фрукт",
            "Овощи или фрукты",
            frozenset({"veg", "fruit"}),
        ),
        Focus(
            EXTRAS,
            "🌰 Добавить орехи или злаки",
            "Орехи или цельные злаки",
            frozenset({"nuts", "grain", "omega"}),
        ),
    )
}


@dataclass(frozen=True)
class View:
    """What the card shows: ``step`` is None when nothing is left to offer."""

    step: Optional[Step]
    state: str
    mode: str


def energy(settings: dict) -> str:
    mode = settings.get(ENERGY)
    return mode if isinstance(mode, str) and mode in MODES else NORMAL


def next_energy(mode: str) -> str:
    return _CYCLE[mode]


def lower_energy(mode: str) -> Optional[str]:
    """One mode down, for «🪫 Сделать проще»; None at the lowest."""
    return None if mode == SIMPLE else _CYCLE[mode]


def _words(text: str) -> list:
    return re.findall(r"\w+", text.casefold().replace("ё", "е"))


def _names(step: Step, wanted: str) -> bool:
    """Every word of ``wanted`` starts a word of the step, endings aside: «орехи»
    finds «орехов». Too wide costs one step; too narrow suggests what was refused."""
    have = _words(f"{step.title} {' '.join(step.foods)}")
    words = _words(wanted)
    return bool(words) and all(
        any(found.startswith(word[: max(3, len(word) - 2)]) for found in have)
        for word in words
    )


def _list(settings: dict, key: str) -> list:
    value = settings.get(key)
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str)]


def _refused(step: Step, settings: dict) -> bool:
    return any(_names(step, food) for food in _list(settings, NO_SUGGEST))


def _row(state: dict, key: str = "row") -> int:
    value = state.get(key)
    return value if isinstance(value, int) else 0


def _limit(mode: str, row: int) -> int:
    limit = _LIMITS[mode]
    return _SHRUNK[limit] if row >= _SHRINK_AFTER else limit


def chosen(settings: dict) -> Optional[str]:
    """The direction's id, ``RECORDS`` included; None when none was chosen."""
    value = settings.get(FOCUS)
    found = value.get("id") if isinstance(value, dict) else None
    return (
        found
        if isinstance(found, str) and (found in FOCUSES or found == RECORDS)
        else None
    )


def focus(settings: dict) -> Optional[Focus]:
    return FOCUSES.get(chosen(settings))


def _in_focus(step: Step, wanted: Optional[Focus]) -> bool:
    if wanted is None or step.cue != wanted.cue:
        return False
    return wanted.cue or bool(wanted.groups.intersection(step.groups))


def options(settings: dict, days: dict, early: bool) -> list:
    """2–3 directions from the records, least present first. ``early``: first food
    comes late in them, see ``step_count.early_clock``."""

    def possible(wanted: Focus) -> bool:
        return any(
            _in_focus(step, wanted) and not _refused(step, settings) for step in STEPS
        )

    foods = [f for f in FOCUSES.values() if not f.cue and possible(f)]
    # sorted() keeps the order of equals, so the same records give the same screen.
    foods.sort(key=lambda f: max(days.get(group, 0) for group in f.groups))
    rare = [
        f
        for f in foods
        if max(days.get(group, 0) for group in f.groups) < _NEARLY_DAILY
    ]
    picks = rare if len(rare) >= 2 else foods
    if early and possible(FOCUSES[EARLY]):
        picks = [FOCUSES[EARLY], *picks]
    return [f.id for f in picks[:_OPTIONS_MAX]]


def choose(settings: dict, wanted: str, extra: Optional[dict] = None) -> dict:
    """A new direction starts a new step; the one in use keeps its step as it is."""
    if chosen(settings) == wanted:
        return settings
    values = {key: value for key, value in settings.items() if key != STATE}
    return {**values, FOCUS: {"id": wanted, **(extra or {})}}


def _select(
    settings: dict, days: dict, row: int, skip, minutes: Optional[int] = None
) -> Optional[Step]:
    mode = energy(settings)
    limit = _limit(mode, row)
    wanted = focus(settings)
    pool = [
        step
        for step in STEPS
        if _in_focus(step, wanted)
        and step.minutes <= limit
        and step.id not in skip
        and (minutes is None or step.minutes == minutes)
        and not _refused(step, settings)
    ]
    if mode == LOW:
        reliable = _list(settings, RELIABLE)
        pool = [
            step for step in pool if any(_names(step, food) for food in reliable)
        ] or pool
    if not pool:
        return None
    # min() keeps the first of equals, so the same week always gives the same step.
    return min(
        pool, key=lambda step: (min(days.get(g, 0) for g in step.groups), -step.rank)
    )


def _text(value) -> Optional[str]:
    return value if isinstance(value, str) else None


def _state(settings: dict) -> dict:
    """Today's step as saved, with anything damaged evened out: settings are open to
    other writers, and a bad value must not cost the user the card."""
    state = settings.get(STATE)
    if not isinstance(state, dict):
        return {}
    seen = state.get("seen")
    found = state.get("state")
    return {
        **state,
        "step": _text(state.get("step")),
        "last": _text(state.get("last")),
        "seen": (
            [x for x in seen if isinstance(x, str)] if isinstance(seen, list) else []
        ),
        "state": found if found in (OPEN, DONE, LATER) else OPEN,
    }


def _pick(settings: dict, day: str, days: dict, row: int, last) -> dict:
    # Yesterday's step waits a day, unless it is the only one left.
    step = _select(settings, days, row, {last}) or _select(settings, days, row, ())
    return {
        "day": day,
        "step": step.id if step else None,
        "state": OPEN,
        "seen": [step.id] if step else [],
        "row": row,
        "before": row,
        "last": last,
    }


def _still_fits(step: Step, settings: dict, row: int) -> bool:
    return (
        _in_focus(step, focus(settings))
        and not _refused(step, settings)
        and step.minutes <= _limit(energy(settings), row)
    )


def current(settings: dict, today: date, group_days: Callable[[], dict]) -> dict:
    """``settings`` with today's step in it. A step stays until it is changed, and is
    chosen again only when it no longer fits or after two «😴 Не сейчас» in a row.
    ``group_days``: days per group this week, asked for only when a step is chosen."""
    if focus(settings) is None:
        return settings
    state = _state(settings)
    day = today.isoformat()
    step = _BY_ID.get(state.get("step"))
    row = _row(state)
    if state.get("day") == day:
        # Done or put off, it stays. Open, it must still be wanted and still fit the
        # mode: «Вернуть шаг» can bring it back after the mode went lower.
        if step is not None and (
            state.get("state") != OPEN or _still_fits(step, settings, row)
        ):
            return settings
        last = state.get("last")
    else:
        if (
            step is not None
            and row < _SHRINK_AFTER
            and _still_fits(step, settings, row)
        ):
            kept = {
                **state,
                "day": day,
                "state": OPEN,
                "seen": [step.id],
                "before": row,
            }
            return {**settings, STATE: kept}
        last = state.get("step")
    picked = _pick(settings, day, group_days(), row, last)
    return {**settings, STATE: picked}


def view(settings: dict) -> View:
    state = _state(settings)
    step = _BY_ID.get(state.get("step")) if focus(settings) else None
    return View(step, state.get("state", OPEN), energy(settings))


def tap(
    settings: dict, verb: str, shown: str, today: date, group_days: Callable[[], dict]
) -> tuple[dict, Optional[str]]:
    """One button on the step. What the button showed travels with it: an old card
    or a double tap changes nothing and just shows the card as it is."""
    settings = current(settings, today, group_days)
    state = _state(settings)
    if focus(settings) is None:
        return settings, None
    if state.get("step") != shown:
        return settings, "Шаг уже другой, вот нынешний"
    now, row = state.get("state"), _row(state)
    if verb == TAKE and now == OPEN:
        state = {**state, "state": DONE, "before": row, "row": 0}
        toast = "✓ Отмечено"
    elif verb == UNDO and now == DONE:
        state, toast = {**state, "state": OPEN, "row": _row(state, "before")}, None
    elif verb == PUT_OFF and now == OPEN:
        state, toast = {**state, "state": LATER, "before": row, "row": row + 1}, None
    elif verb == BRING_BACK and now == LATER:
        state, toast = {**state, "state": OPEN, "row": _row(state, "before")}, None
    elif verb == SWAP and now == OPEN:
        state, toast = _swapped(settings, state, group_days())
    else:
        return settings, None
    return {**settings, STATE: state}, toast


def _swapped(settings: dict, state: dict, days: dict) -> tuple[dict, str]:
    """The same size first, then any size the mode allows; once all were seen, again."""
    row, shown = _row(state), state.get("step")
    seen = set(state.get("seen") or []) | {shown}
    size = _BY_ID[shown].minutes
    same = _select(settings, days, row, seen, size)
    step = same or _select(settings, days, row, seen)
    if step is None:
        seen = {shown}
        same = _select(settings, days, row, seen, size)
        step = same or _select(settings, days, row, seen)
    if step is None:
        return state, "Другого шага сейчас нет"
    state = {**state, "step": step.id, "seen": sorted(seen | {step.id})}
    return state, "Другой шаг того же размера" if same else "Другой шаг"


def switch_energy(
    settings: dict, wanted: str, today: date, group_days: Callable[[], dict]
) -> tuple[dict, Optional[str]]:
    """The mode from the button. An open step is chosen again for the new size; a
    step done or put off today stays as it is."""
    if wanted not in MODES:
        return settings, None
    if energy(settings) == wanted:
        return settings, None
    settings = {**settings, ENERGY: wanted}
    state = _state(settings)
    # No step yet today (the button can be an old card's): the next view with a meal
    # chooses one, as always.
    if (
        focus(settings) is not None
        and state.get("day") == today.isoformat()
        and state.get("state") == OPEN
    ):
        picked = _pick(
            settings, today.isoformat(), group_days(), _row(state), state.get("last")
        )
        settings = {**settings, STATE: picked}
    return settings, MODES[wanted][1]


def lines(shown: View) -> list:
    """The step in the card's text."""
    step = shown.step
    if step is None:
        return []
    if shown.state == LATER:
        return ["😴 Шаг отложен до завтра."]
    if shown.state == DONE:
        return [f"👣 {step.title[:1].upper()}{step.title[1:]} — ✓ готово."]
    head = (
        "Если захочется" if shown.mode == SIMPLE else f"Один шаг, ≈{step.minutes} мин"
    )
    note = f" {step.note}" if step.note else ""
    return [f"👣 <b>{head}:</b> {step.title}.{note}"]


def picture(shown: View) -> Optional[dict]:
    """The same step on the picture."""
    step = shown.step
    if step is None:
        return None
    block = {
        "state": shown.state,
        "icon": step.groups[0],
        "min": f"≈{step.minutes} мин",
        "title": f"{step.title[:1].upper()}{step.title[1:]}",
        "sub": step.note,
    }
    if shown.mode == SIMPLE:
        block["kick"] = "Если захочется"
    return block
