# Review cards and the next direction — scoping research

Research date: 2026-10-29. Scope: what the day / week review cards should show, and where nutricore goes after
"log a meal, get КБЖУ". Design target: vegan users, safe for people with a history of disordered eating, who have
little time and energy for cooking, high disgust sensitivity and many disliked foods. The cognitive lens is the
same as Sweet Home's: ADHD, depression, autism. This file is the **small pass**. Two deep-research runs
(ChatGPT Deep Research) carry the rest: **DR1** psychology and habits, **DR2** nutrition and slices.

## How to read

- **VERIFIED**: I saw the finding in the abstract or the publisher/PubMed page.
- **INFERRED**: seen only second-hand (a search summary, a citing review), or my reasoning. The source is named.
- **ASSUMED**: not checked; flagged where used.
- Everything under **Hypotheses for the cards** is INFERRED: input for the deep research, not a decision.

## Is there research on this already?

**No.** The July report
([`on-premises-calorie-tracking-…`](on-premises-calorie-tracking-for-a-medical-and-activity-data-ecosystem.md))
covers the capture pipeline: barcode → database → vision, portion estimation, confidence, FHIR. It says nothing on
what to show back to the user. [`docs/product-philosophy.md`](../product-philosophy.md) is about honest capture
("best guess, transparent, correctable"). It has no rule on feedback, and none on eating-disorder safety.
**VERIFIED** (read both 2026-10-29).

## What Sweet Home already covers — reuse, don't redo

`~/life/sweet-home/_docs/research/` (2026-09/10) has sections that carry over almost as they are:

| Sweet Home section | Use here |
|---|---|
| `adhd-evidence-base.md` §6 Emotional factors: dysregulation, rejection, shame, self-compassion | wording of every card; no "failed" framing |
| §9 Cognitive load and UI | how much one card may hold |
| §10 Chunking, starting small | the "one step for now" mechanic |
| §11A Sensory processing: disgust-laden tasks | disgust as a real cost, not a whim |
| §3 Gamification: helpful vs harmful; §4 Habit formation | streaks, celebration of small progress |
| `task-model-evidence.md` §1 Graded levels, level resets, state-triggered activation | levels for cooking steps; a level drop after a hard week |

So the deep research does **not** re-ask ADHD basics. It asks what is specific to food, to eating disorders, and to
veganism.

## Findings of the small pass

### 1. Tracking and eating-disorder symptoms: real risk, causality unclear

- 493 college students. Calorie-tracker users "manifested higher levels of eating concern and dietary restraint";
  fitness tracking was uniquely associated with ED symptoms. The authors call the results preliminary: "for some
  individuals, these devices might do more harm than good" (Simpson & Mazzeo, 2017, *Eat Behav* 26:89–92,
  PMID 28214452). **VERIFIED**
- Clinical sample, n = 105 with an ED. About 75% had used MyFitnessPal, and about 73% of those said it at least
  somewhat contributed to their ED (Levinson, Fewell & Brosof, 2017, *Eat Behav* 27:14–16, PMID 28843591).
  **INFERRED** (numbers from search summaries; self-report, no causality).
- An experimental study found no causal effect of MyFitnessPal on ED symptoms (Jospe et al., 2017, cited
  second-hand). **INFERRED**. A 2025 systematic review exists (Wallace et al., Univ. of Hertfordshire repository)
  and is unread.
- **Implication.** Calories as the headline number are the risky part. The danger is in what the card puts first,
  not in logging itself.

### 2. Regular eating is the first step of CBT-E

- CBT-E opens with a regular pattern: three meals and two or three snacks, no gap over ~4 h, **without changing
  amount or kind of food**. A mediation analysis of trial data supported regular eating as a mediator of CBT-E's
  effect on binge frequency (PMC7756462). **INFERRED** (protocol summaries; the PMC page was behind a captcha).
- **Implication.** "When did I eat" is a slice with clinical backing that carries no judgement of the food. It is a
  candidate for the first line of the day card.

### 3. Feedback beats bare logging, but generic feedback may do nothing

- In a small pilot, diet quality worsened over 8 weeks with an app diary alone and improved with feedback. In the
  larger SMARTER RCT, adding generic feedback to self-monitoring did not change diet quality (HEI-2015); the
  authors suggest feedback needs more targeted nutritional content (PMC10632722). **INFERRED** (search summaries).
- **Implication.** One targeted remark beats a dashboard of numbers.

### 4. "Add" instead of "cut"

- 68 women: a non-restrictive "eat more fruit and vegetables" approach gave the same weight result as a restrictive
  low-fat one. Restraint rose only in the restrictive group. The intake gains faded by 12–18 months
  (Lapointe et al., 2010). **INFERRED** (search summary).
- Gain- vs loss-framing results are mixed and depend on the person (autonomy, motivation). **INFERRED**.
- **Implication.** Additive goals ("a handful of beans today") avoid raising restraint, which matters most with an
  ED history. They need a way to keep going after novelty fades.

### 5. Vegan: adequate when planned; a short list needs watching

- AND 2025 position: "appropriately planned vegetarian and vegan dietary patterns can be nutritionally adequate"
  and may benefit cardiometabolic health (Raj et al., 2025, *J Acad Nutr Diet* 125(6):831–846, PMID 39923894).
  The abstract names no specific nutrients. **VERIFIED**
- The nutrients usually named are B12 (the critical one; supplement), iodine, calcium, vitamin D, EPA/DHA, iron,
  zinc and protein quality. Some reviews also note lower bone mineral density and higher fracture risk in vegans.
  **INFERRED** (DGE position, secondary reviews).
- **Boundary.** Saying "you may be deficient" is medical logic and belongs in my-health. A neutral
  "💊 B12 today?" tick is a capture nudge and may live in the bot. See product-philosophy, "Ecosystem boundary".

### 6. Picky eating, disgust and new food: adult evidence is thin

- Neophobia in adults is linked to food disgust sensitivity (a 2026 Go/no-Go study, n = 57). Repeated exposure is
  well supported in children and is the default advice for adults. Exposure does not always raise liking: in one
  study, liking for a rarely eaten food dropped. **INFERRED** (search summaries).
- An adult CBT case for food neophobia exists (Marcontell et al., 2003, *J Anxiety Disord*); results unread.
  **INFERRED**
- **Implication.** "Cook something new" is a big, risky step. A smaller ladder may work better:
  - a known dish plus one added ingredient;
  - a new brand of a known food;
  - a ready-made version before cooking it yourself.

  This is the gap the deep research must fill.

## Hypotheses for the cards (to test, not to build yet)

- **Day card**, in priority order:
  1. Rhythm: when I ate, and the longest gap. No amounts.
  2. What was there: a few plant groups as presence, not grams. Legumes, greens/veg, fruit, whole grains,
     nuts/seeds.
  3. Protein spread across meals.
  4. 💊 B12, if the user turns it on.

  КБЖУ and calories stay one tap away, and the card can be set to hide them. This follows the Stage 2 rule that
  goal and weight can be switched off.
- **Week card**:
  - days with legumes;
  - count of distinct plant foods;
  - regularity;
  - one thing that went well, picked from the data and never invented.
- **One next step** instead of a list of problems. The bot picks it, sized to the week (a smaller step after a hard
  week), and the user can swap it.
- **No red, no "too much", no "should".** The banned-words test from Sweet Home applies.

## Gaps → deep research

- Which daily and weekly slices are valid for a **vegan** diet and computable from photo/text logs. Candidates:
  PDQS, hPDI, food-group presence, plant diversity. → DR2.
- How to show macros at all to someone with an ED history: hidden, ranges, opt-in. What ED clinicians and lived
  experience say about tracker design. → DR1.
- Cooking skill ladder for low time, low energy, high disgust; how to pick "the best step now"; how to celebrate
  without gamification harm. → DR1, cooking section.
- Primary reading of the papers marked INFERRED above.
