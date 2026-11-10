"""«🗒 Дневник дня»: the day's meals as a widget inside a message (Bot API 10.4).

What was eaten and when, a part of the day at a time, each meal with its dish emoji, its
food-group marks in their own colours, ⭐ when a dish came from the person's own base and
📷 when the meal has a photo. A tap opens a meal inside the widget; nothing goes to the
bot. No calories (owner 2026-11-10): this is a record, not a count.

The widget's code never changes after it is sent, so the day is baked into the HTML of
each send, and ‹ › under it send the next day as a new widget. The code may be at most
4096 bytes after zlib -9; a day that does not fit even with its dishes left out goes as
text (:func:`text`). The caption is the short text twin for clients that show no widget.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.crud.crud_meal_item import crud_meal_item
from app.crud.crud_user import crud_user
from app.models.meal import Meal
from app.services import day_card, dish_emoji, food_groups
from app.services.settings_screen import user_zone
from app.services.stats_screen import _card_title, _meal_name, _meals_on, day_title

CALLBACK_PREFIX = "dy:"
DAY = "d"  # dy:d:<yyyymmdd>
BUDGET = 3800  # bytes after zlib -9: the guide's margin under Telegram's 4096
LIMIT = 4096
CAPTION_MAX = 1024
TEXT_MAX = 4096
MAX_BACK = 366  # days ‹ may go back
NAME_MAX = 32
EVENING = 18
DISHES = 4  # an opened meal shows this many, then «ещё N»
FEW = 8  # rows that fit 600 px with one meal opened, every part shown
# Rows one part may hold: 8 rows and an opened meal measured ~565 px of the 600.
PART_ROWS = 8
GROUP_KEYS = tuple(food_groups.GROUPS)  # the index a mark is drawn by
PARTS = ("Ночь", "День", "Вечер", "Без времени")
EMPTY = "За этот день записей нет."
SAVED, PHOTO, UNREAD = 1, 2, 4  # a row's flags

# One colour per group, the same as on every other screen: (light bg, fg, dark bg, fg).
COLOURS = {
    "legumes": ("#f8e5dc", "#97462a", "#3a241c", "#eeab91"),
    "anchor": ("#e2ebf8", "#2b5a8e", "#1d2a3b", "#a3c4ec"),
    "veg": ("#e1f1e6", "#2d7650", "#1c3527", "#91d6b0"),
    "fruit": ("#fbe3e7", "#a2334b", "#3b1e25", "#f0a3b2"),
    "grain": ("#f6eed2", "#76580c", "#352c14", "#e6c97b"),
    "nuts": ("#efe6da", "#6a4e2e", "#30271d", "#d8bb97"),
    "calcium": ("#e0f1f4", "#256c79", "#163239", "#93d3de"),
    "omega": ("#ebe5f6", "#58428c", "#29233a", "#c3b1ec"),
}


@dataclass(frozen=True)
class Row:
    clock: str  # «13:05», «≈ 13:05», «» for a meal that holds no time
    part: int  # index in PARTS
    emoji: str
    name: str
    flags: int
    groups: tuple  # group keys, in display order
    dishes: tuple  # ((emoji, name), …)


@dataclass(frozen=True)
class Diary:
    day: date
    html: Optional[str]  # None: does not fit a widget, send ``text``
    caption: str
    text: str
    markup: Optional[InlineKeyboardMarkup]


# --- the day ---------------------------------------------------------------------------


def _part(meal: Meal, zone: ZoneInfo) -> int:
    if not day_card.has_clock(meal):
        return 3
    hour = day_card.local_time(meal.meal_time, zone).hour
    if hour < day_card.NIGHT_ENDS:
        return 0
    return 1 if hour < EVENING else 2


def _cut(name: str, limit: int = NAME_MAX) -> str:
    name = " ".join((name or "").replace("\x00", "").split())
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"


def _calories(item) -> float:
    try:
        return float(item.base_calories or 0) * float(item.factor or 1)
    except (TypeError, ValueError):
        return 0.0


def row(db, meal: Meal, zone: ZoneInfo) -> Row:
    name = _meal_name(meal)
    items = crud_meal_item.live(db, meal.id) if db is not None else []
    if items:
        dishes = tuple((dish_emoji.of_name(i.name), _cut(i.name)) for i in items)
        biggest = max(items, key=_calories)  # a plate's emoji is its biggest dish's
        emoji = dish_emoji.of_name(biggest.name)
    else:
        names = day_card._item_names(meal)
        dishes = tuple((dish_emoji.of_name(n), _cut(n)) for n in names)
        emoji = dish_emoji.of_name(name)
    flags = 0
    if any(item.personal_food_id for item in items):
        flags |= SAVED
    if meal.photos:
        flags |= PHOTO
    if meal.reading_status in day_card.UNREAD:
        flags |= UNREAD
    groups = food_groups.ordered(day_card.tags(meal).get("food_groups") or ())
    clock = day_card.clock(meal, zone) if day_card.has_clock(meal) else ""
    return Row(
        clock, _part(meal, zone), emoji, _cut(name), flags, tuple(groups), dishes
    )


def rows_of(db, user, day: date, zone: ZoneInfo) -> list:
    meals = _meals_on(db, user, day, zone)
    rows = [row(db, meal, zone) for meal in meals]
    return sorted(rows, key=lambda r: r.part == 3)  # stable: the times keep their order


def counts(rows: list) -> list:
    """(group, meals it was in), in display order, only what was there."""
    seen = {}
    for r in rows:
        for key in r.groups:
            seen[key] = seen.get(key, 0) + 1
    return [(key, seen[key]) for key in GROUP_KEYS if key in seen]


def _plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def entries(count: int) -> str:
    return f"{count} {_plural(count, 'запись', 'записи', 'записей')}"


def _chip(key: str, count: int) -> str:
    mark, label, _word = food_groups.GROUPS[key]
    return f"{mark} {label.lower()}" + (f" ×{count}" if count > 1 else "")


def _summary(rows: list) -> str:
    return " · ".join(_chip(key, n) for key, n in counts(rows))


# --- the widget ------------------------------------------------------------------------


def _css() -> str:
    tones = "".join(
        f".g{i}{{background:light-dark({c[0]},{c[2]});color:light-dark({c[1]},{c[3]})}}"
        for i, c in enumerate(COLOURS[key] for key in GROUP_KEYS)
    )
    return (
        ":root{color-scheme:light dark}"
        "body{margin:0;padding:10px 12px 8px;font:15px/1.3 system-ui,sans-serif;"
        "--h:var(--tg-theme-hint-color);"
        "--s:var(--tg-theme-section-separator-color,var(--tg-theme-secondary-bg-color))}"
        ".h{display:flex;align-items:baseline;gap:8px}.h b{flex:1;font-size:1.1em}"
        ".n,.t,.a,.q{color:var(--h);font-size:.85em}"
        ".c{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0 2px}"
        ".g{display:inline-block;border-radius:9px;padding:1px 6px;font-size:.8em;"
        "white-space:nowrap}"
        ".k{display:flex;gap:2px}.k .g{padding:1px 3px}"
        "button{display:flex;align-items:center;gap:8px;width:100%;margin:0;padding:6px 0;"
        "border:0;border-bottom:1px solid var(--s);border-radius:0;background:none;"
        "color:inherit;font:inherit;text-align:left}"
        ".p{margin-top:6px;padding:4px 0 2px;font-weight:600;font-size:.9em}"
        ".t{width:40px;flex:none}.e{width:22px;flex:none;text-align:center}"
        ".m{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
        ".u .m{color:var(--h)}"
        ".o{padding:4px 0 8px 48px;border-bottom:1px solid var(--s)}.o div{margin:2px 0}"
        ".o .c{margin:4px 0 0}.a{margin-top:6px;text-align:right}" + tones
    )


_SCRIPT = (
    "const $=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;"
    "if(x!=null)e.textContent=x;return e},A=document.getElementById('a');"
    "let o=-1,P=D.p.map((_,i)=>D.f||i==D.p.length-1);"
    "function g(i,w){return $('span','g g'+i,w?G[i][0]+' '+G[i][1]:G[i][0])}"
    "function draw(){A.textContent='';const h=$('div','h');h.append($('b',0,D.t),"
    "$('span','n',D.n));A.append(h);const c=$('div','c');"
    "D.c.forEach(k=>c.append($('span','g g'+k[0],G[k[0]][0]+' '+G[k[0]][1]"
    "+(k[1]>1?' ×'+k[1]:''))));A.append(c);if(!D.p.length)A.append($('div','q',D.e));"
    "let n=0;D.p.forEach((p,j)=>{const b=$(D.f?'div':'button','p',p[0]"
    "+(P[j]?'':' · '+p[1].length));b.onclick=()=>{P=P.map((x,i)=>i==j&&!x);o=-1;draw()};"
    "if(D.p.length>1||!D.f)A.append(b);"
    "p[1].forEach(r=>{const i=n++;if(!P[j])return;"
    "const w=$('button',r[3]&4?'u':'');w.append($('span','t',r[0]),$('span','e',r[1]),"
    "$('span','m',r[2]));const k=$('span','k');"
    "if(r[3]&1)k.append('⭐');if(r[3]&2)k.append('📷');"
    "[...r[4]].forEach(x=>k.append(g(+x)));w.append(k);"
    "w.onclick=()=>{o=o==i?-1:i;draw()};A.append(w);"
    "if(o==i){const d=$('div','o');r[5].forEach(x=>d.append($('div',0,x[0]+' '+x[1])));"
    "if(r[3]&4)d.append($('div','q',D.u));const q=$('div','c');"
    "[...r[4]].forEach(x=>q.append(g(+x,1)));d.append(q);A.append(d)}})});"
    "if(D.a)A.append($('div','a',D.a))}draw()"
)


def _dishes(dishes: tuple, limit: int) -> list:
    """An opened meal's dishes, at most ``limit``; past it a count (600 px tall)."""
    if not limit:
        return []
    shown = [list(d) for d in dishes[:limit]]
    if len(dishes) > limit:
        shown.append(["…", f"ещё {len(dishes) - limit}"])
    return shown


def _data(rows: list, title: str, stamp: str, dishes: int) -> dict:
    parts = []
    for index, word in enumerate(PARTS):
        mine = [r for r in rows if r.part == index]
        if not mine:
            continue
        parts.append(
            [
                word,
                [
                    [
                        r.clock,
                        r.emoji,
                        r.name,
                        r.flags,
                        "".join(str(GROUP_KEYS.index(k)) for k in r.groups),
                        _dishes(r.dishes, dishes),
                    ]
                    for r in mine
                ],
            ]
        )
    return {
        "t": title,
        "n": entries(len(rows)),
        "c": [[GROUP_KEYS.index(k), n] for k, n in counts(rows)],
        "p": parts,
        "f": 1 if len(rows) <= FEW else 0,  # few: every part open; else one at a time
        "a": stamp,
        "e": EMPTY,
        "u": "Ещё не разобрано",
    }


def _script_json(value) -> str:
    """JSON safe inside <script>: no «<» at all (``</script>`` would end it, ``<!--`` would
    swallow the real end), no line separators."""
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return (
        raw.replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _groups_json() -> str:
    return _script_json([[g[0], g[1].lower()] for g in food_groups.GROUPS.values()])


def page(data: dict) -> str:
    return (
        f"<style>{_css()}</style><div id=a></div><script>"
        f"const D={_script_json(data)},G={_groups_json()};{_SCRIPT}</script>"
    )


def packed(html: str) -> int:
    """Bytes after zlib -9: what Telegram measures."""
    return len(zlib.compress(html.encode("utf-8"), 9))


def widget(rows: list, title: str, stamp: str) -> Optional[str]:
    """The day's HTML, as full as fits ``BUDGET``: the dishes inside a meal go first,
    then the names are cut shorter. None when even that does not fit, or when a part
    of the day holds more rows than 600 px can (nothing scrolls inside a widget)."""
    if any(sum(r.part == p for r in rows) > PART_ROWS for p in range(len(PARTS))):
        return None
    for dishes, cut in ((DISHES, None), (2, None), (0, None), (0, 18)):
        shown = rows
        if cut:
            shown = [
                Row(r.clock, r.part, r.emoji, _cut(r.name, cut), r.flags, r.groups, ())
                for r in rows
            ]
        html = page(_data(shown, title, stamp, dishes))
        if packed(html) <= BUDGET:
            return html
    return None


# --- the text twin ---------------------------------------------------------------------


def _row_line(r: Row) -> str:
    marks = ("⭐" if r.flags & SAVED else "") + ("📷" if r.flags & PHOTO else "")
    groups = "".join(food_groups.GROUPS[k][0] for k in r.groups)
    tail = " ".join(x for x in (marks, groups) if x)
    when = f"{r.clock} " if r.clock else ""
    return f"{when}{r.emoji}{dish_emoji.SEP}{r.name}" + (f" {tail}" if tail else "")


def caption(rows: list, title: str) -> str:
    head = (
        f"🗒 Дневник · {title} · {entries(len(rows))}"
        if rows
        else f"🗒 Дневник · {title}"
    )
    summary = _summary(rows)
    text = head + (f"\n{summary}" if summary else "")
    return text[:CAPTION_MAX]


def text(rows: list, title: str) -> str:
    """The whole day in words: the widget's stand-in when it cannot be sent."""
    head = f"🗒 Дневник · {title}"
    if not rows:
        return f"{head}\n{EMPTY}"
    lines = [f"{head} · {entries(len(rows))}"]
    for index, word in enumerate(PARTS):
        mine = [r for r in rows if r.part == index]
        if mine:
            lines.append(f"\n{word}")
            lines.extend(_row_line(r) for r in mine)
    summary = _summary(rows)
    tail = [f"\n{summary}"] if summary else []
    out = lines + tail
    while len("\n".join(out)) > TEXT_MAX and len(lines) > 2:
        lines.pop()
        out = lines + ["…"] + tail
    return "\n".join(out)


# --- the screen ------------------------------------------------------------------------


def markup(day: date, today: date) -> Optional[InlineKeyboardMarkup]:
    back = day - timedelta(days=1)
    row = []
    if (today - back).days <= MAX_BACK:
        row.append(
            InlineKeyboardButton(
                f"‹ {day_title(back)}",
                callback_data=f"{CALLBACK_PREFIX}{DAY}:{back:%Y%m%d}",
            )
        )
    if day < today:
        ahead = day + timedelta(days=1)
        label = "Сегодня ›" if ahead == today else f"{day_title(ahead)} ›"
        row.append(
            InlineKeyboardButton(
                label, callback_data=f"{CALLBACK_PREFIX}{DAY}:{ahead:%Y%m%d}"
            )
        )
    return InlineKeyboardMarkup([row]) if row else None


def parse(data: str, today: date) -> Optional[date]:
    """The day a ‹ › tap asks for; None for anything else, a future day or too far back."""
    head = f"{CALLBACK_PREFIX}{DAY}:"
    if not (data or "").startswith(head):
        return None
    digits = data[len(head) :]
    if len(digits) != 8 or not digits.isascii() or not digits.isdigit():
        return None  # strptime alone reads "2026111" as 1 November
    try:
        day = datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        return None
    if day > today or (today - day).days > MAX_BACK:
        return None
    return day


def build(db, user, day: date, zone: ZoneInfo, now: datetime) -> Diary:
    today = day_card.local_time(now, zone).date()
    rows = rows_of(db, user, day, zone)
    title = _card_title(day)
    title = title[0].upper() + title[1:]
    stamp = ""
    if day == today:
        local = day_card.local_time(now, zone)
        stamp = f"на {day_card.plain_time(local)}"
    return Diary(
        day=day,
        html=widget(rows, title, stamp),
        caption=caption(rows, title),
        text=text(rows, title),
        markup=markup(day, today),
    )


def open_day(db, telegram_id: int, now: datetime, day: Optional[date] = None) -> Diary:
    """The person's diary for ``day``, today in their zone when None."""
    user = crud_user.get_by_telegram_id(db, telegram_id)
    zone = user_zone(user)
    if day is None:
        day = day_card.local_time(now, zone).date()
    return build(db, user, day, zone, now)


def tapped(db, telegram_id: int, data: str, now: datetime) -> Optional[Diary]:
    """The day a ‹ › tap asks for; None for a day it may not ask for."""
    user = crud_user.get_by_telegram_id(db, telegram_id)
    zone = user_zone(user)
    day = parse(data, day_card.local_time(now, zone).date())
    return None if day is None else build(db, user, day, zone, now)
