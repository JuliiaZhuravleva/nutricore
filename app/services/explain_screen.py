"""«ℹ️ Как это работает»: the bot explains itself (stage 5, G5-R22).

    ⚙️ Настройки → [ℹ️ Как это работает] → a list of topics → one topic's screen:
    what it is, how it works, where the idea comes from.

Plain text in the repo: no model call, the same answer for everyone, and the
banned-words scan reads it like any other bot string. Each new feature adds its
entry to ``TOPICS`` in its own slice. The texts are rewritten from
``docs/researches/``, never the research documents themselves, and they say so
where an idea is the project's own and no study tested it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.favorites import Screen
from app.services.food_groups import GROUPS

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "hw:"
OPEN = CALLBACK_PREFIX + "open"
_TOPIC = CALLBACK_PREFIX + "t:"
_SETTINGS = "set:open"  # settings_screen imports this module for its button


@dataclass(frozen=True)
class Topic:
    label: str  # the button and the screen's title
    what: str
    how: str
    why: str
    source: str


_GROUP_WORDS = ", ".join(f"{mark} {word}" for mark, _label, word in GROUPS.values())

TOPICS = {
    "day": Topic(
        label="📊 Карточка дня",
        what="Короткая запись о том, что было за день. Без оценок и без цели.",
        how=(
            "Показывает, когда была еда, а ещё цельные злаки и белковую опору, если "
            "они были. Группы берутся из твоих приёмов пищи. Чего не было, карточка "
            "не называет.\n"
            "Перерыв от шести часов она описывает словами, без счёта часов.\n"
            "Калории и БЖУ лежат под кнопкой «🔢 Цифры». Вес здесь не показывается: "
            "он живёт в «⚖️ Мой вес»."
        ),
        why=(
            "Регулярная еда входит в раннюю часть CBT-E, терапии расстройств пищевого "
            "поведения. В исследовании её вклад виден на снижении приступов "
            "переедания. На карточке это другая задача: не лечение, а спокойная "
            "запись времени. Граница в шесть часов выбрана проектом, точной границы "
            "наука не даёт, и саму карточку никто не проверял."
        ),
        source=(
            "Sivyer и соавт., 2020, International Journal of Eating Disorders: "
            "регулярная еда в CBT-E."
        ),
    ),
    "week": Topic(
        label="📊 Карточка недели",
        what=(
            "Что было за семь дней: разные растения, дни с группами еды, отмеченные "
            "рутины и одна находка, если она есть. Без оценок, цели и сравнения недель."
        ),
        how=(
            "Растения считаются по названиям продуктов с отметкой растительной группы: "
            "гречка дважды — одно растение. Группа, отмеченная вручную, названия не "
            "даёт и в счёт растений не идёт, поэтому число примерное.\n"
            "Находка одна: «снова с записями», если до этого неделя или больше прошла "
            "без записей, иначе продукт, которого раньше в записях не было.\n"
            "Вкладка показывает последние семь дней. Итог приходит в воскресенье в "
            "22:00 без звука за неделю с понедельника и только если записи были. "
            "Выключается в ⚙️ Настройках."
        ),
        why=(
            "Разнообразие растений связывают с разнообразием микробиома кишечника. "
            "Известная цифра «30 растений в неделю» пришла из наблюдения за "
            "добровольцами, это крайняя группа сравнения, а не порог. Поэтому здесь "
            "только счёт, без цели. Какие находки показывать и порог паузы в семь "
            "дней выбраны проектом, саму карточку никто не проверял."
        ),
        source=(
            "McDonald и соавт., 2018, mSystems: American Gut Project; "
            "обзор определений разнообразия растений, 2025."
        ),
    ),
    "groups": Topic(
        label="🏷 Группы еды",
        what=f"Восемь отметок о том, что было в еде: {_GROUP_WORDS}.",
        how=(
            "При разборе бот отмечает группы у каждого продукта. Он может ошибиться: "
            "в черновике их правит кнопка «🏷 Группы», и бот запоминает твой выбор.\n"
            "Опора — заметный источник белка в этом приёме еды. Омега-3 считается "
            "только из растений: лён, чиа, конопля, грецкие орехи.\n"
            "Отметка говорит, что было. Чего не было, бот не называет, и норм здесь нет."
        ),
        why=(
            "Из опросников о качестве питания: вместо граммов там простые вопросы "
            "«было или не было» по группам еды. Такая отметка меньше зависит от того, "
            "насколько точно бот угадал порцию по фото. Набор из восьми групп собран "
            "для этого бота, отдельно его никто не проверял."
        ),
        source=(
            "Herforth, Ballard, Rzepa, 2024, Current Developments in Nutrition: "
            "Diet Quality Questionnaire."
        ),
    ),
    "routines": Topic(
        label="💊 Рутины",
        what="То, что ты решаешь делать регулярно и хочешь отмечать.",
        how=(
            "Название, дни и значок задаёшь ты в ⚙️ Настройках. На карточке дня в "
            "📊 Статистике появляется кнопка: нажми её или напиши название в чат.\n"
            "Рутина раз в неделю ждёт на карточке всю неделю, с понедельника по "
            "воскресенье. День без отметки нигде не считается и не показывается.\n"
            "Бот не предлагает рутины и ничего не советует про добавки."
        ),
        why=(
            "Отметка нажатием не зависит от точности разбора еды, поэтому ей можно "
            "верить. А запись только о том, что было, не делает день без отметки "
            "ошибкой. Это решение проекта, отдельно его никто не проверял."
        ),
        source="отдельного исследования у этой функции нет.",
    ),
}


def list_screen() -> Screen:
    text = (
        "<b>ℹ️ Как это работает</b>\n\n"
        "Коротко о том, что умеет бот: что это, как работает и откуда идея."
    )
    rows = [
        [InlineKeyboardButton(topic.label, callback_data=_TOPIC + key)]
        for key, topic in TOPICS.items()
    ]
    rows.append([InlineKeyboardButton("⬅️ Настройки", callback_data=_SETTINGS)])
    return Screen(text, InlineKeyboardMarkup(rows))


def topic_screen(topic: Topic) -> Screen:
    text = (
        f"<b>{topic.label}</b>\n\n"
        f"<b>Что это.</b> {topic.what}\n\n"
        f"<b>Как работает.</b> {topic.how}\n\n"
        f"<b>Откуда идея.</b> {topic.why}\n\n"
        f"<i>Источник: {topic.source}</i>"
    )
    back = InlineKeyboardButton("⬅️ Как это работает", callback_data=OPEN)
    return Screen(text, InlineKeyboardMarkup([[back]]))


def handle_callback(db, telegram_id: int, data: str) -> Screen:
    if data == OPEN:
        return list_screen()
    topic = TOPICS.get(data[len(_TOPIC) :]) if data.startswith(_TOPIC) else None
    if topic is None:
        # A topic renamed in a release leaves old buttons behind.
        logger.warning("explain: unknown callback data %r", data)
        screen = list_screen()
        screen.toast = "Этой темы уже нет, вот список"
        return screen
    return topic_screen(topic)
