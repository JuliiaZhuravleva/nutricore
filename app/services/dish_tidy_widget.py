"""🍲 Порядок в блюдах as a Telegram widget (Bot API 10.4): the page, its state and the tap.

The checkbox text screen (``dish_tidy_screen``) is this widget's text twin: the same batch,
the same words, sent instead when the switch is off, the batch does not fit a widget, or
Telegram refuses it. The page is built per batch with its dictionary baked in (the groups,
the row names, the «N дней из 28» notes, the week's day, the expiry day); the message's
``chat_data`` carries only what changes, integers and strings only:

    r  revision: 1 open, 2 applied, 3 closed (undone, retired, expired). Derived from the
       batch's state, no column: a state never goes back down the ladder, so it is a
       revision. The page ignores an older state; the bot answers a tap with an older
       revision with the current state and writes nothing.
    m  the ticked rows, a hex mask (bit ``idx``): the default ticks while open; after
       apply what the apply used, read back from the rows' states
    e  the proposals' end, unix seconds: the page switches to its expired look by itself
    d  1 once the batch was applied
    u  1 once it was taken back
    x  1 when a newer batch retired it
    f  hex mask of the rows that did not apply (their dishes changed after the batch)

The page sends one request, ``a:<rev>:<mask hex>``; the bot applies through
``dish_tidy_batch.apply`` (this module holds no merge logic), answers with the new state
first and edits ``chat_data`` after. ✓ shows only after that answer.
"""

from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.core.config import settings
from app.crud.crud_dish_tidy import OPEN, crud_dish_tidy
from app.crud.crud_user import crud_user
from app.models.dish_tidy import DishTidyBatch, DishTidyItem
from app.models.user import User
from app.services import dish_tidy, dish_tidy_batch, dish_tidy_screen, widget_queries
from app.services.close_row import ok_row
from app.services.diary_widget import BUDGET, CAPTION_MAX, _script_json, packed
from app.services.meal_card import _cut

logger = logging.getLogger(__name__)

PREFIX = "a"  # widget_queries route: the page's data is ``a:<rev>:<mask hex>``
FORM = "widget"
TEXT_LABEL = "💬 Текстом"  # the way out for a phone that cannot draw the widget
MAX_STATE = widget_queries.MAX_DATA_BYTES
CUTS = (dish_tidy_screen.LINE_NAME_MAX, 40, 28, 20)  # names, longest first
HEIGHT_MAX = 560  # px at 320 wide: Telegram clips at 600 and nothing scrolls inside
CHARS_LINE = 34  # a row's name and note at 15 px beside the circle at 320 px

# Group kinds on the page: g[1] in the dictionary.
_PLAIN, _STAR, _SAME = 0, 1, 2
_KIND = {dish_tidy.MERGE: _PLAIN, dish_tidy.STAR: _STAR, dish_tidy.SAME: _SAME}
_TITLES = {
    dish_tidy.SAME: "Объединено само — одинаковые названия",
    dish_tidy.MERGE: "Похоже на одно блюдо",
    dish_tidy.STAR: "В Избранное — часто в записях",
}
ALREADY = "уже объединено"
HOW = "Нажатие на строку ставит или снимает отметку."
HOW_SAME = " Без отметки объединённое само разъединится, а предложение не применится."
HOW_PLAIN = " Без отметки предложение не применится."
UNTIL = "Предложения действуют до {day}."
CAPTION_HEAD = "🍲 Порядок в блюдах · {day}"

# --- the page ---------------------------------------------------------------------------

_CSS = (
    "body{margin:0;padding:10px 12px;font:15px/1.3 system-ui,sans-serif;"
    "--h:var(--tg-theme-hint-color);--ok:light-dark(#2d7650,#91d6b0);"
    "--s:var(--tg-theme-section-separator-color,var(--tg-theme-secondary-bg-color))}"
    ".h{display:flex;align-items:baseline;gap:8px}.h b{flex:1;font-size:1.1em}"
    ".h span,.q,.w{color:var(--h);font-size:.85em}.ex{margin:6px 0 0;font-size:15px;color:var(--h)}"
    ".g{margin:10px 0 2px;font-weight:600;font-size:.9em;display:flex;gap:6px;"
    "align-items:center}"
    ".r{display:flex;align-items:center;gap:10px;width:100%;margin:0;padding:7px 0;"
    "border:0;border-bottom:1px solid var(--s);border-radius:0;background:none;"
    "color:inherit;font:inherit;text-align:left}"
    ".m{flex:1;min-width:0}.m i{font-style:normal;color:var(--h)}"
    ".c{width:22px;height:22px;flex:none;border-radius:11px;border:2px solid var(--h);"
    "display:flex;align-items:center;justify-content:center;box-sizing:border-box}"
    ".on .c{background:var(--ok);border-color:var(--ok)}"
    "svg{width:14px;height:14px;fill:none;stroke:#fff;stroke-width:3;"
    "stroke-linecap:round;stroke-linejoin:round}"
    ".g svg{width:16px;height:16px;stroke:#3A3442;stroke-width:1.5;fill:#F5C46A}"
    ".a{display:block;width:100%;margin:12px 0 6px;padding:10px;border:0;"
    "border-radius:10px;background:var(--tg-theme-button-color);"
    "color:var(--tg-theme-button-text-color);font:inherit;font-weight:600}"
    ".a:disabled{background:var(--s);color:var(--h)}"
    ".end .r{color:var(--h)}.end .c{border:0;width:auto;color:var(--h)}"
    ".end .on .c{background:none}.end .on .c svg{stroke:var(--ok)}"
    ".st{margin:4px 0 0;font-weight:600}"
)

# Icons: one hidden sheet of symbols, drawn by <use>. No emoji in the page's own code.
_ICONS = (
    '<svg style="position:absolute;width:0;height:0"><symbol id="iy" viewBox="0 0 24 24">'
    '<path d="M6 12.5l4 4 8-9"/></symbol><symbol id="is" viewBox="0 0 24 24">'
    '<path d="M12 3l2.7 5.6 6.1.8-4.5 4.2 1.1 6.1L12 16.8 6.6 19.7l1.1-6.1L3.2 9.4'
    'l6.1-.8z"/></symbol></svg>'
)

_SCRIPT = (
    "const W=window.TelegramWidget,$=(t,c,x)=>{const e=document.createElement(t);"
    "if(c)e.className=c;if(x!=null)e.textContent=x;return e},"
    "L=document.getElementById('l');"
    "const T={apply:'Применить отмеченное (',fin:'Готово',wait:'Применяю…',"
    "did:'Применено: ',of:' из ',ok:'Готово.',undo:'Отменено: всё вернулось, как было.',"
    "part:'Отменено не всё: где «не отменилось», без изменений.',"
    "old:'Предложения устарели: им больше недели. Объединённое само не менялось.',"
    "shut:'Эта подборка закрыта: есть новая.',"
    "same:'объединено',apart:'разъединено',made:'сделано',keep:'не тронуто',"
    "back:'отменено',fail:'не вышло',stay:'не отменилось',"
    "err:{TIMEOUT:'Бот не ответил, нажми ещё раз',BUSY:'Ещё применяю прошлое',"
    "RATE_LIMITED:'Подожди немного и нажми ещё раз',"
    "NOT_ALLOWED:'Здесь это не открыть',FAILED:'Не применилось, нажми ещё раз'}};"
    "let S=null,M=0,F=0,N=0,busy=0,note='';"
    "function load(t){try{const s=JSON.parse(t);if(s&&(!S||s.r>=S.r)){S=s;"
    "M=parseInt(s.m,16)||0;F=parseInt(s.f||'0',16)||0;N=parseInt(s.n||'0',16)||0}}catch(e){}}"
    "const ended=()=>S.x||S.d||S.u||Date.now()/1e3>=S.e;"
    "function ic(i){const d=$('span');d.innerHTML='<svg><use href=\"#i'+i+'\"/></svg>';"
    "return d.firstChild}"
    "function bits(m){let n=0;for(;m;m>>=1)n+=m&1;return n}"
    "function word(k,gt){const t=M>>k&1;if(F>>k&1)return T.fail;"
    "if(S.u)return N>>k&1?T.stay:gt>1?T.apart:S.d&&t?T.back:T.keep;"
    "if(S.d)return gt>1?(t?T.same:T.apart):t?T.made:T.keep;return''}"
    "function draw(){L.textContent='';document.getElementById('w').textContent=D.w;"
    "const end=ended();document.body.className=end?'end':'';"
    "if(end)L.append($('div','st',S.u?(N?T.part:T.undo):S.d?(D.c?T.did+bits(M&~F&D.c)+T.of+"
    "bits(D.c):T.ok):S.x?T.shut:T.old));"
    "let i=0;D.g.forEach(g=>{const h=$('div','g');if(g[1]==1)h.append(ic('s'));"
    "h.append(g[0]);L.append(h);g[2].forEach(r=>{const k=i++,"
    "on=S.u?0:end&&!S.d?g[1]>1:(M>>k&1)&&!(F>>k&1),"
    "b=$('button','r'+(on?' on':'')),c=$('span','c'),m=$('span','m',r[0]);"
    "if(on)c.append(ic('y'));else if(end)c.textContent='—';"
    "const w=word(k,g[1]);if(r[1]&&!(w&&g[1]>1))m.append($('i',0,' · '+r[1]));"
    "if(w)m.append($('div','w',w));b.append(c,m);"
    "b.onclick=()=>{if(end||busy)return;M^=1<<k;draw()};L.append(b)})});"
    "if(!end){const n=bits(M&D.c),a=$('button','a',busy?T.wait:n?T.apply+n+')':T.fin);"
    "a.disabled=busy;a.onclick=apply;L.append(a);L.append($('div','q',note||D.h));"
    "if(D.u)L.append($('div','ex',D.u))}}"
    "function apply(){if(!W){S.d=1;draw();return}busy=1;note='';draw();"
    "W.request('a:'+S.r+':'+M.toString(16)).then(a=>{busy=0;"
    "if(a.result)load(a.result);else note=T.err.FAILED;draw()},"
    "e=>{busy=0;note=T.err[e.code]||T.err.FAILED;draw()})}"
    "if(W){load(W.chatData);W.onEvent('chatDataChanged',()=>{load(W.chatData);draw()});"
    "W.onEvent('activated',draw)}"
    "if(!S)S={r:0,m:'0',e:0};draw();if(W)W.ready();"
)


def page(data: dict) -> str:
    return (
        f"<style>{_CSS}</style>{_ICONS}"
        '<div class="h"><b>Порядок в блюдах</b><span id="w"></span></div>'
        f'<div id="l"></div><script>const D={_script_json(data)};{_SCRIPT}</script>'
    )


# --- the switch --------------------------------------------------------------------------


def mode() -> str:
    value = str(settings.TIDY_WIDGET or "").strip().lower()
    return value if value in ("off", "owner", "all") else "off"


def allowed(user: Optional[User]) -> bool:
    """Whether this person gets the widget form (the ``TIDY_WIDGET`` switch)."""
    if user is None:
        return False
    which = mode()
    if which == "all":
        return True
    return which == "owner" and user.telegram_id in settings.admin_ids


def is_widget(batch: DishTidyBatch) -> bool:
    return batch.form == FORM


# --- what a batch looks like as a widget -------------------------------------------------


def _note(item: DishTidyItem) -> str:
    if item.kind == dish_tidy.SAME:
        return ALREADY
    if item.kind == dish_tidy.STAR:
        days = item.days
        return f"{days} {dish_tidy_screen._days_word(days)} из {dish_tidy.WINDOW_DAYS}"
    return ""


def _name(item: DishTidyItem, cut: int) -> str:
    names = [_cut(n, cut) for n in item.label or []]
    if item.kind == dish_tidy.STAR:
        return names[0] if names else "?"
    return f"«{names[0]}» → «{names[1]}»" if len(names) > 1 else "?"


def _groups(items: list, cut: int) -> list:
    groups = []
    for item in items:
        if not groups or groups[-1][3] != item.kind:
            groups.append([_TITLES[item.kind], _KIND[item.kind], [], item.kind])
        groups[-1][2].append([_name(item, cut), _note(item)])
    return [g[:3] for g in groups]


def height(groups: list, has_until: bool) -> int:
    """A rough height in px at 320 wide: header, group titles, rows by their lines, the
    button, the hint and the expiry line. Telegram clips at 600 and nothing scrolls, so
    a batch past ``HEIGHT_MAX`` goes as text."""
    total = 24 + 20 + 58 + 3 * 16 + (28 if has_until else 0)
    for group in groups:
        total += 30
        for row in group[2]:
            text = row[0] + (" · " + row[1] if row[1] else "")
            lines = -(-len(text) // CHARS_LINE)
            total += 15 + 19 * max(lines, 1)
    return total


def _how(items: list) -> str:
    same = any(i.kind == dish_tidy.SAME for i in items)
    return HOW + (HOW_SAME if same else HOW_PLAIN)


def countable(items: list) -> int:
    """The rows «Применить отмеченное (N)» counts: the proposals. A row merged by
    itself stays tickable, but it is already done and is not a thing to apply."""
    return sum(1 << i.idx for i in items if i.kind != dish_tidy.SAME)


def _data(db, batch: DishTidyBatch, user: User, items: list, cut: int) -> dict:
    has_until = any(i.kind != dish_tidy.SAME for i in items) and bool(batch.expires_at)
    return {
        "w": dish_tidy_screen._day(batch.claimed_at, user),
        "u": (
            UNTIL.format(day=dish_tidy_screen._day(batch.expires_at, user))
            if has_until
            else ""
        ),
        "h": _how(items),
        "c": countable(items),
        "g": _groups(items, cut),
    }


def caption(batch: DishTidyBatch, user: User, items: list) -> str:
    """The text twin's header and the counts, short: the rows live in the widget."""
    lines = [CAPTION_HEAD.format(day=dish_tidy_screen._day(batch.claimed_at, user))]
    same = sum(1 for i in items if i.kind == dish_tidy.SAME)
    proposals = len(items) - same
    if same:
        lines.append(f"Объединено само: {same}")
    if proposals:
        lines.append(f"Предложений: {proposals}")
    return "\n".join(lines)[:CAPTION_MAX]


# --- the state ---------------------------------------------------------------------------

_REV = {
    "applied": 2,
    "undone": 3,
    "retired": 3,
    "expired": 3,
    "blocked": 3,
    "failed": 3,
}
_TICKED_STATES = ("applied", "undone", "stale")


def revision(batch: DishTidyBatch) -> int:
    """1 open, 2 applied, 3 closed: the batch's state only ever climbs this ladder."""
    return _REV.get(batch.state, 1)


def _was_applied(batch: DishTidyBatch, items: list) -> bool:
    if batch.state == "applied":
        return True
    if batch.state != "undone":
        return False
    # Undone from «applied» or straight from «retired» (its auto-merges only): a row that
    # was declined or a proposal that was decided means the apply happened.
    return any(
        i.state == "declined"
        or (i.kind != dish_tidy.SAME and i.state in _TICKED_STATES)
        for i in items
    )


def _mask_used(batch: DishTidyBatch, items: list, applied: bool) -> int:
    """The ticks: the default ones while open, else what the apply used, read back from
    the rows (a declined row was unticked; a stale proposal was ticked, a stale
    near-identical pair was not: it would not come apart)."""
    mask = 0
    for item in items:
        if applied and item.state != "proposed":
            if item.kind == dish_tidy.SAME:
                on = item.state in ("applied", "undone")
            else:
                on = item.state in _TICKED_STATES
        else:
            on = bool(item.ticked)
        mask |= int(on) << item.idx
    return mask


def state_of(batch: DishTidyBatch, items: list) -> dict:
    applied = _was_applied(batch, items)
    state = {"r": revision(batch), "m": f"{_mask_used(batch, items, applied):x}"}
    state["e"] = (
        int(dish_tidy.aware(batch.expires_at).timestamp()) if batch.expires_at else 0
    )
    if applied:
        state["d"] = 1
    if batch.state == "undone":
        state["u"] = 1
        # Undo leaves a row "applied" when its change could not be taken back: the
        # page names those rows and does not say everything came back.
        kept = sum(1 << i.idx for i in items if i.state == "applied")
        if kept:
            state["n"] = f"{kept:x}"
    if batch.state in ("retired", "blocked", "failed"):
        state["x"] = 1
    failed = sum(1 << i.idx for i in items if i.state == "stale")
    if failed:
        state["f"] = f"{failed:x}"
    return state


def state_json(batch: DishTidyBatch, items: list) -> str:
    return json.dumps(state_of(batch, items), separators=(",", ":"))


# --- the keyboard under the widget -------------------------------------------------------


def keyboard(db, batch: DishTidyBatch, user: User) -> Optional[InlineKeyboardMarkup]:
    """Open: the Sunday switch (the ticks are the widget's own). After the apply, and for
    a batch closed with something still to take back: ✅ ОК first, then ↩️ Отменить: the
    text screen's ``dt:`` buttons, so its undo is the one that runs. Open, «💬 Текстом»
    comes first: the same rows as a text message, for a phone that shows no widget."""
    if batch.state in OPEN or batch.state == "ready":
        items = crud_dish_tidy.items(db, batch.id)
        mask = f"{dish_tidy_screen.default_mask(items):x}"
        on = dish_tidy_batch.tidy_on(user)
        label = dish_tidy_screen.OFF_LABEL if on else dish_tidy_screen.ON_LABEL
        verb = "x" if on else "s"
        prefix = dish_tidy_screen.CALLBACK_PREFIX
        data = f"{prefix}{verb}:{batch.id}:{mask}"
        words = f"{prefix}{dish_tidy_screen.TEXT_VERB}:{batch.id}"
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(TEXT_LABEL, callback_data=words)],
                [InlineKeyboardButton(label, callback_data=data)],
            ]
        )
    if dish_tidy_screen._undoable(db, batch):
        undo = f"{dish_tidy_screen.CALLBACK_PREFIX}u:{batch.id}"
        return InlineKeyboardMarkup(
            [
                ok_row(),
                [InlineKeyboardButton(dish_tidy_screen.UNDO_ALL, callback_data=undo)],
            ]
        )
    return None


# --- building ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Built:
    html: str
    caption: str
    state: str  # chat_data as first sent


def build(db, batch: DishTidyBatch, user: User) -> Optional[Built]:
    """The batch as a widget; None when it does not fit one (code over ``BUDGET`` after
    zlib -9 even with the names cut short, or taller than 600 px): it goes as text."""
    items = crud_dish_tidy.items(db, batch.id)
    if not items:
        return None
    for cut in CUTS:
        data = _data(db, batch, user, items, cut)
        if height(data["g"], bool(data["u"])) > HEIGHT_MAX:
            continue
        html = page(data)
        if packed(html) <= BUDGET:
            state = state_json(batch, items)
            return Built(html, caption(batch, user, items), state)
    logger.info("dish tidy: batch %s does not fit a widget, sent as text", batch.id)
    return None


# --- the tap ------------------------------------------------------------------------------


def _parse(data: str) -> Optional[tuple]:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != PREFIX:
        return None
    rev, raw = parts[1], parts[2]
    if not (rev.isdigit() and len(rev) <= 6):
        return None
    if not (raw and len(raw) <= 6 and all(c in "0123456789abcdef" for c in raw)):
        return None
    return int(rev), int(raw, 16)


def _expire(db, batch: DishTidyBatch, now: datetime) -> None:
    """An open batch past its week ends here, declining nothing (as the text screen)."""
    if (
        batch.state in OPEN
        and batch.expires_at
        and now > dish_tidy.aware(batch.expires_at)
    ):
        batch.state = "expired"
        db.commit()


@dataclass(frozen=True)
class Outcome:
    state: Optional[str]  # the JSON to answer with; None: answer with no data
    chat_id: Optional[int] = None
    message_id: Optional[int] = None
    markup: Optional[InlineKeyboardMarkup] = None
    wrote: bool = False  # the apply ran: the keyboard and chat_data follow the answer


def tap(db, query: widget_queries.WidgetQuery, now: datetime) -> Outcome:
    """What a page's ``a:<rev>:<mask>`` does. Only the batch's own person writes anything:
    the batch is found by (chat, message, person), so a stranger's tap, a tap on no batch
    or on a text batch finds nothing."""
    parsed = _parse(query.data)
    user = crud_user.get_by_telegram_id(db, query.from_id)
    batch = None
    if user is not None and query.chat_id is not None and query.message_id is not None:
        batch = crud_dish_tidy.by_message(db, query.chat_id, query.message_id, user.id)
    if parsed is None or batch is None or not is_widget(batch):
        logger.warning("dish tidy widget: a tap with no batch of the person, ignored")
        return Outcome(None)
    rev, mask = parsed
    items = crud_dish_tidy.items(db, batch.id)
    _expire(db, batch, now)
    if mask >> len(items) or rev != revision(batch) or batch.state not in OPEN:
        # Older or newer than what is stored, or already settled: the current state.
        return Outcome(state_json(batch, items))
    result = dish_tidy_batch.apply(db, user, batch, mask, now)
    db.refresh(batch)
    items = crud_dish_tidy.items(db, batch.id)
    return Outcome(
        state_json(batch, items),
        batch.chat_id,
        batch.message_id,
        keyboard(db, batch, user),
        wrote=not result.already,
    )


async def refresh(bot, db, batch: DishTidyBatch, user: User) -> str:
    """The widget's state and keyboard follow the batch (undo, retire, the switch):
    ``editMessageWidgetData``, never ``editMessageText``: a widget message has no text.
    Failures are logged, never raised; the state edit's outcome is returned
    (``widget_queries.REFUSED``: Telegram would not draw it, :func:`to_text` may)."""
    if batch.chat_id is None or batch.message_id is None:
        return widget_queries.FAILED
    items = crud_dish_tidy.items(db, batch.id)
    state = state_json(batch, items)
    outcome = await widget_queries.edit_state(
        bot, batch.chat_id, batch.message_id, state
    )
    try:
        await bot.edit_message_reply_markup(
            chat_id=batch.chat_id,
            message_id=batch.message_id,
            reply_markup=keyboard(db, batch, user),
        )
    except TelegramError as error:
        if "not modified" not in str(error).lower():
            logger.info("dish tidy: batch %s keyboard not edited: %s", batch.id, error)
    return outcome


async def handle_tap(bot, db, query: widget_queries.WidgetQuery) -> None:
    """The route for ``a:…``: apply, answer with the new state FIRST (15 s), then edit
    ``chat_data`` and the keyboard. Nothing raises out of here."""
    try:
        outcome = tap(db, query, datetime.now(UTC))
    except Exception as error:
        # The type and the raising line, never the error's text: it may hold dish names.
        frames = traceback.extract_tb(error.__traceback__)
        where = (
            f"{Path(frames[-1].filename).name}:{frames[-1].lineno}" if frames else "?"
        )
        logger.error(
            "dish tidy widget: the tap of %s on message %s/%s failed: %s at %s",
            query.from_id,
            query.chat_id,
            query.message_id,
            type(error).__name__,
            where,
        )
        db.rollback()
        await widget_queries.answer(bot, query.id, None)
        return
    await widget_queries.answer(bot, query.id, outcome.state)
    if not outcome.wrote or outcome.state is None:
        return
    try:
        await widget_queries.edit_data(
            bot, outcome.chat_id, outcome.message_id, outcome.state
        )
        await bot.edit_message_reply_markup(
            chat_id=outcome.chat_id,
            message_id=outcome.message_id,
            reply_markup=outcome.markup,
        )
    except TelegramError as error:
        logger.info("dish tidy widget: keyboard not edited: %s", error)
    except Exception as error:
        logger.warning(
            "dish tidy widget: after-answer edit failed: %s", type(error).__name__
        )


# --- sending ------------------------------------------------------------------------------


async def send(bot, chat_id: int, built: Built, markup=None):
    """``sendWidget``, silent, with the chat state and queries on. Raises what Telegram
    raises: the caller tells a refusal from a failure to reach Telegram."""
    kwargs = {
        "chat_id": chat_id,
        "html": built.html,
        "caption": built.caption,
        "chat_data": built.state,
        "allow_queries": True,
        "disable_notification": True,
    }
    if markup is not None:
        kwargs["reply_markup"] = markup
    return await bot.do_api_request(
        "sendWidget", api_kwargs=kwargs, return_type=Message
    )


def message_id_of(message) -> int:
    if isinstance(message, dict):
        return message["message_id"]
    return message.message_id


CLOSED_STATE = '{"r":3,"m":"0","e":0,"x":1}'  # a widget message left behind: closed


async def on_demand(bot, db, user: User, batch: DishTidyBatch) -> bool:
    """🍲 Блюда → «Порядок» for a person the switch allows: the batch goes out as a new
    silent widget message, which becomes the batch's message (a widget message of the
    batch sent before is closed with ``x: 1``). False, nothing changed, when it does not
    fit or Telegram would not take it: the caller keeps the text screen."""
    built = build(db, batch, user)
    if built is None:
        return False
    old = (batch.chat_id, batch.message_id) if is_widget(batch) else None
    try:
        message = await send(bot, user.telegram_id, built, keyboard(db, batch, user))
        new_id = message_id_of(message)
    except Exception as error:
        logger.warning(
            "dish tidy: on-demand widget not sent, text kept: %s", type(error).__name__
        )
        return False
    if old and old[0] is not None and old[1] is not None:
        await widget_queries.edit_data(bot, old[0], old[1], CLOSED_STATE)
        try:
            await bot.edit_message_reply_markup(
                chat_id=old[0], message_id=old[1], reply_markup=None
            )
        except TelegramError as error:
            logger.info("dish tidy: old widget keyboard stayed: %s", error)
    batch.chat_id = user.telegram_id
    batch.message_id = new_id
    batch.form = FORM
    batch.state = "sent"
    db.commit()
    return True


async def to_text(bot, db, batch: DishTidyBatch, screen) -> bool:
    """The older form when the widget cannot be drawn (the person asked «💬 Текстом», or
    Telegram refused the widget's edit): ``screen`` as a new silent text message, which
    becomes the batch's message; the widget left behind loses its buttons and, where
    Telegram still takes it, shows closed. False, nothing changed, when the text did not
    go either."""
    old = (batch.chat_id, batch.message_id)
    if None in old:
        return False
    try:
        message = await bot.send_message(
            chat_id=batch.chat_id,
            text=screen.text,
            parse_mode=ParseMode.HTML,
            reply_markup=screen.markup,
            disable_notification=True,
        )
    except TelegramError as error:
        logger.error(
            "dish tidy: batch %s not sent as text either: %s",
            batch.id,
            type(error).__name__,
        )
        return False
    batch.message_id = message_id_of(message)
    batch.form = "text"
    db.commit()
    logger.info("dish tidy: batch %s went from the widget to text", batch.id)
    await widget_queries.edit_data(bot, old[0], old[1], CLOSED_STATE)
    try:
        await bot.edit_message_reply_markup(
            chat_id=old[0], message_id=old[1], reply_markup=None
        )
    except TelegramError as error:
        logger.info("dish tidy: old widget keyboard stayed: %s", error)
    return True
