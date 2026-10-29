# Stage 5 — Gentle review: day and week cards, one next step

**Covers:** the review cards (day, week), safe display of numbers, food-group and routine capture, one adaptive
next step, reliable foods, the cooking ladder.
**Depends on:** Stage 1's shipped 📊 statistics (aggregation by the user's day), Stage 2's ⚙️ Настройки.
**Evidence:** [`gentle-feedback-evidence.md`](../researches/gentle-feedback-evidence.md) (psychology, habits,
eating-disorder safety, picky eating, cooking) and [`vegan-slices-evidence.md`](../researches/vegan-slices-evidence.md)
(which nutrition signals are valid and robust). Scoping: [`review-cards-scoping.md`](../researches/review-cards-scoping.md).
**Status:** 📝 planned 2026-10-29; the owner's answers to the open decisions are pending (a questionnaire). Every
place an answer changes the plan is marked **❓**.

## Why this stage, and why it changes stages 1, 3 and 4

The bot logs meals well. What it shows back is a calorie app's view: КБЖУ per meal and per day. The evidence says
that for the design target (see the research docs) the risky part is not logging but **what the card puts first**:
calorie targets, "remaining", over/under colours, streaks. The useful, robust signals are **presence** (which food
groups and anchors were there), **rhythm** (when food happened) and **routines** (B12 and similar ticks). Those
also survive AI estimation error much better than grams.

So three earlier plans conflict with the evidence and are revised here:

| Earlier plan | Conflict | Revision ❓ |
|---|---|---|
| Stage 1 §4 "remaining-budget reply" after each meal, §5 "totals vs goal + remaining" | "Calories remaining" is the most-reported harmful tracker feature | Drop "remaining" from every default surface. A calorie goal, if ever kept, lives only in the numbers layer |
| Stage 3 "streaks" and the evening "streak-save" nudge | Streaks make logging itself the target; a missed day reads as failure | Drop streaks. Reminders stay optional and gentle; the weekly digest becomes the week card |
| Stage 4 §1 "what to eat from the remaining macro budget" | Generates goals from calorie and macro gaps | Becomes the one-next-step engine below: additive, effort-sized, never from a calorie gap |

## Rules that apply to every slice

These come from the "top principles" of both docs. A slice is not done until its screens pass them.

1. **Two layers.** Default: at most three observations plus one next step. Numbers (kcal, macros, grams) appear
   only behind «🔢 Цифры», and the switch in ⚙️ can hide them everywhere. ❓ default state.
2. **Presence, not deficit.** Say what was there. Never say what was missing, too much, too little, over or under.
3. **Add, never restrict.** No generated goal removes or reduces a food. A missed step brings no compensating step.
4. **No streaks, badges or "perfect" anything.** Praise only process: coming back, a flexible fix, a pattern noticed.
5. **Neutral look.** No red/green for food. Colour and the green button only for navigation and Done.
6. **Weekly for micronutrients.** No daily miss for anything on a weekly schedule.
7. **Non-medical.** Never "deficient", "low", "not enough". Ticks and presence only (product philosophy, boundary).
8. **Banned-words test.** One test over every user-facing string. It covers the telegram-bot-ui list plus a
   nutrition list:
   - слишком, превысил, перебор, норма, недобор;
   - осталось (for food), срыв, сорвалась;
   - плохо, хорошо (about food), чистое питание;
   - заработала, отработать, сжечь, «надо», «должна».

   It has a known-positive control, and also cases that must pass, so the test does not wrongly refuse.
9. **Robustness order.** Visible signals come from food identity and food group first. A number from a photo is
   rounded or hidden.

## Slices

Each slice is one feature branch, tests first, released through `nutricore-release` like every runtime change.
The order is the recommended one; ❓ the owner may reorder. Rough size is in "sessions" of focused work.

### A. Safety pass on what already ships (≈1 session)

- Sweep every current screen for evaluative wording and colour:
  - the meal reply;
  - 📊 Статистика (today, yesterday, 7 days, with bars);
  - ⚖️ Мой вес;
  - ⚙️ Настройки;
  - favourites.
- Add the banned-words test (rule 8) with its control.
- Add the ⚙️ «🔢 Цифры» switch (rule 1). Wire 📊 and the meal reply to it. ❓ whether the meal reply hides
  numbers too, and the default.
- Remove «🎯 Цель по калориям: не задана» from ⚙️ unless a calorie goal survives. ❓
- Stage docs 1, 3 and 4 get the revisions in the table above.

### B. Food groups on every meal (≈1–2 sessions)

- Extend the existing meal analysis (same OpenAI call; no new call) to tag each item with groups:
  - legumes/soy;
  - vegetables;
  - fruit;
  - whole grain;
  - nuts/seeds;
  - calcium-rich/fortified;
  - ALA-rich;
  - protein anchor.
- Stored in `Meal.ai_analysis` JSON: **no migration**.
- The groups are also shown, editable, in the meal draft, so a wrong tag is fixed where it is born (product
  philosophy: transparent, correctable).
- A validation set of 30–50 real vegan meals checks the tagging, because no published study validates
  food-group classifiers on vegan mixed meals (gap in the nutrition doc). Built from fakes and the owner's own
  logs, never committed if personal.
- ❓ Re-tag past meals: costs one cheap call per meal. Price one call first; the whole run must stay under the
  $0.20 rule or be asked about.
- ❓ Which groups the owner wants on the cards (the analysis can tag all of them; the card shows a chosen few).

### C. Routine ticks (≈1 session)

- ⚙️ Настройки → «💊 Рутины»: the owner turns on the routines they actually use, each with its own schedule
  (daily / on certain weekdays / weekly):
  - B12;
  - vitamin D;
  - an iodine source;
  - algal omega-3.
- A tick is one tap on the day card, or a typed message.
- The week card says «B12 ✓», never a miss. ❓ which routines, and whether to remind (default: no reminder).
- Stored in `User.settings` plus a small per-day record. That record may need a table, the only likely migration in
  this stage. Decide in the slice's design task.

### D. Day card (≈1–2 sessions)

- 📊 «Сегодня» becomes the day card. Order, from the evidence, with at most three lines plus the step:
  1. **Rhythm:** when food happened, as plain times. A long gap is described, never timed. ❓ whether rhythm shows
     at all.
  2. **What was there:** the chosen food groups as a positive list.
  3. **Protein anchors:** «тофу в обед · чечевица вечером».
  4. **Routine ticks**, if any are on.
  5. **One next step** (slice F).
- «🔢 Цифры» opens today's КБЖУ (the current view), if numbers are on.
- An empty day asks about the log («похоже, не всё записано?»), never about the food.
- UI per the telegram-bot-ui skill: edit in place, ≤ 4 rows, a named back button, mock first, then the owner's
  look at the mock before code.

### E. Week card (≈1–2 sessions)

- 📊 «7 дней» becomes the week card:
  - group days («бобовые 5 из 7»);
  - routines;
  - plant variety with no target, plus "new this week" ❓;
  - **one real win** picked from data, never invented: "came back after a break", "three steady mornings";
  - the active experiment and how it went.
- A week with two logs must not look worse than one with seven.
- ❓ Delivery: only on tap, or also a Sunday-evening push (this replaces Stage 3's digest; it needs Celery beat and a
  send helper, both planned in Stage 3).

### F. One next step (≈2 sessions)

- Rule-based first; no LLM and no bandit (the evidence for autonomous optimisation in this population is very
  low). Selector order:
  1. Exclude restriction, compensation, weight loss, foods on the dislike list.
  2. Regular, sufficient eating: only if rhythm is on.
  3. One additive anchor from the week's least-present group, using the anchor-food list (nutrition doc §8).
  4. Rank by benefit × chance ÷ effort.
- **Effort tiers** on every step: 0–2 min / 5–10 min / 15–25 min familiar / new. ❓ whether the owner sets an energy
  mode (normal / low / survival), or the bot only suggests one.
- After two misses the step shrinks; it never grows pressure.
- Buttons:
  - «✅ Сделала»;
  - «🔄 Другой шаг», so the owner can swap;
  - «😴 Не сейчас», which is neutral and never counts as a fail.
- One active experiment at a time, stored in `User.settings`.
- ❓ Who picks: the bot proposes and the owner can swap (recommended), or the owner picks from three.

### G. Reliable foods and dislikes (≈1 session)

- «Надёжная еда»: seeded from favourites and the most repeated meals. Repeats are never a variety failure.
- «Не предлагать»: foods the owner never wants suggested. Typed once, honoured by slice F.
- Both live in ⚙️ or in a 🍽 hub. ❓ whether to keep a dislike list at all.

### H. Cooking ladder (later; ≈2+ sessions) ❓

- Steps are operations, not recipes. Each is a variation of a reliable meal:
  1. Assemble.
  2. Microwave frozen parts.
  3. Boil one staple.
  4. Pan-heat one protein.
  5. A familiar three-part meal.
  6. Something new.
- Every experiment names its fallback («если не зайдёт — обычная еда, ничего компенсировать не нужно»).
- A new food is a teaspoon beside a safe meal, never the whole dinner. "Didn't like it" is logged as information.
- ❓ Now, later, or not at all.

## What this stage does not do

- No calorie targets, deficits or weight-loss goals on any default surface.
- No micronutrient amounts or "deficient" wording (medical: my-health).
- No UPF score, no "30 plants" target, no fasting or meal-frequency targets.
- No autonomous LLM coaching on eating behaviour; Stage 4's «психолог» stays a separate, later question.

## Verification (every slice)

- `./scripts/test.sh` green.
- The banned-words test with its control, which turns red when a banned word is planted.
- Screen tests per the telegram-bot-ui rules: rows, back buttons, labels.
- **Cases that must produce no finding:**
  - an empty week produces no "you missed";
  - a weekly B12 schedule produces no daily miss;
  - a day with only safe foods produces no variety nag.
- Before release: drive the real bot in polling mode, and run the `ux-fresh-eyes` skill on each new card's mock.
- After release: confirm the new module imports inside the running container (memory: deployed ≠ pushed).

## Evaluation after a few weeks

Judge by:
- how it feels;
- skipped meals;
- compulsive checking;
- coming back after a hard week.

Not by logging frequency. A short check-in question to the owner after two weeks of the day card.
