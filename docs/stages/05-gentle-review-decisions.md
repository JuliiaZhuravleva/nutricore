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

## Facts confirmed

- Cooking time: steps stay within 15–20 minutes.
- At most one new food per week; one experiment at a time.
- The week runs Monday to Sunday.
