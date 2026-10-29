# Vegan slices — which nutrition signals to show, day by day and week by week

Research date: 2026-10-29. Source: a ChatGPT Deep Research run ("DR2"), condensed and re-labelled here. Follows
[`review-cards-scoping.md`](review-cards-scoping.md). The sibling on psychology is
[`gentle-feedback-evidence.md`](gentle-feedback-evidence.md); the build plan is
[`../stages/05-gentle-review.md`](../stages/05-gentle-review.md). Labels as in the sibling: **VERIFIED** (I read
the abstract), **DR** (the run says it read it; not re-checked by me), **INFERRED** (design inference).

**Boundary.** Everything here is a *capture and presence* signal. Nutrient status, deficiency and biomarkers are
medical and belong in my-health ([product philosophy](../product-philosophy.md), ecosystem boundary).

## 1. Diet-quality indices: borrow the food groups, never show the score

- Overall diet quality predicts mortality and chronic disease better than any single nutrient (HEI, AHEI, DASH;
  Schwingshackl 2015, PMID 25680825; Chiuve 2012, PMID 22513989). **DR**
- **hPDI** is not "all plants are good": the overall PDI is. hPDI rewards whole grains, fruit, vegetables, nuts,
  legumes and oils, and penalises refined grains, sweet drinks, juice, potatoes and sweets; uPDI does the reverse,
  with opposite risk associations (Satija 2016, PMID 27299701; Wang 2023, PMID 37789346). **DR**
- hPDI scores people into population quintiles, so it is relative, and every animal component is a constant zero
  for a vegan. **INFERRED**
- **PDQS** awards 0/1/2 per food group by weekly frequency; it predicts mortality (UK Biobank 2025, n = 124,851),
  but scores poultry, fish and eggs positively. **DR**
- **HEI-2020** needs amounts per 1,000 kcal and has a dairy component; **AHEI** needs EPA+DHA, sodium and grams.
  Both are fragile to noisy AI logs. **DR / INFERRED**
- **DQQ** asks 29 yes/no food-group questions about the previous 24 hours; it was built for low-burden
  population monitoring of nutrient adequacy and NCD protection (Herforth, Ballard & Rzepa 2024,
  *Curr Dev Nutr* 8(8):103798, PMID 39161595). **VERIFIED**
- **GDQS**: 25 groups with coarse gram bins; scores animal foods positively. **DR**

**Confidence.** High that a few food-group indicators beat a displayed index score; moderate that a custom vegan
presence set keeps the parents' health relevance (it is itself unvalidated).

**Design implications.** INFERRED
- **Day:** a small positive list of what was there, e.g. «Сегодня было: 🫘 бобовые · 🥬 овощи · 🌾 цельное зерно».
- **Week:** days per group, e.g. «бобовые 5 из 7 дней».
- Never call it PDQS or hPDI; never show a synthetic score or a negative item.

## 2. Plant diversity and "30 plants a week"

- "30" comes from the American Gut Project: a self-selected, cross-sectional comparison of >30 vs ≤10 plant types
  per week and microbiome differences. It was the extreme category, not a dose-response cutoff (McDonald 2018,
  *mSystems*). **DR**
- A 2025 scoping review found no standard definition, no validated measure and too little evidence for strong
  claims. One small crossover trial (n = 25, chronic kidney disease) saw microbiome and symptom changes. **DR**

**Confidence.** Moderate as a proxy for breadth; low that 30 means anything clinically.

**Design implications.** INFERRED
- No «22/30 — осталось 8». At most a weekly count with no target, plus "new this week: …".
- It sits below the anchors and can be left out on a low-energy week. A daily count is noise.
- For high disgust sensitivity, a hard 30 turns a weak observation into a pass/fail burden.

## 3. Nutrients of concern for vegans: what a log can and can't tell

Both position papers agree that planned vegan diets can be adequate and name a short watch list (Raj et al., 2025,
*J Acad Nutr Diet*, PMID 39923894, VERIFIED earlier; DGE / Klug 2024, DR).

| Nutrient | What the evidence says | Can a meal log estimate it? | Safe slice |
|---|---|---|---|
| **B12** | Little reliable B12 without supplements; supplement users have much better markers (Niklewicz 2024, PMID 39373282). **DR** | No. Supplement or fortified food only | A routine tick on the person's own schedule; weekly "routine logged" |
| **Iodine** | Vegans have the lowest intake and urinary iodine; depends on national fortification (Eveleigh 2023, PMID 36912094). **DR** | No: iodised salt is invisible, seaweed varies hugely | "Iodine source" days: iodised salt / fortified drink / labelled seaweed / supplement |
| **Calcium** | Clearly lower intake in vegans (74-study meta-analysis, PMID 38054787); aim for 2–3 servings of well-absorbed sources a day. **DR** | Poorly from photos; well from barcodes | "Calcium-rich food" days: fortified plant milk, calcium-set tofu, low-oxalate greens |
| **Vitamin D** | Lower intake and blood levels; sun, latitude and season dominate. **DR** | No | Optional routine tick |
| **EPA/DHA** | Low in vegans; ALA conversion is limited; algal oil raises levels (Lane 2022, PMID 33576691). No consensus that every healthy vegan needs it (2026 review). **DR** | ALA foods yes, status no | "ALA-rich food" days; optional algal-oil tick |
| **Iron** | Non-heme; absorption depends on vitamin C and phytate; certainty of lower ferritin is low. **DR** | Grams are noise | Iron-rich plant food, optionally paired with vitamin C at the same meal (weekly) |
| **Zinc** | Lower intake and status; phytate cuts absorption (Neufingerl 2021, PMID 35010904). **DR** | No | No own slice: legume and nut/seed days are its proxy |
| **Selenium** | Varies with soil and geography. **DR** | No | Background only; a tick if a supplement is used |
| **Bone** | Higher fracture risk in vegans in EPIC-Oxford (Tong 2020, PMID 33222682); mediators unresolved. **DR** | — | Never a "bone score"; the supports are calcium, protein and the vitamin-D routine |

**Confidence.** High for B12 as a supplementation matter; high for iodine and calcium deserving attention;
moderate–high for vitamin D, iron and zinc; moderate for selenium; low–moderate that every healthy adult needs
algal EPA/DHA.

**Design implications.** INFERRED
- Micronutrients are **weekly and behavioural**, never numeric: «B12 ✓ · йод 5 дней · кальций 6 дней».
- A routine on a weekly schedule never shows a daily miss.
- Never "looks low" or "not enough": the bot cannot know status, and that wording is medical.

## 4. Protein

- The adult reference is about 0.8 g/kg/day; a 2025 review found no basis to replace it for healthy adults. Plant
  eaters' intake is lower but usually within range (Neufingerl 2021). **DR**
- Lysine is the usual limit in grain-heavy vegan diets; legumes and soy supply it. In 193 New Zealand vegans,
  legumes were the top lysine source (Soh 2025, PMID 40238722). **DR**
- Same-meal "complementary protein" is too rigid when the day is varied; legume meals score higher on meal-level
  DIAAS (2026, PMID 41769645). **DR**
- 20–30 g per meal for muscle protein synthesis is sports and ageing physiology, not a general-health rule. **DR**

**Confidence.** High that total protein and regular legume/soy matter; moderate for amino-acid quality; low–moderate
for per-meal targets in healthy non-athletes.

**Design implications.** INFERRED
- Day: **protein anchors present** («тофу в обед · чечевица вечером»), never a red/green gram target.
- Week: days with a substantial legume/soy anchor.
- Grams per kg only behind the numbers layer, and only if asked.

## 5. Day vs week, and meal timing

- A single day is a poor estimate of usual micronutrient intake. **DR**
- Fibre: WHO recommends at least 25 g/day; habitual intake is what predicts outcomes. **DR**
- Meal frequency: very-low-certainty evidence, no cardiometabolic advantage (Blazey 2023, PMID 37964316). Nordic
  2023 review: timing and regularity evidence too inconsistent for recommendations (PMID 38370117). **DR**
- Time-restricted eating has small, uncertain effects and is especially questionable after an ED. **DR**

**Confidence.** High that micronutrients and diversity belong to the week; moderate for daily presence; low–moderate
for meal timing *as a nutrition metric*.

**Design implications.** INFERRED
- Day: "what useful things were present?". Week: "which patterns repeated often enough to count?".
- The week never becomes seven pass/fail days.
- Meal rhythm stays a **self-care** line (the psychology doc backs it), never a diet-quality score. Never push
  fasting or time-restricted eating.

## 6. How accurate AI meal logging is

- Image methods underestimate energy by about 179 kcal on average (Ho 2020, PMID 32839035). In 52 AI studies,
  calorie error ran 0.1–38% (Shonkoff 2023, PMID 38060823). **DR**
- ChatGPT on 114 meal photos: good food identification, but >10% error on 13 of 16 nutrients, worse on big portions
  (2025, PMID 40004936). GPT-4o, Claude and Gemini: about 36% mean absolute error on weight and energy
  (PMID 41081011). **DR**
- Text with explicit foods and amounts agrees well for energy and macros, poorly for micronutrients (2026,
  *J Nutr*). **DR**

**Robustness order** (INFERRED): food identity/presence > food group > barcode nutrients > rough protein/energy
ranking > exact grams and macros > micronutrients from photos.

**Design implications.** INFERRED
- The visible card is built from food identities and food groups.
- Attach a confidence to the **source**: barcode or known recipe > typed amount > text without amount > photo with
  scale > photo without. This fits the existing pipeline ([ADR-0001](../decisions/)).
- A number from a photo is rounded hard or hidden.

## 7. Fibre, ultra-processed food, sugar, sodium, saturated fat

- Fibre is the best candidate for positive framing (WHO 2023). **DR**
- UPF: in plant-based cohorts, overall plant-food quality explains much of the signal (Harbers 2024,
  PMID 39180555; Thompson 2026). NOVA classification has only moderate validity and needs brand data. **DR**
- Sugar, sodium and saturated fat matter for general health. Sodium is invisible in photos, and iodised salt is
  also an iodine source. **DR**

**Design implications.** INFERRED
- «Основа из клетчатки»: legumes + whole grains + fruit + vegetables + nuts/seeds, as presence.
- No UPF percentage and no red UPF warning; at most "whole-food anchors".
- Sodium, sugar and saturated fat stay hidden unless the data are reliable and the user asks for them.

## 8. Low-effort anchor foods

Foods that cover several concerns with little cooking (DR for the nutrient facts; the list is INFERRED):
- fortified soy milk;
- tofu, especially calcium-set;
- canned lentils, beans and chickpeas;
- hummus;
- frozen edamame;
- peanut butter, tahini;
- walnuts, ground flax or chia;
- whole-grain bread or cereal;
- iodised salt;
- fortified foods the person already likes.

**Design implication.** The next step is **one high-leverage addition**: "few legume/soy anchors this week; the
easiest add is fortified soy milk, hummus or tofu — whichever bothers you least".

## Ranked slices

| # | Slice | Window | Data | Robust to logging error | Evidence |
|---|---|---|---|---|---|
| 1 | Legume / soy / protein-anchor presence | day + week | food identity | very high | high |
| 2 | B12 routine | the person's schedule + week | tick | very high | very high |
| 3 | Calcium-rich / fortified food | day + week | food identity, barcode | high | high |
| 4 | Iodine source | week | explicit tick or labelled food | medium–high if logged | high |
| 5 | Core plant groups: veg, fruit, whole grain, legumes, nuts/seeds | day + week | food groups | very high | high |
| 6 | Fibre-foundation days | week | same groups | high | high |
| 7 | ALA-rich food / optional algal oil | week | food identity or tick | high | moderate |
| 8 | Plant diversity, no target | week | unique plant names | very high | low–moderate |
| 9 | Protein at each eating occasion | day | food identity | high | moderate |
| 10 | Whole-food anchors | week | food identity + rough processing | medium–high | moderate |

## Don't build on these

- A "30 plants = good gut" badge.
- A raw hPDI/PDI score shown as a personal health score.
- "Complete protein at every meal" rules.
- Micronutrient deficiency warnings from photos.
- A "UPF bad" score.
- Meal-frequency or fasting targets as nutrition metrics.

## Gaps

- No validated diet-quality index exists for vegans logging by AI.
- There is no evidence for an optimal weekly plant count, or for how much of a plant must be eaten for it to count.
- Little vegan evidence links per-meal protein distribution to long-term outcomes.
- No validation of AI *food-group presence* classifiers on vegan mixed meals: we must check our own tagging.
- The nutrition literature ignores ED-sensitive presentation. The presence/addition/weekly framing is design
  inference, not an outcomes result.
