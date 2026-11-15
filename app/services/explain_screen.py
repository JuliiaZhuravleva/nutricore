"""«ℹ️ Как это работает»: the bot explains itself (stage 5, G5-R22).

    ⚙️ Настройки → [ℹ️ Как это работает] → a list of topics → one topic's screen:
    what it is, how it works, where the idea comes from. Beside the topics, a reference
    on vitamins and minerals: what the studies found, with links (G5-R23). It is nearly
    all about vegans, so a person who answered «🍖 Всё» does not get it.

Plain text in the repo: no model call, the same answer for everyone, and the
banned-words scan reads it like any other bot string. Each new feature adds its
entry to ``TOPICS`` in its own slice. The texts are rewritten from
``docs/researches/``, never the research documents themselves, and they say so
where an idea is the project's own and no study tested it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.models.user import User
from app.services import diet
from app.services.favorites import Screen
from app.services.food_groups import GROUPS

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "hw:"
OPEN = CALLBACK_PREFIX + "open"
_TOPIC = CALLBACK_PREFIX + "t:"
NUTRIENTS_OPEN = CALLBACK_PREFIX + "nl"
_NUTRIENT = CALLBACK_PREFIX + "n:"
_NUTRIENTS_ROW = 3
_PAGE = CALLBACK_PREFIX + "p:"
_PAGE_DATA = re.compile(re.escape(_PAGE) + r"(\d+)")
NOP = CALLBACK_PREFIX + "nop"
_LIST_PAGE = 6  # three rows of two, the pager and the back row: 4 + 1 (M15)
_LIST_ROW_LABEL = 20  # the budget of a label at two per row; a longer one stands alone
_SETTINGS = "set:open"  # settings_screen imports this module for its button
# Here, because feedback_screen imports settings_screen, which needs these for its row.
FEEDBACK_OPEN = "fb:open"
FEEDBACK_TITLE = "💬 Фидбэк"
NUTRIENTS_LIST_LABEL = "🧪 Витамины"


@dataclass(frozen=True)
class Topic:
    label: str  # the button and the screen's title
    what: str
    how: str
    why: str
    source: str
    more: bool = False  # a button to the reference on vitamins and minerals
    privacy: str = ""  # where the data goes, right after what it is
    shown: int = 4  # points of ``how`` left open; the rest fold into «Подробнее»


@dataclass(frozen=True)
class Nutrient:
    label: str  # the button
    title: str
    what: str
    found: str
    unknown: str
    sources: tuple  # (what the link is, PMID), read in PubMed, abstracts only


_GROUP_WORDS = " · ".join(f"{mark} {word}" for mark, _label, word in GROUPS.values())
_ROUTINES_REFERENCE = (
    "Что об этих веществах пишут исследования, собрано в справке ниже.\n"
)
_ROUTINES_SOURCE_ALONE = "у самой отметки исследования нет."

# Every text below is a set of points, one per line, each at most two phone lines:
# the screen draws them as a list (never a wall of text, owner 2026-11-15). ``how``
# holds every point; the first ``shown`` stay open, the rest fold into «Подробнее».
TOPICS = {
    "day": Topic(
        label="📊 Карточка дня",
        what="Короткая запись о том, что было за день. Без оценок и без цели.",
        how=(
            "Когда была еда и что это было, словами из записи.\n"
            "Чипсы названы чипсами, без оценки.\n"
            "Цельные злаки и белковая опора идут отдельно, если они были.\n"
            "Чего не было, карточка не называет.\n"
            "Если еда была ночью, линия дня начинается с 0:00.\n"
            "Перерыв от шести часов описан словами, без счёта часов.\n"
            "Сон перерывом не считается.\n"
            "Калории и БЖУ лежат под кнопкой «🔢 Цифры».\n"
            "Вес здесь не показывается: он в ⚙️ Настройки → «⚖️ Вес»."
        ),
        why=(
            "Регулярная еда входит в раннюю часть CBT-E, терапии расстройств "
            "пищевого поведения.\n"
            "В исследовании её вклад виден на снижении приступов переедания.\n"
            "Карточка решает другую задачу: не лечение, а спокойная запись времени.\n"
            "Границу в шесть часов выбрал проект: точной границы наука не даёт.\n"
            "Саму карточку никто не проверял."
        ),
        source=(
            "Sivyer и соавт., 2020, International Journal of Eating Disorders: "
            "регулярная еда в CBT-E."
        ),
    ),
    "week": Topic(
        label="📊 Карточка недели",
        what="Что было за семь дней. Без оценок, цели и сравнения недель.",
        how=(
            "Сколько было разных растений: гречка дважды — одно растение.\n"
            "В какие дни были группы еды.\n"
            "Отмеченные рутины и одна находка, если она есть.\n"
            "Итог приходит в воскресенье в 22:00 без звука, если записи были.\n"
            "Группа, отмеченная вручную, названия не даёт и в счёт растений не "
            "идёт: число примерное.\n"
            "Находка — «снова с записями» после недели без записей, иначе продукт, "
            "которого раньше не было.\n"
            "Вкладка показывает последние семь дней, итог — неделю с понедельника.\n"
            "Итог выключается в ⚙️ Настройках."
        ),
        why=(
            "Разнообразие растений связывают с разнообразием микробиома кишечника.\n"
            "Цифра «30 растений в неделю» — крайняя группа в наблюдении за "
            "добровольцами, а не порог.\n"
            "Поэтому здесь только счёт, без цели.\n"
            "Какие находки показывать и паузу в семь дней выбрал проект.\n"
            "Саму карточку никто не проверял."
        ),
        source=(
            "McDonald и соавт., 2018, mSystems: American Gut Project; "
            "обзор определений разнообразия растений, 2025."
        ),
    ),
    "step": Topic(
        label="👣 Один шаг",
        what=("Одна маленькая добавка к еде. Её выбираешь ты из двух-трёх вариантов."),
        how=(
            "Шаг только добавляет и ничего не убирает.\n"
            "Его можно отметить, сменить или отложить.\n"
            "«😴 Не сейчас» нигде не считается. Два раза подряд — и шаг станет "
            "другим и меньше.\n"
            "«🔋 Силы» переключаешь только ты, сам бот режим не меняет.\n"
            "Пока выбора нет, на карточке только кнопка.\n"
            "Варианты бот берёт из записей: первым то, чего за семь дней было "
            "меньше всего.\n"
            "«Поесть один раз пораньше» появляется, когда первая еда в записях "
            "поздняя.\n"
            "Готовая еда из магазина считается наравне с любой другой.\n"
            "Силы: обычно — до 10 минут, мало — минута-две из готового, минимум — "
            "только если хочется.\n"
            "При «мало» и «минимум» шаг сначала берётся из «🧺 Надёжной еды».\n"
            "Это ⭐ Избранное и то, что было в записях от трёх раз за месяц. Список "
            "правишь ты.\n"
            "Еда из «✋ Не предлагать» в шаги не попадает.\n"
            "Творог, йогурт и яйцо — только при «🍖 Всё» и «🥚 Без мяса».\n"
            "Рыбные консервы — только при «🍖 Всё». Выбор — в ⚙️ Настройках; пока его "
            "нет, шаги только из растений.\n"
            "Счёт на неделе — дни, когда выбранное есть в записях, рядом — неделей "
            "раньше.\n"
            "«✅ Готово» в счёт не идёт. Целей и серий нет, прерваться нечему.\n"
            "Когда дней меньше, бот так и говорит."
        ),
        why=(
            "Конкретные планы работают скромно, и для добавления еды лучше, чем "
            "для отказа.\n"
            "Что один шаг лучше двух-трёх, никто не показал.\n"
            "Что алгоритм справится сам, тоже. Поэтому здесь правила, а не обучение.\n"
            "Уменьшать шаг после двух пропусков решил проект: меньший шаг не "
            "всегда лучше.\n"
            "В одной работе обычная цель сработала лучше, чем «ещё чуть-чуть».\n"
            "Оценки пользы и размера шагов тоже выбрал проект. Сам шаг никто не "
            "проверял.\n"
            "«Поесть пораньше» — догадка: что это уменьшает еду ночью, надёжно не "
            "показано."
        ),
        source=(
            "Carrero и соавт., 2019 (PMID 31125588): планы «если — то»; "
            "метаанализ планов и овощей с фруктами, 2026 (PMID 41987207)."
        ),
    ),
    "groups": Topic(
        label="🏷 Группы еды",
        what=f"Восемь отметок о том, что было в еде:\n{_GROUP_WORDS}",
        how=(
            "Бот сам отмечает группы у каждого продукта и может ошибиться.\n"
            "Поправить: под приёмом «✏️ Исправить», затем «Ещё ›» и «🏷 Группы».\n"
            "Отметка говорит, что было. Чего не было, бот не называет, норм нет.\n"
            "Бот запоминает твою правку.\n"
            "Если блюдо в приёме одно, перед «Ещё ›» нажми «⬅️ К приёму».\n"
            "Опора — заметный источник белка в этом приёме еды.\n"
            "Омега-3 считается из жирной рыбы: лосось, форель, скумбрия, сельдь, "
            "сардины.\n"
            "И из растений: лён, чиа, конопля, грецкие орехи."
        ),
        why=(
            "Из опросников о качестве питания: там вместо граммов простые вопросы "
            "«было или не было».\n"
            "Такая отметка меньше зависит от того, как точно бот угадал порцию по "
            "фото.\n"
            "Набор из восьми групп собран для этого бота, отдельно его никто не "
            "проверял."
        ),
        source=(
            "Herforth, Ballard, Rzepa, 2024, Current Developments in Nutrition: "
            "Diet Quality Questionnaire."
        ),
    ),
    "capture": Topic(
        label="📷 Запись сразу",
        what=(
            "Фото, текст или голосовое — это уже запись. Подтверждения нет, "
            "можно ничего не отвечать."
        ),
        how=(
            "Сначала сохраняется то, что отправлено, потом бот читает и отвечает "
            "одной строкой.\n"
            "Прочитанное можно поправить в любой день. Присланное не меняется.\n"
            "Новое сообщение — новый приём. «🔗 Объединить» соединит его с "
            "прошлым.\n"
            "К приёму сообщение присоединяется само, только если это ответ на него.\n"
            "Или один альбом, или ясные слова вроде «ещё сыр».\n"
            "«➖ Отделить» вернёт как было.\n"
            "Границы вроде трёх часов для таких слов и знак ≈ у «вечером» выбрал "
            "проект, их не проверяли."
        ),
        why=(
            "Подробный дневник быстро бросают, поэтому важнее сохранить сам факт "
            "еды.\n"
            "Прямых исследований «сохранить сразу» против «проверить сначала» не "
            "нашлось: "
            "это решение проекта.\n"
            "Лишнее соединение обходится дороже лишнего разделения, поэтому без "
            "явного знака "
            "приёмы не соединяются."
        ),
        source="PMID 39190910, PMID 31155473",
        privacy=(
            "Сообщения боту не защищены сквозным шифрованием: их может прочитать "
            "тот, кто запускает бота.\n"
            "Сообщение, удалённое в чате, остаётся в дневнике.\n"
            "Убрать запись из дня — 🗑 Удалить на карточке приёма.\n"
            "Сама запись при этом хранится, чтобы её можно было вернуть."
        ),
    ),
    "voice": Topic(
        label="🎙 Голосовые",
        what=(
            "Голосовое записывается как фото. Это проба: качество русских названий "
            "еды пока измеряется."
        ),
        how=(
            "Голосовое сохраняется, затем расшифровывается на сервисе самого "
            "проекта.\n"
            "Кнопка «🎙 Текст» покажет, что услышано.\n"
            "Из оговорки «один… нет, два банана» берётся последнее."
        ),
        why=(
            "В небольшой пробе на 18 человек голосом записывали чаще, чем текстом.\n"
            "С фото это не сравнивали, поэтому голос здесь не считается лучше."
        ),
        source="PMID 37191989",
        privacy=(
            "Если сервис проекта не отвечает, голосовое уходит во внешний сервис: "
            "звук покидает проект."
        ),
    ),
    "routines": Topic(
        label="💊 Рутины",
        what="То, что ты решаешь делать регулярно и хочешь отмечать.",
        how=(
            "Название, дни и значок задаёшь ты в ⚙️ Настройках.\n"
            "На карточке дня в 📊 Статистике будет кнопка: нажми её или напиши "
            "название в чат.\n"
            "День без отметки нигде не считается и не показывается.\n"
            "Бот не предлагает рутины и ничего не советует про добавки.\n"
            "Рутина раз в неделю ждёт на карточке всю неделю, с понедельника по "
            "воскресенье."
        ),
        why=(
            "B12, йод и витамин D по записям еды не оценить: их не видно ни на "
            "фото, ни в названии.\n"
            "А регулярность видно по отметке, и она не зависит от точности "
            "разбора.\n" + _ROUTINES_REFERENCE + "Сама отметка — решение проекта, "
            "отдельно её никто не проверял.\n"
            "День без отметки ошибкой не считается."
        ),
        source="у самой отметки исследования нет, у веществ — в справке.",
        more=True,
    ),
    "meal": Topic(
        label="🍽 Приём и блюда",
        what="Приём состоит из блюд, и каждое можно поправить отдельно.",
        how=(
            "Блюдо в приёме записано таким, каким было в тот день.\n"
            "Правка блюда меняет будущие приёмы, прошлые остаются как были.\n"
            "Удалённый приём не считается на карточках дня и недели.\n"
            "Но он хранится, чтобы его можно было вернуть.\n"
            "Ранние записи не разделены на блюда: цифры у них на всю запись."
        ),
        why=(
            "По фото лучше видно, что было, чем сколько. Поэтому сначала блюдо, "
            "граммы вторыми.\n"
            "Деление на блюда сделал проект, в исследованиях его не проверяли."
        ),
        source="PMID 30060528",
    ),
    "time": Topic(
        label="🕐 Время приёма",
        what="У записи два времени: когда отправлено и когда была еда.",
        how=(
            "Время отправки не меняется.\n"
            "Время еды сначала совпадает с ним. «Вчера», «ночью» или «в 13:20» "
            "его сдвигают.\n"
            "Ответ бота называет, на какое время записано. Знак ≈ значит "
            "«примерно».\n"
            "«Не помню» оставляет приём в том же дне, но без времени.\n"
            "Такой приём есть в списке приёмов, а в строке времён на карточке дня "
            "его нет."
        ),
        why=(
            "По памяти время еды обычно называют позже, чем оно было, поэтому "
            "время не выдумывается."
        ),
        source="PMID 35198846",
    ),
    "reminders": Topic(
        label="🔔 Напоминания",
        what="Короткое сообщение вечером. Отвечать не обязательно.",
        how=(
            "Если что-то из еды вспоминается, можно рассказать голосовым, написать "
            "или нажать на блюдо из ⭐, примерно и не всё.\n"
            "По умолчанию приходит раз в день, в 21:00 по своему поясу, без звука.\n"
            "Время с 20:00 до 23:00, звук и выключение — в ⚙️ Настройки → "
            "🔔 Напоминания.\n"
            "Если были записи за три часа до напоминания, оно не приходит.\n"
            "В воскресенье вечером вместо него приходит итог недели.\n"
            "Блюдо из ⭐ записывается на день напоминания, без часов.\n"
            "Время поправит кнопка 🕐: «утром», «днём», «вечером» или когда ответ "
            "был прислан.\n"
            "Написанное время ставится на день приёма.\n"
            "«Утром каша, в обед суп» запишется как несколько приёмов, каждый со "
            "своим временем.\n"
            "Кнопка ✅ ОК убирает кнопки.\n"
            "Если напоминания подряд остаются без ответа, они приходят реже, а "
            "потом перестают.\n"
            "Они возвращаются после перерыва в записях или кнопкой в настройках.\n"
            "Если записей долго нет, придут одно-два коротких сообщения.\n"
            "Дальше бот молчит, пока не придёт еда."
        ),
        why=(
            "Одним людям напоминания помогают, другим мешают.\n"
            "Поэтому их легко выключить, а без ответа они затихают сами.\n"
            "Сколько напоминаний лучше, исследования не говорят: числа выбрал "
            "проект."
        ),
        source="PMID 42462070, DOI 10.2196/10253; числа — решение проекта.",
    ),
    "dishes": Topic(
        label="🍲 Мои блюда",
        what="Все блюда из записей в одном месте. ⭐ записывается одним нажатием.",
        how=(
            "Блюдо появляется само, когда приходит еда.\n"
            "«🔗 Объединить» соединяет два названия одного блюда.\n"
            "«🗑 Из списков» прячет блюдо.\n"
            "Прошлые приёмы и их цифры не меняются, оба действия отменяются."
        ),
        why=(
            "Повторы в еде здесь не считаются недостатком.\n"
            "Это решение проекта, отдельно оно не проверялось."
        ),
        source="Решение проекта.",
    ),
}

NUTRIENTS_TITLE = "🧪 Витамины и минералы"
_NUTRIENTS_INTRO = (
    "Почему в боте можно отмечать добавки и что о них пишут исследования.\n"
    "• Бот ничего не назначает и не называет доз.\n"
    "• Почти все работы здесь — о веганах и вегетарианцах.\n"
    "• Средние цифры по группе ничего не говорят о конкретном человеке."
)
_NUTRIENTS_POSITION = (
    "Позиция Академии питания и диетологии США, 2025",
    39923894,
)
_NUTRIENTS_ADEQUATE = (
    "По этой позиции продуманный веганский рацион может давать всё нужное "
    "взрослым, кроме времени беременности и кормления."
)
_NUTRIENT_CLOSING = (
    "Это справка, не совет. Принимать ли что-то и сколько — решать тебе и врачу."
)

_REVIEW_2021 = (
    "Обзор 141 работы, 2021. Авторы — сотрудники Unilever, которая продаёт "
    "обогащённые продукты и добавки",
    35010904,
)

NUTRIENTS = {
    "b12": Nutrient(
        label="B12",
        title="💊 Витамин B12",
        what="Участвует в работе крови и нервной системы.\n"
        "В еде без животных продуктов его почти нет, если она не обогащена.",
        found="Метаанализ 17 работ: у веганов B12 в крови ниже, чем у людей, которые "
        "едят мясо.\n"
        "Гомоцистеин, ещё один показатель в крови, у них выше.\n"
        "В подгруппах у веганов с добавками все показатели лучше, чем у веганов без "
        "них, и по статистике это не случайность.\n"
        "Авторы исходят из того, что долгое малое поступление ведёт к дефициту и "
        "рискам для здоровья.",
        unknown="Ещё два показателя при сравнении с мясоедами сдвинуты так же, но "
        "эта разница может быть "
        "случайной.\n"
        "Абстракт не называет дозу и форму.",
        sources=(("Niklewicz и соавт., 2024, метаанализ", 39373282),),
    ),
    "calcium": Nutrient(
        label="Кальций",
        title="🥛 Кальций",
        what="Основа костей.\nПриходит и с едой: обогащённые напитки, тофу с кальцием.",
        found="Обзор 74 работ (о веганах 23): веганы получают кальция меньше, чем "
        "вегетарианцы и те, кто ест мясо.\n"
        "Между вегетарианцами и теми, кто ест мясо, заметной разницы не нашли.\n"
        "Британское наблюдение: переломы бедра у веганов случались чаще, чем у тех, "
        "кто ест мясо.\n"
        "Разница — примерно 15 случаев на 1000 человек за 10 лет (от 8 до 25).",
        unknown="Эти работы не проверяли таблетки.\n"
        "Переломы — наблюдение, а не причина.\n"
        "Учёт кальция и белка лишь немного менял разницу, без поправки на вес она была "
        "больше.\n"
        "Авторы пишут, что нужны новые исследования.",
        sources=(
            ("Bickelmann и соавт., 2023, метаанализ", 38054787),
            ("Tong и соавт., 2020, EPIC-Oxford", 33222682),
        ),
    ),
    "iodine": Nutrient(
        label="Йод",
        title="🧂 Йод",
        what="Нужен щитовидной железе.\n"
        "Много зависит от страны: где соль обычно йодируют, его получают незаметно.",
        found="Обзор 11 работ (4421 взрослый): у веганов самые низкие поступление и "
        "йод в моче.\n"
        "Медиана йода в моче ни в одной группе питания не попала в диапазон ВОЗ.\n"
        "Разница в поступлении зависела от пола, йодирования соли и обеспеченности "
        "страны йодом.",
        unknown="Работа сравнивает поступление и анализы, а не проверяет добавки.",
        sources=(("Eveleigh и соавт., 2023, метаанализ", 36912094),),
    ),
    "vitamin_d": Nutrient(
        label="Витамин D",
        title="☀️ Витамин D",
        what="Участвует в усвоении кальция.\n"
        "Образуется в коже на солнце: многое зависит от широты и времени года.",
        found="Обзор 141 работы о питании в целом.\n"
        "У людей на растительном питании поступление и уровень в крови обычно ниже, "
        "чем у тех, кто ест мясо.\n"
        "Риск малого поступления есть и у тех, кто ест мясо.",
        unknown="В абстракте нет данных о том, что будет от добавки.",
        sources=(_REVIEW_2021,),
    ),
    "omega": Nutrient(
        label="Омега-3",
        title="🌰 Омега-3 (EPA и DHA)",
        what="Жирные кислоты.\n"
        "В льне и орехах другая форма, ALA, и в EPA и DHA она превращается слабо.",
        found="Картирующий обзор 13 работ: высокие дозы льняного масла и масла эхиума "
        "не подняли индекс омега-3.\n"
        "В части работ он даже снизился.\n"
        "Масло микроводорослей подняло его во всех работах.\n"
        "Авторы называют свой вывод предварительным.",
        unknown="Индекс — показатель в крови, а не здоровье.\n"
        "Официальные рекомендации к единому мнению пока не пришли.",
        sources=(("Lane и соавт., 2022, обзор", 33576691),),
    ),
    "iron": Nutrient(
        label="Железо",
        title="🩸 Железо",
        what="Участвует в переносе кислорода.\n"
        "Из растений усваивается меньшая его доля, чем из мяса.",
        found="Метаанализ 24 поперечных работ: ферритин, запас железа, у вегетарианцев "
        "ниже, чем у остальных.\n"
        "Разница — примерно 30 мкг/л.\n"
        "У мужчин разница больше, чем у женщин.",
        unknown="Это снимок, а не причина.\n"
        "Авторы считают высокий запас тоже фактором риска, например диабета 2 типа.\n"
        "Они предлагают контролировать статус железа не только вегетарианцам.\n"
        "По записям еды запас не оценить.",
        sources=(("Haider и соавт., 2018, метаанализ", 27880062),),
    ),
    "zinc": Nutrient(
        label="Цинк",
        title="🌱 Цинк",
        what="Участвует в работе иммунитета и заживлении.\n"
        "Есть в бобовых, орехах и семечках.",
        found="Обзор 141 работы о питании в целом.\n"
        "У людей на растительном питании поступление и уровень цинка обычно ниже, "
        "чем у тех, кто ест мясо.",
        unknown="В абстракте о проверке добавок нет ни слова, а источник у этого "
        "экрана один.\n"
        "Дни бобовых и орехов в боте — примета, а не оценка цинка.",
        sources=(_REVIEW_2021,),
    ),
}


def _list_rows(buttons: list) -> list:
    """Two per row, in order; a label past the two-per-row budget stands alone."""
    rows = []
    for button in buttons:
        last = rows[-1] if rows else None
        fits = last is not None and len(last) == 1
        if fits and max(len(last[0].text), len(button.text)) <= _LIST_ROW_LABEL:
            last.append(button)
        else:
            rows.append([button])
    return rows


def list_screen(page: int = 0, nutrients: bool = True) -> Screen:
    text = "<b>ℹ️ Как это работает</b>"
    entries = [
        InlineKeyboardButton(topic.label, callback_data=_TOPIC + key)
        for key, topic in TOPICS.items()
    ]
    if nutrients:
        # The full title would stand alone and push the page past its five rows.
        entries.append(
            InlineKeyboardButton(NUTRIENTS_LIST_LABEL, callback_data=NUTRIENTS_OPEN)
        )
    # Last in the list: the first page is what most people came for, and full.
    entries.append(InlineKeyboardButton(FEEDBACK_TITLE, callback_data=FEEDBACK_OPEN))
    pages = (len(entries) + _LIST_PAGE - 1) // _LIST_PAGE
    page %= pages
    rows = _list_rows(entries[page * _LIST_PAGE : (page + 1) * _LIST_PAGE])
    if pages > 1:
        rows.append(
            [
                InlineKeyboardButton("◀️", callback_data=f"{_PAGE}{(page - 1) % pages}"),
                InlineKeyboardButton(f"{page + 1}/{pages}", callback_data=NOP),
                InlineKeyboardButton("▶️", callback_data=f"{_PAGE}{(page + 1) % pages}"),
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ Настройки", callback_data=_SETTINGS)])
    return Screen(text, InlineKeyboardMarkup(rows))


def _without_reference(topic: Topic) -> Topic:
    """The topic with no word about a reference this person does not get."""
    return replace(
        topic,
        why=topic.why.replace(_ROUTINES_REFERENCE, ""),
        source=_ROUTINES_SOURCE_ALONE,
        more=False,
    )


def _points(lines: list) -> str:
    return "\n".join(f"• {line}" for line in lines)


def _fold(body: str) -> str:
    """Telegram shows the first lines of it, the rest after a tap."""
    return f"<blockquote expandable>{body}</blockquote>"


def topic_screen(topic: Topic, nutrients: bool = True) -> Screen:
    """A title, one short line of what it is, then points: never a wall of text.

    The first ``shown`` points of how it works stay open, the rest and where the
    idea comes from fold into quotes a person opens if they want more.
    """
    if topic.more and not nutrients:
        topic = _without_reference(topic)
    how = topic.how.split("\n")
    if len(how) > topic.shown + 1:  # folding a single point saves nothing
        how, rest = how[: topic.shown], how[topic.shown :]
    else:
        rest = []
    parts = [f"<b>{topic.label}</b>\n{topic.what}"]
    if topic.privacy:
        parts.append(f"<b>Приватность</b>\n{_points(topic.privacy.split("\n"))}")
    parts.append(f"<b>Как работает</b>\n{_points(how)}")
    if rest:
        parts.append(_fold(f"<b>Подробнее</b>\n{_points(rest)}"))
    parts.append(
        _fold(
            f"<b>Откуда идея</b>\n{_points(topic.why.split("\n"))}\n"
            f"<i>Источник: {topic.source}</i>"
        )
    )
    text = "\n\n".join(parts)
    back = InlineKeyboardButton("⬅️ Как это работает", callback_data=OPEN)
    rows = [[back]]
    if topic.more:
        rows.insert(
            0, [InlineKeyboardButton(NUTRIENTS_TITLE, callback_data=NUTRIENTS_OPEN)]
        )
    return Screen(text, InlineKeyboardMarkup(rows))


def _link(source: tuple) -> str:
    name, pmid = source
    return f'<a href="https://pubmed.ncbi.nlm.nih.gov/{pmid}/">{name}</a>'


def nutrients_screen() -> Screen:
    text = (
        f"<b>{NUTRIENTS_TITLE}</b>\n{_NUTRIENTS_INTRO}\n\n"
        f"<b>Общая позиция</b>\n{_NUTRIENTS_ADEQUATE}\n"
        f"<i>Источник: {_link(_NUTRIENTS_POSITION)}.</i>"
    )
    buttons = [
        InlineKeyboardButton(item.label, callback_data=_NUTRIENT + key)
        for key, item in NUTRIENTS.items()
    ]
    rows = [
        buttons[i : i + _NUTRIENTS_ROW] for i in range(0, len(buttons), _NUTRIENTS_ROW)
    ]
    rows.append([InlineKeyboardButton("⬅️ Как это работает", callback_data=OPEN)])
    return Screen(text, InlineKeyboardMarkup(rows))


def nutrient_screen(item: Nutrient) -> Screen:
    """What it is in a line or two, then what was found and what is not known as
    points; the sources fold into a quote under them."""
    found = _points(item.found.split("\n"))
    unknown = _points(item.unknown.split("\n"))
    links = "\n".join(f"• {_link(source)}" for source in item.sources)
    sources = _fold(f"<b>Источники</b>\n{links}")
    text = (
        f"<b>{item.title}</b>\n{item.what}\n\n"
        f"<b>Что нашли</b>\n{found}\n\n"
        f"<b>Чего не знаем</b>\n{unknown}\n"
        f"{sources}\n"
        f"<i>{_NUTRIENT_CLOSING}</i>"
    )
    back = InlineKeyboardButton(
        f"⬅️ {NUTRIENTS_TITLE.split(' ', 1)[1]}", callback_data=NUTRIENTS_OPEN
    )
    return Screen(text, InlineKeyboardMarkup([[back]]))


def _shows_nutrients(db, telegram_id: int) -> bool:
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    return diet.shows_nutrients((user.settings or {}) if user else {})


def handle_callback(db, telegram_id: int, data: str) -> Screen:
    nutrients = _shows_nutrients(db, telegram_id)
    if data == OPEN:
        return list_screen(nutrients=nutrients)
    if data == NOP:
        return Screen("", None)  # the pager's middle: nothing to do
    turned = _PAGE_DATA.fullmatch(data)
    if turned:
        return list_screen(int(turned.group(1)), nutrients)
    reference = data == NUTRIENTS_OPEN or data.startswith(_NUTRIENT)
    if reference and not nutrients:
        # A button from before the answer changed.
        screen = list_screen(nutrients=False)
        screen.toast = "Этой справки здесь нет, вот список"
        return screen
    if data == NUTRIENTS_OPEN:
        return nutrients_screen()
    if data.startswith(_NUTRIENT) and data[len(_NUTRIENT) :] in NUTRIENTS:
        return nutrient_screen(NUTRIENTS[data[len(_NUTRIENT) :]])
    topic = TOPICS.get(data[len(_TOPIC) :]) if data.startswith(_TOPIC) else None
    if topic is None:
        # A topic renamed in a release leaves old buttons behind.
        logger.warning("explain: unknown callback data %r", data)
        screen = list_screen(nutrients=nutrients)
        screen.toast = "Этой темы уже нет, вот список"
        return screen
    return topic_screen(topic, nutrients)
