# Gentle feedback — psychology and habit evidence for the review cards

Research date: 2026-10-29. Source: a ChatGPT Deep Research run ("DR1", prompt in the scoping doc's gaps), condensed
and re-labelled here. Follows [`review-cards-scoping.md`](review-cards-scoping.md). The sibling on nutrition is
[`vegan-slices-evidence.md`](vegan-slices-evidence.md); the build plan is
[`../stages/05-gentle-review.md`](../stages/05-gentle-review.md).

Design target: vegan users, safe for people with a history of disordered eating, little time and energy for cooking,
high disgust sensitivity and many disliked foods. ADHD basics are not repeated; Sweet Home's evidence base covers them.

## How to read

- **VERIFIED**: I read the abstract myself (PubMed, 2026-10-29). Spot-checked: Moody 2025, Sivyer 2020, Herforth
  2024 — all three matched what the run reported.
- **DR**: the deep-research run says it read the abstract or full text; I did not re-check it.
- **INFERRED**: a design inference, the run's or mine. Nothing here is a tested bot feature.

## 1. Tracking and eating disorders

- **Association, not proven cause.** A 2025 systematic review of 27 studies: cross-sectional studies link tracker use
  with disordered eating, restraint and excessive exercise; experimental studies did not reproduce the link. "It is
  currently not possible to conclude if they increase disordered eating"; trackers may be a marker rather than a
  cause (Moody et al., 2025, *Eur Eat Disord Rev* 33(6), PMID 40640999). **VERIFIED**
- Students: calorie-tracker users had more eating concern and restraint (Simpson & Mazzeo 2017, PMID 28214452).
  Clinical sample n = 105: most MyFitnessPal users felt it contributed to their ED (Levinson 2017, PMID 28843591).
  A 12-month weight-loss RCT found no worse ED scores with intermittent tracking (Jospe 2017, PMID 28703448), but it
  excluded high-risk people. **DR**
- **Which features hurt** (qualitative, Eikey et al. 2021, *BMJ Health Care Inform*): being "under" a target with
  green feedback felt rewarding; red for going over brought guilt, shame and plans to restrict later; "calories
  remaining" caused anxiety; people competed with themselves to eat less. **DR**
- **Treatment records are different.** CBT-E's food record asks for time, a plain description and context; the
  manual says calories are *not* recorded. Recovery Record logs meals, thoughts and feelings. Even so, some patients
  rebuilt compulsive habits by logging in excessive detail (Lindgreen 2018, PMID 29934285). **DR**
- Weight monitoring: a 2024 meta-analysis of 28 studies found no overall link with ED scores in the general
  population (Kühn 2024, PMID 39069082); CBT-E still limits weighing to once a week with interpretation. **DR**

**Confidence.** Moderate–high for the association; low–moderate for causality; moderate that numeric targets,
red/green pass-fail, "remaining" counters and salient weight hurt a meaningful subgroup. Causal trials usually
exclude exactly the people this design targets.

**Design implications.** INFERRED
- The default review must not look like a calorie app. Calories, macros, "remaining", deficits, target completion
  and pass/fail colours sit behind an explicit numbers layer, never on the home surface.
- The card answers "what pattern helps me feel fed and function?", not "how far from target was I?".
- No warning for going over, no congratulation for going under.

## 2. Safe feedback

- **No trial has found the safest display.** No RCT compares exact numbers vs ranges, hidden vs visible calories,
  daily vs weekly, or neutral vs evaluative colours in people with an ED history. A 2025 metasynthesis of self-guided
  ED interventions found food logging the most controversial feature; users wanted to switch functions off and pick
  simple vs detailed views (*JMIR Mental Health* 2025). **DR**
- **"Hidden by default, available by choice"** has more support than "never show numbers": the same feature feels
  supportive to one person and compulsive to another, and changes with recovery phase (Lindgreen 2018). **DR**
- **Presence fits treatment logic**: regular eating, eating enough, a range of macronutrients predicted fewer binge
  and compensatory symptoms the same and the next week (Smith 2023, PMID 37986629). **DR**
- Intuitive-eating interventions: diet quality neutral or better across 14 studies (Warren 2023, PMID 36274010);
  no tested digital feedback format. **DR**

**Confidence.** High that ED-oriented monitoring is descriptive, not budget-framed; moderate for opt-in detail; low
for ranges, colours and weekly summaries specifically.

**Design implications.** INFERRED
- **Two layers.** The default card holds at most three non-evaluative observations: regularity, what was present,
  one suggested next step. A «🔢 Цифры» control reveals energy and macros on request.
- Numbers never share a line or a visual level with praise.
- Neutral encoding: plain text, muted dots. Colour is for navigation, never for nutritional virtue.
- An empty or odd day asks about the log ("maybe some meals were not logged?"), not about the food.
- **Wording**: observations, not verdicts. "Protein-containing food at two meals", not "protein too low"; "a long
  afternoon gap", not "you skipped a meal"; "one thing that might make tomorrow easier", not "you need to fix".
  Banned: good/bad, clean, cheat, earned, burned off, over budget, failed, too much, only N left, any praise for
  eating less.

## 3. Regular eating

- Regular eating (three meals, two or three snacks) is an early core step of CBT-E; NICE NG69 recommends it for BED
  and BN. **DR**
- Regular eating mediated CBT-E's effect on binge-eating frequency in a secondary analysis of a 130-patient RCT
  (Sivyer et al., 2020, *Int J Eat Disord* 53(12):1928–1940, PMID 33150640). **VERIFIED**
- Supporting: adherence to the meal plan tracked lower binge frequency (Zendegui 2014, PMID 24854811); regular
  eating predicted lower symptoms the next week (Smith 2023). **DR**
- The "never more than four hours" rule is a clinical heuristic, not a tested physiological threshold. **DR**

**Confidence.** Moderate–high for regular eating in BN/BED treatment; low for any exact gap threshold. Samples are
clinical BN/BED, so extrapolation to every ED history stays cautious.

**Design implications.** INFERRED
- Meal rhythm is the safest first slice: it carries no judgement of the food and has clinical backing.
- Describe, don't police: "mornings were steady; three days had a long gap before the evening". No overdue timers,
  no punctuality percentage, no "never over 4 h" streak.
- Note the nutrition side (sibling doc, §5): as a *diet-quality* metric, meal timing has weak evidence. It earns its
  place as self-care, not as nutrition.

## 4. Add, not restrict

- An "eat more fruit and vegetables" arm produced less dietary restraint than a low-fat arm (n = 68, Carbonneau
  2010, PMID 20850069). **DR**
- If-then plans worked better for adding healthy foods (d ≈ 0.33) than for cutting unhealthy ones (d ≈ 0.18)
  across 70 interventions (Carrero 2019, PMID 31125588). **DR**
- Counter-signal: a conventional "five a day" target beat a personal "just one more" goal over a short follow-up
  (n = 84, de Vet 2013, PMID 23415976). Smaller is not automatically better. **DR**
- Self-compassion interventions reduce body-weight shame; effects on eating itself are mixed (Braun 2021,
  PMID 34694950). **DR**

**Confidence.** Moderate that adding beats restricting on average; moderate–high that dieting should be avoided in
binge-spectrum EDs; low that "crowding in" makes other foods disappear on its own.

**Design implications.** INFERRED
- Generated goals ask "what can be added?" first. Restrictive goals are never generated from calorie or macro
  estimates.
- A possible gap becomes an opportunity: "one experiment: tofu or beans in a meal you already like", not "protein
  was low again".
- A missed goal produces no compensating target the next day.

## 5. One step at a time

- Implementation intentions: modest effects, stronger for specific if-then plans than complex ones (Carrero 2019);
  about +0.29 servings/day of fruit and vegetables in adult RCTs (2026 meta-analysis, PMID 41987207). **DR**
- No dietary RCT shows that exactly one goal beats two or three. The supported claim is *specificity*. **DR**
- Dietary JITAIs (20 studies, 2026 review, PMID 42462070): more consistent effects on near-term behaviour than on
  long-term outcomes; prompt burden is a recurring problem. **DR**
- Bandit-style recommenders: feasibility only (MyBehavior, n = 17). OnTrack's benefit depended on the surrounding
  programme. **DR**

**Confidence.** High for implementation intentions as a modest tool; very low that an algorithm should optimise
eating autonomously for an ED-vulnerable person.

**Design implications.** INFERRED
- **One active experiment** on the card. The bot may notice other patterns silently.
- **Selector order:**
  1. Exclude restriction, compensation, weight loss and disliked or high-disgust foods.
  2. Regular and sufficient eating.
  3. Additive adequacy (anchors, see the sibling doc).
  4. Rank by benefit × chance of doing it ÷ effort.
- **Effort tiers:** normal / low energy / survival. A hard week lowers the effort, not the aim: "cook lentil curry"
  → "ready-to-eat protein beside a meal you already eat".
- After two misses, shrink the step before changing the aim. A miss is information about friction ("this version
  costs too much; try the 30-second one?"), never about motivation.
- Never optimise engagement: an ED-vulnerable user can be highly engaged in the wrong behaviour.

## 6. Celebrating without harm

- Therapists endorse celebrating small wins but warn against rewards tied to eating changes (2025 interview study).
  **DR**
- Streaks and reminders may feed compulsive logging; breaks can help (Eikey 2021). In 2026 lived-experience studies,
  some liked streaks while others felt a missed day as failure; rigid numeric tracking felt more compulsive than
  reflective journaling. **DR**

**Confidence.** Moderate that quantified rewards and rigid achievement mechanics harm vulnerable users; low for
which reward systems are safer.

**Design implications.** INFERRED
- **No logging streak, no perfect-week badge, no under-target celebration.**
- Praise process, flexibility and care: "found something that worked on a hard day", "noticed the afternoon gap and
  tried a snack", "came back after a break".
- Breaks are neutral: a week with two logs does not look worse than a week with seven.

## 7. Picky eating, disgust and new food in adults

- Adult ARFID treatment (2026 meta-analysis, 40 studies, PMID 41913341): improvement pre–post, but mostly case
  series, very few RCTs. **DR**
- Food disgust is strongly associated with picky eating and partly accounts for the sensory-sensitivity link
  (n = 491 adults, PMID 41038394). Disgust may extinguish less readily than fear (Harris 2019, PMID 30801767). **DR**
- CBT-AR: adults added a mean of 18 new foods in an open trial (n = 15, PMID 34423319); a 2026 naturalistic cohort
  of 549 improved across profiles (no controls). Modules matched to the mechanism worked better (PMID 38213085). **DR**
- Repeated exposure reduces neophobia broadly, but only seven intervention studies exist across the lifespan
  (Kaar 2023, PMID 36394363). Food chaining has no controlled adult trials. **DR**
- Nobody has measured what a failed new dish costs (money, effort, a missed meal, more avoidance). **DR**

**Confidence.** Moderate that disgust and sensory sensitivity drive adult selective eating; preliminary for CBT-AR;
low for food chaining and sensory approaches as standalone adult treatments.

**Design implications.** INFERRED
- Keep a **reliable-foods** list. Repeating them is not a failure of variety.
- **Separate exposure from feeding yourself.** A new thing is never the only dinner: a teaspoon beside a safe meal.
  "Tried it, didn't like it" is information gained, not a failure.
- Change one dimension at a time (a familiar sauce on a known meal), never "try this whole new recipe".
- Refusing an experiment is never non-compliance.

## 8. Learning to cook with low time and energy

- Cooking interventions raise confidence and self-reported diet more reliably than hard outcomes (Hasan 2020,
  30 studies, PMID 32153942); a 2026 review rates certainty low to very low (PMID 42616802). **DR**
- Batch cooking: one pilot of ten adults. Meal kits and no-prep meals were both acceptable (PMID 38041070);
  delivered minimally processed meals were feasible in depression (n = 31, PMID 42034775). **DR**
- No evidence validates a "recipe complexity ladder"; essentially no cooking RCTs target adults with ADHD. **DR**

**Confidence.** Moderate for self-efficacy gains; low for any specific method; very low for ADHD-specific evidence.

**Design implications.** INFERRED
- Model cooking as a **ladder of operations**, not recipe sophistication:
  1. Open and assemble.
  2. Microwave frozen parts.
  3. Boil one staple.
  4. Pan-heat one protein.
  5. A familiar three-part meal.
  6. Only then an unfamiliar recipe.
- Every suggestion carries an effort tier: `0–2 min`, `5–10 min, one heat`, `15–25 min, familiar`, `new`.
- Ready-made and frozen parts are first-class food, not a failure to cook.
- Familiar-dish chaining: add lentils to the pasta already eaten before suggesting a new cuisine.
- Every experiment names its fallback: "if it's unpleasant, eat your usual meal; nothing needs compensating".

## Top principles, by strength of evidence

1. Keep calorie targets, "remaining", deficit/surplus and over/under off the default screen. *Strong for caution.*
2. Regular eating before macro optimisation. *Moderate–strong.*
3. One active next step; other patterns stay silent. *Moderate for specific plans, low for the exact count.*
4. Add rather than restrict when either works. *Moderate.*
5. Never praise eating less, staying under, losing weight or compensating. *Moderate–strong harm signal.*
6. Detailed numbers are opt-in and easy to hide again. *Moderate.*
7. Describe meal timing; never score punctuality. *Moderate–strong for regular eating.*
8. No logging streaks. *Moderate qualitative harm signal; no evidence they help.*
9. Celebrate flexibility, problem-solving and coming back. *Moderate.*
10. After repeated misses, shrink the step; never add pressure. *Moderate, inferred for food.*
11. Keep safe foods while experimenting. *Moderate clinical rationale.*
12. Novel-food experiments are tiny and never the whole meal. *Preliminary–moderate.*
13. Ready meals, frozen parts and assembly are legitimate tools. *Low–moderate direct, strong pragmatic.*
14. Cooking self-efficacy through harder operations, not novelty. *Moderate for self-efficacy, low for the ladder.*
15. Never let an adaptive algorithm optimise engagement or target attainment alone. *Strong conceptual rationale.*

## Don't build on these

- "Any calorie tracking causes eating disorders": experiments don't show it (but high-risk groups go unstudied).
- "Ranges are proven safer than exact numbers"; "weekly feedback is proven safer than daily": plausible, untested.
- "Four hours is the optimal maximum gap": stronger than the evidence.
- "One habit at a time is scientifically optimal": specificity is supported, the count is not.
- "Crowding in removes unhealthy foods on its own".
- "Food chaining / sensory integration are evidence-based adult treatments".
- "Batch cooking is best for low-energy people": one tiny pilot.
- "Gamification improves recovery": depends entirely on what is rewarded.

## Gaps

- No RCT compares nutrition-feedback interfaces in adults with an ED history.
- No evidence on an ED-safe objective for adaptive step selection (regularity vs adequacy vs effort vs disgust).
- Adult selective eating at the meeting point of autism, depression and veganism is barely studied; the cost of a
  failed new meal is unmeasured.

**Evaluation stance.** Be conservative about what can harm and experimental about what may help. Hard-code
guardrails against restriction, compensation, punitive scoring and compulsive engagement. Treat ranges, weekly
summaries, additive nudges, effort tiers and food chaining as hypotheses. Judge them by distress, skipped meals,
compulsive checking and willingness to come back after a hard week, not by logging frequency.
