"""R0 frozen: one table of what the time words of a message say, and the proof that
R0 changed nothing but the rows it is about.

Every row is a text, the local clock it was sent at on пт 6 ноября 2026 in
Asia/Tbilisi, ``bare`` when a bare time is allowed (a caption, a reply), how many of its
clauses say a time, what ``time_words`` gave before R0 (``dcb225c``, generated from that
module and hard-coded), and, only where R0's rule applies, what it gives now:

- ``-`` none: the meal keeps the moment it was sent;
- ``exact 11-06 19:30 + [в 19:30] «паста»``: precision, local moment (``day 11-05``: the
  day and no clock), ``of_day`` (``+``/``-``), the phrase, and what is left of the text
  (``=``: the whole text).

R0's rule: two or more clauses that each say a time, and those times differ, give the
day and no clock. In that rule alone a clause with a meal's name and no time phrase
says a time too (stage 6, D2: «утром овсянка, в обед суп»); the column of clauses with a
time still counts time phrases only. Every other row is the pre-R0 result, untouched.
Pure: no rows, no model. A change of a row here is a change of behaviour."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.meal_words import _MEAL_NAME, DAY_ONLY, clauses, time_words

ZONE = ZoneInfo("Asia/Tbilisi")  # UTC+4

# text | sent | bare | clauses with a time | before R0 | R0, where the rule applies
TABLE = """
# --- one clock, one hour, «назад»
в 19:30 паста | 21:00 |  | 1 | exact 11-06 19:30 + [в 19:30] «паста»
в 13 суп | 21:00 |  | 1 | exact 11-06 13:00 + [в 13] «суп»
в 13 суп | 13:20 |  | 1 | exact 11-06 13:00 + [в 13] «суп»
в 13 | 13:20 | bare | 1 | exact 11-06 13:00 + [в 13] «»
в 13:10 | 13:20 | bare | 1 | exact 11-06 13:10 + [в 13:10] «»
в 14:10 | 13:20 | bare | 1 | exact 11-05 14:10 + [в 14:10] «»
в 12:10 | 13:20 | bare | 1 | exact 11-06 12:10 + [в 12:10] «»
в 12 | 13:20 | bare | 1 | exact 11-06 12:00 + [в 12] «»
в 8:00 каша | 10:30 |  | 1 | exact 11-06 08:00 + [в 8:00] «каша»
в 8:00 каша | 07:00 |  | 1 | exact 11-05 08:00 + [в 8:00] «каша»
сегодня в 9:00 каша | 10:00 |  | 1 | exact 11-06 09:00 + [в 9:00] «сегодня каша»
вчера 23:10 | 13:20 | bare | 1 | exact 11-05 23:10 - [вчера 23:10] «»
вчера суп в 13:00 | 21:00 |  | 1 | exact 11-06 13:00 + [в 13:00] «вчера суп»
час назад суп | 13:20 |  | 1 | approx 11-06 12:20 - [час назад] «суп»
час назад | 13:20 | bare | 1 | approx 11-06 12:20 - [час назад] «»
суп полчаса назад | 13:20 |  | 1 | approx 11-06 12:50 - [полчаса назад] «суп»
ещё сыр час назад | 13:20 |  | 1 | approx 11-06 12:20 - [час назад] «ещё сыр»
# --- not time words
вчерашний суп | 13:20 |  | 0 | -
в 3 раза больше супа | 13:20 |  | 0 | -
3 часа назад суп | 13:20 |  | 0 | -
суп с хлебом | 21:00 |  | 0 | -
обеденный стол | 21:00 |  | 0 | -
суп обед | 21:00 |  | 0 | -
ужин из пасты | 21:00 |  | 0 | -
завтраки | 21:00 |  | 0 | -
обедом | 21:00 |  | 0 | -
# --- a time word alone is no meal, unless bare
утром | 13:20 |  | 1 | -
# --- a time word alone is no meal, unless bare
утром | 13:20 | bare | 1 | approx 11-06 09:00 + [утром] «»
вчера | 13:20 | bare | 1 | day 11-05 - [вчера] «»
вечером | 21:00 | bare | 1 | approx 11-06 20:00 + [вечером] «»
вечером | 18:30 | bare | 1 | approx 11-05 20:00 + [вечером] «»
утром | 08:00 | bare | 1 | approx 11-05 09:00 + [утром] «»
в обед | 11:30 | bare | 0 | -
на обед | 12:40 | bare | 0 | -
# --- «вчера» with a part, the night, a meal's name (no time before R0)
вчера суп | 13:20 |  | 1 | day 11-05 - [вчера] «суп»
вчера утром каша | 21:00 |  | 1 | approx 11-05 09:00 - [вчера утром] «каша»
Вчера утром суп | 13:20 |  | 1 | approx 11-05 09:00 - [Вчера утром] «суп»
вчера вечером суп | 13:20 |  | 1 | approx 11-05 20:00 - [вчера вечером] «суп»
суп вчера днём | 13:20 |  | 1 | approx 11-05 14:00 - [вчера днём] «суп»
вчера на обед суп | 21:00 |  | 1 | day 11-05 - [вчера] «на обед суп»
вчера паста на ужин | 21:00 |  | 1 | day 11-05 - [вчера] «паста на ужин»
вчера на ужин суп | 16:30 |  | 1 | day 11-05 - [вчера] «на ужин суп»
вчера на завтрак каша | 06:30 |  | 1 | day 11-05 - [вчера] «на завтрак каша»
суп в 2 вчера утром | 21:00 |  | 1 | approx 11-05 09:00 - [вчера утром] «суп в 2»
вчера ночью бутерброд | 10:00 |  | 1 | day 11-05 - [вчера] «ночью бутерброд»
вчера ночью бутерброд | 21:00 |  | 1 | day 11-05 - [вчера] «ночью бутерброд»
вчера ночью бутерброд | 00:30 |  | 1 | day 11-05 - [вчера] «ночью бутерброд»
бутерброд вчера ночью | 13:20 |  | 1 | day 11-05 - [вчера] «бутерброд ночью»
вчера ночью | 10:00 | bare | 1 | day 11-05 - [вчера] «ночью»
вчера ночью в 23:30 бутерброд | 10:00 |  | 1 | exact 11-05 23:30 + [в 23:30] «вчера ночью бутерброд»
вчера бутерброд ночью | 10:00 |  | 1 | day 11-05 - [вчера] «бутерброд ночью»
вчера ел ночью бутерброд | 10:00 |  | 1 | day 11-05 - [вчера] «ел ночью бутерброд»
вчера поздно ночью бутерброд | 10:00 |  | 1 | day 11-05 - [вчера] «поздно ночью бутерброд»
бутерброд вчера, ночью | 10:00 |  | 2 | day 11-05 - [вчера] «бутерброд , ночью» | day 11-05 + [None] =
вчера ночью в 2:00 бутерброд | 10:00 |  | 1 | exact 11-06 02:00 + [в 2:00] «вчера ночью бутерброд»
# --- the night
ночью суп | 13:20 |  | 1 | approx 11-06 03:00 - [ночью] «суп»
ночью бутерброд | 10:00 |  | 1 | approx 11-06 03:00 - [ночью] «бутерброд»
ночью бутерброд | 00:30 |  | 1 | approx 11-05 03:00 - [ночью] «бутерброд»
ночью бутерброд | 02:00 |  | 1 | approx 11-05 03:00 - [ночью] «бутерброд»
ночью бутерброд | 23:50 |  | 1 | approx 11-06 03:00 - [ночью] «бутерброд»
ночью в 2:00 бутерброд | 10:00 |  | 1 | exact 11-06 02:00 + [в 2:00] «ночью бутерброд»
# --- a part said live: before R0 the latest one, even yesterday's (pre-R0 debt)
утром каша | 06:30 |  | 1 | approx 11-05 09:00 + [утром] «каша»
утром каша | 08:30 |  | 1 | approx 11-05 09:00 + [утром] «каша»
утром каша | 12:40 |  | 1 | approx 11-06 09:00 + [утром] «каша»
утром каша | 13:20 |  | 1 | approx 11-06 09:00 + [утром] «каша»
утром каша | 23:50 |  | 1 | approx 11-06 09:00 + [утром] «каша»
утром каша | 00:30 |  | 1 | approx 11-05 09:00 + [утром] «каша»
хлеб утром | 13:20 |  | 1 | approx 11-06 09:00 + [утром] «хлеб»
на завтрак каша | 06:30 |  | 0 | -
на завтрак каша | 08:30 |  | 0 | -
на завтрак овсянка | 08:30 |  | 0 | -
на завтрак каша | 12:40 |  | 0 | -
на завтрак каша | 00:30 |  | 0 | -
каша за завтраком | 21:00 |  | 0 | -
в обед суп | 06:30 |  | 0 | -
в обед суп | 10:00 |  | 0 | -
в обед суп | 12:40 |  | 0 | -
в обед суп | 13:20 |  | 0 | -
в обед суп | 16:30 |  | 0 | -
в обед суп | 00:30 |  | 0 | -
В  Обед суп | 21:00 |  | 0 | -
на обед суп | 21:00 |  | 0 | -
суп за обедом | 21:00 |  | 0 | -
днём суп | 13:20 |  | 1 | approx 11-05 14:00 + [днём] «суп»
днём суп | 08:30 |  | 1 | approx 11-05 14:00 + [днём] «суп»
на полдник йогурт | 21:00 |  | 0 | -
на полдник йогурт | 12:40 |  | 0 | -
на ужин суп | 06:30 |  | 0 | -
на ужин паста | 07:00 |  | 0 | -
на ужин суп | 12:40 |  | 0 | -
на ужин суп | 16:30 |  | 0 | -
на ужин паста | 18:40 |  | 0 | -
на ужин суп | 23:50 |  | 0 | -
на ужин суп | 00:30 |  | 0 | -
паста за ужином | 21:00 |  | 0 | -
вечером паста | 16:30 |  | 1 | approx 11-05 20:00 + [вечером] «паста»
вечером паста | 18:40 |  | 1 | approx 11-05 20:00 + [вечером] «паста»
вечером паста | 19:00 |  | 1 | approx 11-05 20:00 + [вечером] «паста»
вечером паста | 12:40 |  | 1 | approx 11-05 20:00 + [вечером] «паста»
вечером паста | 23:50 |  | 1 | approx 11-06 20:00 + [вечером] «паста»
суп, вечером | 13:20 |  | 1 | approx 11-05 20:00 + [вечером] «суп»
сегодня утром каша | 06:30 |  | 1 | approx 11-05 09:00 + [утром] «сегодня каша»
сегодня утром каша | 21:00 |  | 1 | approx 11-06 09:00 + [утром] «сегодня каша»
сегодня на ужин суп | 12:40 |  | 0 | -
на ужин сегодня суп | 07:00 |  | 0 | -
сегодня в обед суп | 08:00 |  | 0 | -
сегодня на полдник йогурт | 08:00 |  | 0 | -
сегодня вечером паста | 12:40 |  | 1 | approx 11-05 20:00 + [вечером] «сегодня паста»
# --- a clock with a part in one clause: the clock
утром в 8:30 овсянка | 21:00 |  | 1 | exact 11-06 08:30 + [в 8:30] «утром овсянка»
овсянка утром в 8:30 | 21:00 |  | 1 | exact 11-06 08:30 + [в 8:30] «овсянка утром»
вечером в 19:30 паста | 21:00 |  | 1 | exact 11-06 19:30 + [в 19:30] «вечером паста»
на ужин в 19:30 паста | 21:00 |  | 1 | exact 11-06 19:30 + [в 19:30] «на ужин паста»
на ужин паста в 19:30 | 21:00 |  | 1 | exact 11-06 19:30 + [в 19:30] «на ужин паста»
на обед в 13:30 суп | 21:00 |  | 1 | exact 11-06 13:30 + [в 13:30] «на обед суп»
на обед суп в 13:30 | 21:00 |  | 1 | exact 11-06 13:30 + [в 13:30] «на обед суп»
утром, в 12:00 каша | 21:00 |  | 2 | exact 11-06 12:00 + [в 12:00] «утром, каша» | day 11-06 + [None] =
на завтрак в 8:00 овсянка | 08:30 |  | 1 | exact 11-06 08:00 + [в 8:00] «на завтрак овсянка»
вчера вечером в 20:00 паста | 10:00 |  | 1 | exact 11-05 20:00 + [в 20:00] «вчера вечером паста»
вчера вечером в 20:00 паста | 21:00 |  | 1 | exact 11-06 20:00 + [в 20:00] «вчера вечером паста»
# --- two clauses
утром каша, в 19:30 паста | 21:00 |  | 2 | exact 11-06 19:30 + [в 19:30] «утром каша, паста» | day 11-06 + [None] =
утром каша, в 19:30 паста | 10:00 |  | 2 | exact 11-05 19:30 + [в 19:30] «утром каша, паста» | day 11-06 + [None] =
утром каша в 19:30 паста | 21:00 |  | 1 | exact 11-06 19:30 + [в 19:30] «утром каша паста»
в обед суп, а вечером паста | 21:00 |  | 1 | approx 11-06 20:00 + [вечером] «в обед суп, а паста» | day 11-06 + [None] =
в обед суп, а вечером паста | 10:00 |  | 1 | approx 11-05 20:00 + [вечером] «в обед суп, а паста» | day 11-05 + [None] =
в обед суп, а вечером паста | 07:00 |  | 1 | approx 11-05 20:00 + [вечером] «в обед суп, а паста» | day 11-05 + [None] =
утром каша, в обед суп | 21:00 |  | 1 | approx 11-06 09:00 + [утром] «каша, в обед суп» | day 11-06 + [None] =
утром каша, в обед суп | 13:20 |  | 1 | approx 11-06 09:00 + [утром] «каша, в обед суп» | day 11-06 + [None] =
утром каша, в обед суп | 09:30 |  | 1 | approx 11-06 09:00 + [утром] «каша, в обед суп» | day 11-06 + [None] =
на ужин суп, вечером чай | 18:40 |  | 1 | approx 11-05 20:00 + [вечером] «на ужин суп, чай»
на завтрак каша, в обед суп | 06:30 |  | 0 | - | day 11-05 + [None] =
в обед суп, в 15:00 чай | 21:00 |  | 1 | exact 11-06 15:00 + [в 15:00] «в обед суп, чай» | day 11-06 + [None] =
в обед суп и в 16:00 чай | 21:00 |  | 1 | exact 11-06 16:00 + [в 16:00] «в обед суп и чай» | day 11-06 + [None] =
утром каша, в 10:30 йогурт | 21:00 |  | 2 | exact 11-06 10:30 + [в 10:30] «утром каша, йогурт» | day 11-06 + [None] =
днём суп, полдник в 17:00 | 21:00 |  | 2 | exact 11-06 17:00 + [в 17:00] «днём суп, полдник» | day 11-06 + [None] =
на обед суп, в 16:30 печенье | 18:00 |  | 1 | exact 11-06 16:30 + [в 16:30] «на обед суп, печенье» | day 11-06 + [None] =
на обед суп, в 13:30 чай | 21:00 |  | 1 | exact 11-06 13:30 + [в 13:30] «на обед суп, чай» | day 11-06 + [None] =
на ужин паста, в 19:30 чай | 21:00 |  | 1 | exact 11-06 19:30 + [в 19:30] «на ужин паста, чай» | day 11-06 + [None] =
утром каша, утром кофе | 21:00 |  | 2 | approx 11-06 09:00 + [утром] «каша, утром кофе»
утром каша, утром кофе | 08:30 |  | 2 | approx 11-05 09:00 + [утром] «каша, утром кофе»
утром каша, в 13:00 суп | 13:20 |  | 2 | exact 11-06 13:00 + [в 13:00] «утром каша, суп» | day 11-06 + [None] =
в 8:00 каша, в 10:00 кофе | 10:30 |  | 2 | exact 11-06 08:00 + [в 8:00] «каша, в 10:00 кофе» | day 11-06 + [None] =
# --- «вчера» and «сегодня» across clauses
вчера утром каша, вечером суп | 21:00 |  | 2 | approx 11-05 09:00 - [вчера утром] «каша, вечером суп» | day 11-05 + [None] =
вчера каша, утром суп | 21:00 |  | 2 | day 11-05 - [вчера] «каша, утром суп» | day 11-05 + [None] =
вчера суп, утром каша | 10:00 |  | 2 | day 11-05 - [вчера] «суп, утром каша» | day 11-05 + [None] =
вчера пицца, сегодня утром каша | 10:00 |  | 2 | day 11-05 - [вчера] «пицца, сегодня утром каша» | day 11-06 + [None] =
вчера суп, сегодня в 9:00 каша | 10:00 |  | 2 | exact 11-06 09:00 + [в 9:00] «вчера суп, сегодня каша» | day 11-06 + [None] =
на завтрак каша, вчера в 22:00 торт | 21:00 |  | 1 | exact 11-05 22:00 - [вчера в 22:00] «на завтрак каша, торт» | day 11-05 + [None] =
вчера в обед суп, сегодня на завтрак каша | 10:00 |  | 1 | day 11-05 - [вчера] «в обед суп, сегодня на завтрак каша» | day 11-06 + [None] =
вчера суп, ночью бутерброд | 10:00 |  | 2 | day 11-05 - [вчера] «суп, ночью бутерброд» | day 11-05 + [None] =
# --- the clause edges, and more two-clause rows
утром каша — вечером суп | 21:00 |  | 2 | approx 11-06 09:00 + [утром] «каша — вечером суп» | day 11-06 + [None] =
утром каша и вечером суп | 21:00 |  | 2 | approx 11-06 09:00 + [утром] «каша и вечером суп» | day 11-06 + [None] =
утром каша а вечером суп | 21:00 |  | 2 | approx 11-06 09:00 + [утром] «каша а вечером суп» | day 11-06 + [None] =
суп; вечером паста, утром каша | 21:00 |  | 2 | approx 11-06 20:00 + [вечером] «суп; паста, утром каша» | day 11-06 + [None] =
рис и курица в 13:00 | 21:00 |  | 1 | exact 11-06 13:00 + [в 13:00] «рис и курица»
рис и курица утром | 21:00 |  | 1 | approx 11-06 09:00 + [утром] «рис и курица»
вчера утром каша, вчера вечером суп | 21:00 |  | 2 | approx 11-05 09:00 - [вчера утром] «каша, вчера вечером суп» | day 11-05 + [None] =
сегодня утром каша, вчера вечером суп | 21:00 |  | 2 | approx 11-05 20:00 - [вчера вечером] «сегодня утром каша, суп» | day 11-06 + [None] =
вчера утром каша, сегодня вечером суп | 21:00 |  | 2 | approx 11-05 09:00 - [вчера утром] «каша, сегодня вечером суп» | day 11-06 + [None] =
в 8:00 каша, в 8:00 кофе | 10:30 |  | 2 | exact 11-06 08:00 + [в 8:00] «каша, в 8:00 кофе»
в 8:00 каша,в 10:00 кофе | 10:30 |  | 2 | exact 11-06 08:00 + [в 8:00] «каша,в 10:00 кофе» | day 11-06 + [None] =
суп, в 13 | 21:00 |  | 1 | exact 11-06 13:00 + [в 13] «суп»
вчерашний суп, утром каша | 21:00 |  | 1 | approx 11-06 09:00 + [утром] «вчерашний суп, каша»
утром, вечером | 21:00 | bare | 2 | approx 11-06 09:00 + [утром] «вечером» | day 11-06 + [None] =
Утром каша, ВЕЧЕРОМ суп | 21:00 |  | 2 | approx 11-06 09:00 + [Утром] «каша, ВЕЧЕРОМ суп» | day 11-06 + [None] =
ночью бутерброд, утром каша | 10:00 |  | 2 | approx 11-06 03:00 - [ночью] «бутерброд, утром каша» | day 11-06 + [None] =
час назад суп, утром каша | 13:20 |  | 2 | approx 11-06 12:20 - [час назад] «суп, утром каша» | day 11-06 + [None] =
каша, в обед суп | 21:00 |  | 0 | -
вчера суп и вчера каша | 21:00 |  | 2 | day 11-05 - [вчера] «суп и вчера каша»
вчера ночью бутерброд, утром каша | 10:00 |  | 2 | day 11-05 - [вчера] «ночью бутерброд, утром каша» | day 11-05 + [None] =
в 19:30 паста, а утром каша | 21:00 |  | 2 | exact 11-06 19:30 + [в 19:30] «паста, а утром каша» | day 11-06 + [None] =
вчера в 22:00 торт, в 9:00 каша | 10:00 |  | 2 | exact 11-05 22:00 - [вчера в 22:00] «торт, в 9:00 каша» | day 11-05 + [None] =
утром каша, вечером | 21:00 |  | 2 | approx 11-06 09:00 + [утром] «каша, вечером» | day 11-06 + [None] =
"""


def _rows(table: str) -> list:
    rows = []
    for line in table.strip().splitlines():
        if line.startswith("#"):
            continue
        cells = [cell.strip() for cell in line.split("|")]
        text, sent, bare, timed, before = cells[:5]
        rule = cells[5] if len(cells) > 5 else None
        rows.append((text, sent, bare == "bare", int(timed), before, rule))
    return rows


ROWS = _rows(TABLE)


def _sent(clock: str) -> datetime:
    hour, minute = map(int, clock.split(":"))
    return datetime(2026, 11, 6, hour, minute, tzinfo=ZONE)


def render(said, text: str) -> str:
    """The row's form of a ``SaidTime``: every one of its fields."""
    if said is None:
        return "-"
    local = said.moment.astimezone(ZONE)
    if said.precision == DAY_ONLY:
        when = f"day {local:%m-%d}"
    else:
        when = f"{said.precision} {local:%m-%d %H:%M}"
    rest = "=" if said.rest == text else f"«{said.rest}»"
    return f"{when} {'+' if said.of_day else '-'} [{said.phrase}] {rest}"


def _now(text, sent, bare_ok) -> str:
    return render(time_words(text, _sent(sent), ZONE, bare_ok=bare_ok), text)


@pytest.mark.parametrize("text,sent,bare_ok,timed,before,rule", ROWS)
def test_time_phrase_corpus(text, sent, bare_ok, timed, before, rule):
    assert _now(text, sent, bare_ok) == (rule or before)


def _named(text: str) -> int:
    """Clauses the rule counts for a meal's name: no time phrase, a meal's name."""
    return sum(
        time_words(one, _sent("12:00"), ZONE, bare_ok=True) is None
        and _MEAL_NAME.search(one) is not None
        for one in clauses(text)
    )


def test_no_row_of_fewer_than_two_timed_clauses_changed_since_before_r0():
    single = [row for row in ROWS if row[3] + _named(row[0]) < 2]
    assert single  # a known positive: most rows are such
    assert [row for row in single if row[5] is not None] == []
    changed = [row for row in single if _now(*row[:3]) != row[4]]
    assert changed == []


def test_a_meals_name_changes_only_rows_of_two_clauses_that_say_a_time():
    named = [row for row in ROWS if row[3] < 2 and row[5] is not None]
    assert named  # «утром каша, в обед суп»
    for text, _sent_at, _bare, timed, _before, _rule in named:
        assert _named(text) >= 1 and timed + _named(text) >= 2, text


def test_the_rule_only_ever_gives_the_day_and_no_clock():
    ruled = [row for row in ROWS if row[5] is not None]
    assert ruled  # a known positive
    for text, sent, _bare, timed, before, rule in ruled:
        assert timed + _named(text) >= 2, text
        assert rule.startswith("day ") and rule.endswith(" + [None] ="), text
        assert rule != before, text


def test_rows_of_two_timed_clauses_that_agree_are_as_before_r0():
    agreeing = [row for row in ROWS if row[3] >= 2 and row[5] is None]
    assert agreeing  # «утром каша, утром кофе»
    assert [row for row in agreeing if _now(*row[:3]) != row[4]] == []


@pytest.mark.parametrize("text,sent,bare_ok,timed,before,rule", ROWS)
def test_the_timed_clause_count_is_the_resolvers(
    text, sent, bare_ok, timed, before, rule
):
    said = [time_words(one, _sent(sent), ZONE, bare_ok=True) for one in clauses(text)]
    assert sum(one is not None for one in said) == timed


def test_the_corpus_has_no_row_twice():
    keys = [row[:3] for row in ROWS]
    assert len(keys) == len(set(keys))
