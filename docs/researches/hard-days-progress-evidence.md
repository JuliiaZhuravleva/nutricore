# Hard days and small progress — evidence for what the cards show

Research date: 2026-10-30. Source: a ChatGPT Deep Research run ("DR3"), condensed and re-labelled here. It answers
G5-R24: the bot starts from how a person eats now, hard days are the main case, and it has three jobs (objective
record, one small thing to do now, small progress when it happens). Siblings:
[`gentle-feedback-evidence.md`](gentle-feedback-evidence.md), [`vegan-slices-evidence.md`](vegan-slices-evidence.md).

Scenario under test: two large packs of chips and nothing else logged; about 3000 kcal of mostly ready-made food; the
first food of the day at 04:00; a week with no vegetables or little protein in the records; no change; a worse week; a
return after a month.

## How to read

- **VERIFIED**: I read the abstract in PubMed on 2026-10-30, and it matches what is said here. Eleven were read: Singh
  2025, Carrero 2019, Ellison 2016, Sheeran 2020, Allison 2010, Bruton 2025, Adriaanse 2011, Pawlow 2003, Hahn 2021,
  van der Haar 2023, Griffith 2024. All 36 PMIDs and 10 DOIs in the run exist and their titles fit.
- **DR**: the run reports it; I did not read the source. Includes every statement about what an app does today and all
  App Store and Reddit anecdotes. The run's other inline citations were markers that could not be followed.
- **INFERRED**: a design inference, the run's or mine. Nothing here tested a bot like this one, and the sample texts were
  not tested on anyone.

## Where the run claims more than its sources

- **"Regular eating" and Ellison 2016.** The run cites it for "greater regular meal consumption". The abstract says
  something narrower: in 80 adults with bulimia nervosa, more *evening* meals over treatment went with less bingeing and
  purging at follow-up. It says nothing about earlier food, and the population has a diagnosis. **VERIFIED**
- **Night eating.** Pawlow 2003 is 20 adults with night eating syndrome and a week of relaxation training, with "a trend"
  towards more breakfast. Allison 2010 is an uncontrolled pilot of 25 patients with ten therapist sessions. Neither tests
  moving food earlier in people without the syndrome. **VERIFIED** So «one earlier moment to eat» is a product hypothesis
  with weak support, and it must be labelled as one in the bot's own explanation.
- **Four days.** Singh 2025 (958 Swiss participants, research app) found that 3–4 days give a reliable estimate of *usual
  intake* for most nutrients and for food groups like vegetables, ideally non-consecutive with a weekend day. It did not
  study detecting change in one person. The threshold below is our heuristic. **VERIFIED**
- **Shame is common.** Hahn 2021 is 20 students: 9 more negative feelings, 7 more positive, 2 both, 10 more concern about
  weight or shape. Variation is the finding, not a rate. **VERIFIED**
- **Priority.** The run says Job 2 outranks Job 3 "in their stated priority order". The owner stated no order. Its evidence
  for doing over reviewing is its own inference.
- **Its sample texts** use masculine or split forms («Записал», «ел_а»), against G5-R19. Everything is rewritten.

## Findings

### Why people leave
- Effort is the most reported reason for leaving nutrition apps (van der Haar 2023, mixed methods). **VERIFIED** Tedious,
  inaccurate entry and forgetting recur in qualitative work. **DR**
- Red displays and budgets led some users to feel shame and then skip, omit or alter entries (Cordeiro 2015; McCaig 2020).
  **DR** Recovery Record users split: the same reminder or affirmation was structure for one and surveillance or
  childish for another (2018, DOI 10.2196/10253). **DR**
- No evidence sets the best number or length of food feedback messages: 19 randomised studies, mixed designs, no format
  emerged as better (2024, DOI 10.1186/s12966-023-01555-6). **DR** "Short and few" rests on burden, not on trials.

### Feedback and motivation
- Feedback on average helps, yet worsened performance in over a third of effect sizes, more so when attention moves from
  the task to the self (Kluger & DeNisi 1996). **DR**
- Support for choice has a small, reliable effect: 65 tests, d = 0.23, still ≥ 0.15 after bias correction (Sheeran 2020).
  **VERIFIED**
- Specific if-then plans work better for adding than for cutting: d = 0.51 vs 0.29 (Adriaanse 2011, authors warn some
  effects are inflated) and d = 0.33 vs 0.18 (Carrero 2019). Specific plans beat complex ones, and an instructor
  checking the plan lowered the effect. **VERIFIED** That last point is weak ground, but a bot asking «did you do it?»
  has no support here. **INFERRED**
- There is no substantial evidence that "always add, never restrict" is better in general. The narrow finding above is
  what we can lean on. **DR**
- A lapse followed by self-blame predicts giving up (Carels 2004). **DR** So nothing symbolic resets after a hard day. **INFERRED**

### Progress against the person's own past
- No validated threshold says when a change in casual food logs is real. Intake varies by weekday, and logs miss snacks. **DR**
- Three states, not two: movement, no clear movement, too little information. **INFERRED**
- Compare only comparable windows, and prefer counts of days over a single day. **INFERRED**
- Streaks motivate while intact, and a break cuts engagement (2022, DOI 10.1093/jcr/ucac029). **DR** No head-to-head with
  tallies exists. A count of days survives a missed day, which is the reason to use it. **INFERRED**
- Habit timing varies from weeks to many months (Lally 2009). **DR** A focus has no set length; two weeks is a first look,
  not a deadline. **INFERRED**

### Night, chips, absence, protein
- Night eating syndrome is a diagnosis with distress and impairment, not late eating. The bot never names it. **DR**
- Clock time is a poor stand-in for a person's day: eating timing goes with sleep timing (Griffith 2024). **VERIFIED**
- Sensing the body's own signals tends to be weaker with ADHD traits (Bruton 2025, moderate quality; hunger itself was not its measure), so a cue like waking or first
  coffee may suit better than «eat when hungry». **VERIFIED** for the review, **INFERRED** for the cue.
- A controlled inpatient trial found about 500 kcal/day more on an ultra-processed diet (Hall 2019). **DR** It tested whole
  diets and does not justify a verdict on one pack or a personal score. **INFERRED**
- Calories estimated from a photo are unreliable, and calorie displays are a known shame trigger, so a high day gets no
  calorie line by default. **INFERRED**, in line with G5-R01.
- Protein raises satiety short term; long-term appetite effects are unclear (Kohanmoo 2020). **DR** Not a cure for night eating.
- Eating-disorder guidance asks to avoid language that feeds poor self-worth (Crone 2023). **DR**

## The run's hypotheses

| | Hypothesis | Result |
|---|---|---|
| A | Name foods plainly, chips included | Survives |
| B | Progress line only when real, otherwise silence | Changed: say «no clear change» when the data support it, and say so when too few records |
| C | The person picks one focus of 2–3 | Survives, the strongest result |
| D | First focus: earlier first meal | Partly: «one earlier moment to eat» as one offer when gaps are long and food is late. Weak evidence |
| E | After 20:00 the step moves to tomorrow | Refuted as written: use the person's own day, not a clock |
| F | Fewer, shorter reports, the summary leads with what moved | Survives as a product hypothesis; Sunday has no evidence |

## What the run proposes to change (owner decides, see G5-R25 onward once decided)

1. Rule 2 «never say what was missing» becomes: name an absence only weekly, when logs are complete enough, as a fact
   about the records («В записях за неделю овощей не видно»). Rule 7 stays for verdicts on the person.
2. «No comparison» becomes: compare with the person's own past on the chosen focus, and say so too when it went the
   other way. Never with an ideal. G5-R09 (plants, no target) stands.
3. «Praise only process» becomes: name a specific thing that was seen. No praise after each entry.

## What not to build on

- Positive feedback always helps; negative feedback motivates; a known best report frequency.
- Earlier food reduces night eating in general.
- A percentage or day count that proves a change. Any «real progress» claim beyond plain counts.
- Logging frequency as a success measure: more can mean compulsion.

## Safety

- No diagnosis from logs: night eating syndrome, binge eating, deficiency.
- The run lists signals that call for a clinician: vomiting, laxatives or diet pills, fainting, palpitations, rapid weight
  loss, long severe restriction, loss of control with distress, self-harm. Urgent wording for acute danger. **DR**
- Signs around the bot itself: ever smaller entries, editing amounts down, hiding foods, requests for lower targets. No
  validated classifier exists. **DR** Not automated here.

## Measures (none validated, all INFERRED)

The chosen action happening more often, coming back after a gap, feedback felt as accurate rather than judging, «I hid or
changed food because of the app» staying rare, and the record still working with feedback switched off.
