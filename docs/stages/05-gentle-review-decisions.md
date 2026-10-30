# Stage 5 — owner decisions, 2026-10-29

Answers to the stage 5 questionnaire. The plan is [`05-gentle-review.md`](05-gentle-review.md). Every
requirement below is reversible unless marked otherwise. "Default" means the owner left the question
unanswered and the stated default applies.

## Requirements

| Id | Decision | Slice |
|---|---|---|
| G5-R01 | Calories and macros are hidden behind «🔢 Цифры» by default, on every screen. A switch in ⚙️ Настройки shows them again | A |
| G5-R02 | The meal draft after logging follows the same switch as 📊 Статистика (one setting for both) | A |
| G5-R03 | No calorie goal and no "remaining" anywhere. The «🎯 Цель по калориям» line leaves ⚙️ Настройки. Moving towards target values goes through additive weekly anchors only | A |
| G5-R04 | No streaks, no "days in a row", no streak-saving nudge. Coming back after a break is acknowledged; a break is not mentioned | A, E |
| G5-R05 | Weight stays in «⚖️ Мой вес» only; the day and week cards never show it | D, E |
| G5-R06 | The day card's first line is meal times as plain times, no timers and no "missed" | D |
| G5-R07 | Food groups on the **day** card: protein anchor, whole grains. On the **week** card: all eight (legumes/soy, protein anchor, vegetables, fruit, whole grains, nuts/seeds, calcium-rich, plant omega-3) | B, D, E |
| G5-R08 | Routine ticks are **user-defined**: the bot ships no preset supplement list and never suggests, recommends or questions a supplement. A tick is a neutral record of something the user decided to do (see "Supplements" below) | C |
| G5-R09 | The week card shows the number of different plants, with no target and no comparison | E |
| G5-R10 | One next step: the bot proposes one, the user can swap it or say "not now" | F |
| G5-R11 | Energy mode (normal / low / survival) is switched by the user; the bot does not infer it | F |
| G5-R12 | Reliable foods are seeded automatically from favourites and frequent meals; the user edits the list | G |
| G5-R13 | A «не предлагать» list exists in ⚙️; the next-step engine honours it | G |
| G5-R14 | The cooking ladder comes after the day and week cards | H |
| G5-R15 | The week card is available on tap and is also sent on Sunday at 22:00 in the user's time zone, silently | E |
| G5-R16 | Default: slices go safety → food groups → cards (question unanswered) | all |
| G5-R17 | Re-tagging past meals is allowed if the whole run costs under $0.20; price one call first, ask again above that. **Money: one-way** | B |
| G5-R18 | Up to 3–4 review agents before each release without asking; a cost line after each run | all |
| G5-R19 | **All user-facing text is gender-neutral**: the project is open source and the bot is universal. «✅ Готово», never «✅ Сделала». Enforced by a test with a positive control | all |
| G5-R20 | Ready-made and convenience food is first-class. Steps add to it; they never "fix" or replace it | F, H |
| G5-R21 | Future: the user can export the food diary for a period as a file to hand to a psychologist or a nutritionist (owner, 2026-10-29). Format: both — a PDF a person reads and a table (CSV) to count in (owner, 2026-10-30) | I |
| G5-R22 | The bot explains itself: a base of short explanations of what each feature does, how it works and where the idea comes from, reached by tapping (owner, 2026-10-30). Plain text in the repo, no model call. Each new feature adds its entry in its own slice. Text comes from `docs/researches/`, rewritten for the user: never the research documents themselves, no judging words, no eating-disorder material, no supplement advice | J, then each of D–I |

## Notes behind three answers

**Supplements (G5-R08).** The owner asked to tick all five offered routines but added that they are not sure
supplements are needed at all, and would challenge each one and ideally agree it with a doctor. So the bot
must not carry an opinion about supplements. Which supplements anyone takes is a medical decision outside the
bot (the my-health boundary). The bot only records a tick for a routine the user created.

**Universal text (G5-R19).** The owner: «Бот должен быть универсальным. Это опенсорс проект». The rule covers
button labels, notices and cards. Wording addresses the user without past-tense verbs that carry gender
(«Готово», «Записано», «Получилось?»).

**Ultra-processed food (G5-R20).** The owner: ready-made food is normal; if it has proven harm, it could be
phased out gently. What the evidence says ([`vegan-slices-evidence.md`](../researches/vegan-slices-evidence.md)
§7): in plant-based cohorts most of the ultra-processed signal is explained by overall plant-food quality, and
the NOVA classification has only moderate validity without brand data. So the plan does not remove anything.
It adds whole-food anchors to ready meals, such as frozen vegetables, a legume pouch or fruit. That serves
the same goal through the "add, never restrict" route.

## Screen questions for slices B–I (2026-10-30)

The mocks asked 12 questions and said that silence takes the recommendation. The owner answered «давай делать»,
so these stand until changed. Stories: [`05-gentle-review-stories.md`](05-gentle-review-stories.md).

| # | Question | Taken |
|---|---|---|
| 1 | «из 7» on the week card | No. «овощи 7 дн.», present groups only |
| 2 | Energy mode control | One cycling button |
| 3 | Mode names | обычно / мало сил / минимум сил |
| 4 | Routine icon | Choose one of 💊 🚶 🧘 💧 😴 on creating a routine |
| 5 | Marks for omega-3 and anchor | 🌰 and ⚓ |
| 6 | Custom icons on the old buttons | Yes, once the emoji set is uploaded |
| 7 | Independent fresh-eyes round | **Open:** runs only on the owner's yes (about 260k tokens) |
| 8 | Emoji set upload | **Open:** needs the owner's Premium account and a set name; plain emoji until then |
| 9 | Stories in the repo | Yes |
| 10 | «🗓 Итог недели» switch | Keep |
| 11 | «7 дней» vs «Неделя» | Last seven days vs Monday to Sunday |
| 12 | Size chips | «Меньше 240г · ✓ 350г · Больше 490г» (grams are rounded to tens), without waiting for a phone screenshot |

## Slice B, as built

- Stored in `ai_analysis` and in `nutrients` (a favorite copies `nutrients`): `food_items` is what the analysis
  said, per item; `food_groups` is what the user confirmed; `food_groups_by_user` says whether they edited it.
- Omega-3 is plant sources only, flax oil included (G5-R07). Fish is «anchor».
- A meal nobody tagged carries no `food_groups` at all, so the re-tag run finds it. `[]` means «looked at, none».
- A correction typed into the draft is a new analysis: its groups replace the earlier ticks.
- Validation (invented meals, `tests/fixtures/food_groups_eval.json`, text analysis on gpt-6-luna, $0.0096):
  74 of 75 required tags, no extra ones; the miss is «anchor» on ready-made nuggets. The photo path uses the same
  rule and was not run. The owner's real meals are still to be run, through `--set`, outside the repo.

## Slice D, as built (text card)

- The text card is whole on its own: head with the full month, times of food, whole grains and protein anchors if they
  were there, ticked routines, then the numbers in a collapsed quote. The picture comes on top of it and can fail
  without taking anything away.
- A meal's confirmed `food_groups` decide a line, not the analysis' items: a group the user took off hides its items, and
  a group the user added with no item behind it shows the meal's own name. An untagged meal gives only its time.
- A gap of 6 hours or more between two meals of one day gets words and no hours: «днём длинный перерыв». The threshold is
  the project's choice; the evidence gives no exact one.
- With no numbers open, the card does not name untagged food, as in the mock. The names are one tap away, in «🔢 Цифры».
- Buttons, top to bottom: routine ticks, «🔢 Цифры», tabs. The tabs are always last, on the week too.
- Empty today: the invitation and «🍽 Добавить приём пищи», which enters the keyboard button's flow. **Departs from the
  mock:** routine ticks stay on an empty day, above that button, because slice C lets a routine be ticked with no food
  logged. Mid-step, where the conversation does not take the tap, a toast says to finish the step first.
- A card left open overnight shows today with a notice on top: «Это была карточка прошлого дня, вот сегодняшняя». **Departs
  from the mock's** «вчерашняя»: the card may be older than yesterday. Malformed button data shows today with no notice.
- The step, ✅/🔄/😴 and 🔋 belong to slice F, so the first-show line has no sentence about a step yet.

## Slice C, as built

- Routines live in `User.settings["routines"]`; each tick is a row in `routine_ticks` (migration `a9b8c7d6e5f4`). A day
  without a tick leaves no row, so nothing can count it.
- A removed routine stays stored and its ticks stay: undo restores it, and a tick on an earlier day still happened.
- A new routine is a hidden draft from the typed name on. Leaving it halfway shows nothing, and the next new one
  replaces it.
- The steps run name → days → icon (screen question 4). «🔣 Значок» on one routine's screen is an addition to the mock:
  the icon chosen at creation has to be changeable somewhere.
- The card's day travels in the tick button. A card left open overnight ticks nothing and shows today.
- A weekly routine shows every day of its Mon–Sun week until ticked, then only on the day of the tick.
- A typed message ticks only when it equals a routine on today's card, ignoring case and extra spaces. Other text stays
  ignored as before, and text being entered as a meal never reaches this check.
- Up to 9 routines, names up to 30 characters. Button rows narrow for long names and count ✓ in advance, so a tick
  never moves a button.
- A tick button carries the state it asks for, so an old card or a double tap cannot flip a tick set elsewhere. A
  routine ticked today stays on the card even if its schedule has changed since, so the tick can be taken back.
- The name step closes on any other inline tap and after 10 minutes. Text after that is what it would be anywhere else:
  a weight, a routine tick or nothing, never a silent rename. «➕» and «✏️» do not pull the user out of a meal being
  entered: they answer with a short notice and the meal stays.
- Names that read as a weight («72,5», «100») are refused, because typing one is a weigh-in. Undo from an older message
  rechecks the name and the limit; a second tap on «🗑» keeps the undo button.
- Known gap: the name step lives in memory like every conversation state, so a bot restart between «➕» and the typed
  name loses it. The name then goes unanswered, and tapping «➕» again recovers.

## Slice D, the picture

- A sidecar draws the card (`render/`): no network, no secrets, only the `render_jobs` volume shared. The caption is the
  whole text twin, so a stopped sidecar costs the picture and nothing else.

## Slice E, as built

- The tab «7 дней» is the last seven days to today; Sunday's message is Monday to Sunday and titled «Неделя». The same
  screen builds both, and the finding's words differ: «Новое за 7 дней», «Новое на этой неделе».
- Plants are counted by the names of tagged foods whose group the meal still confirms. A group ticked by hand has no name
  and adds no plant. Names match after case, «ё» and spaces are evened out, and nothing more: «гречка» and «гречневая
  каша» count as two. Merging them would take a dictionary or a paid call, and the number is a plain count with no target.
- The finding is one at most: «Снова с записями» when 7 or more days without records came before the period's first
  record, else the first plant food no earlier meal named. With no earlier meals nothing is «new». The 7 days are the
  project's choice. **Departs from the design:** «three steady mornings» is not built, since the card has no way to say
  it that is not a count of days.
- Meals with no tags: «Записи есть, группы в них пока не отмечены», with the numbers still a tap away.
- The picture's finding drops «— впервые в записях» after a name over 20 characters; the words did not fit on the card.
- **Sending.** Beat runs every 15 minutes, because time zones with :30 and :45 reach 22:00 between the hours. The window
  is all of 22:00–23:59 on Sunday, so a run lost to a restart is made up. The week is marked in `week_push_last` before
  the send, under a row lock, because two summaries are worse than one missed. Only a connection never made
  (`ConnectError`, `ConnectTimeout`, `PoolTimeout`), a refusal or a failure before the send clears the mark for a retry.
  Any other network error may have arrived after Telegram took the request, so the mark stays and no text follows. A
  worker killed between the mark and the send loses that week, and a retry after 23:59 does not happen. Both accepted.
- The summary goes only to whoever may open the screens: access mode and an active subscription, admins always. Its
  button would answer 🔒 to anyone else.
- A summary goes out only once its picture is drawn or the wait ends in text, with no loader in a quiet chat. The worker
  got the `render_jobs` volume for this.
- The summary's only button is «🔢 Цифры», with its own Monday in the data, so it keeps its week days later. No tabs: it is
  a message, not the screen.

## Slice F, as built

- `app/services/next_step.py` is pure rules with no model and no learning; `stats_screen` draws it. Order: drop what the
  user never wants suggested, then the week's least-present group (days over the last seven), then benefit × chance ÷
  minutes. Benefit and chance are the project's own rough marks, and no study tested this card. A step with two groups
  counts by the rarer one.
- **Departs from the spec:** the «regular sufficient eating» branch is not built, because the app has no rhythm setting
  to switch it on. The 15–20 minute size is left for the ladder (slice H): normal mode offers up to 10 minutes, as the
  mock's toast says.
- The step is chosen once a day, on the first view of today with a meal, and stays when more meals are logged. Yesterday's
  step is not chosen again, so «завтра будет новый» holds. An empty day has no step and no energy button; yesterday and
  the week have neither.
- The state lives in `User.settings["next_step"]`, the mode in `settings["energy"]`. The mode changes only by the user's
  tap and stays across days. Changing it chooses an open step again for the new size, and leaves a step done or put off.
- `row` counts «😴 Не сейчас» in a row: at two the next step is one size smaller, without a word. «Вернуть шаг» and undo
  restore it, «Готово» clears it. Nothing else reads it and no screen shows it.
- **Two sources disagree, the mock wins:** US-F1 says a put-off step is shown nowhere, the design has a «day-later» screen.
  Built: that day's own card says «😴 Шаг отложен» with «👣 Вернуть шаг»; no other day, tab or count ever shows it.
- The catalogue goes past the anchor list: fruit, vegetables, seeds and any nuts are the project's additions.
- `no_suggest` and `reliable_foods` are read here and filled by slice G. A refused word also drops a step that merely
  goes with it: refusing «хлеб» drops «хумус к хлебу». Matching is by word start with the ending cut, so it can refuse one
  step too many. That costs a step, and the other way would suggest what was refused.
- **Known gap:** the user model has no allergy data, so until slice G's list a nut step can reach someone allergic to
  nuts. «🔄 Другой шаг» is the remedy until then.
- In the lowest mode the keyboard keeps to the step, as in the mock: ticks leave the buttons, stay in the text and still
  work by typed name.
- A time zone change across midnight can drop that day's done or put-off mark and choose again. Rare, accepted.
- Callback data carries the step shown, the card's day and, for energy, the mode wanted, so a double tap or an old card
  changes nothing. A button from another day shows today's card with the usual notice.

## Facts confirmed

- Cooking time: steps stay within 15–20 minutes.
- At most one new food per week; one experiment at a time.
- The week runs Monday to Sunday.
