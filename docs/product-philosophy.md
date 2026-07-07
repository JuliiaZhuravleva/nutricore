# Product philosophy — Nutricore

> The "why" and the "how it should feel" behind the bot. When a design or
> implementation decision is ambiguous, this doc is the tie-breaker. Kept short on
> purpose — principles, not a spec.

## What Nutricore is

A **personal** nutrition tracker on Telegram: capture meals with minimal friction
(text/photo → КБЖУ via AI) and chat about food. It is **not** a multi-user product.

**Ecosystem boundary:** `my-health` = brain / data / guardrails; **nutricore = the capture
+ chat surface.** Nutrition/food coaching uses nutricore's own OpenAI; medical/health
questions go to my-health via the `/consult` relay. These never mix, and no medical logic
or medical data lives in the bot.

## North star

**Give the user the best-guess result seamlessly — but keep every step transparent and
correctable.**

The product should feel effortless *and* honest at the same time. We optimize for four
things together, not one at the expense of the others:

- **Simplicity** — logging a meal is fast and low-effort.
- **Flexibility** — the system adapts to the case (packaged vs. cooked, photo vs. text)
  instead of forcing one rigid flow.
- **Maximum accuracy** — use the best source available for the numbers (a structured
  product database beats an eyeballed guess).
- **Transparency + the ability to intervene** — the user can always see *how* a number was
  produced and can correct it.

## Operating principles

These are the concrete rules that flow from the north star. Apply them to every user-facing
flow.

1. **Decide, don't nag.** When the system can reasonably infer something (which lookup path
   to use, the portion size, the product), it decides and shows a best guess. Don't stop the
   user with an extra question when a good default exists. Re-questioning is a last resort,
   not a habit.

2. **Ambiguity → offer a choice, don't guess silently.** When the system genuinely can't
   tell which path is best, surface the options as buttons and let the user pick. Automatic
   when confident; a quick chooser when not.

3. **Always show the path and the intermediate data.** The reply must make visible *which*
   route produced the numbers and the key signals along the way — which barcode/EAN was
   read, which product matched, per-100g vs. scaled grams, the confidence tier. A wrong turn
   must be *visible*, never silently baked into a saved meal. The failure we are designing
   against: *"the AI decided to read a barcode, matched the wrong product, set the wrong
   portion — and we couldn't even see at which step it went wrong."*

4. **Label confidence honestly.** A barcode-database number and a web-search guess are not
   equally trustworthy, and the UI must say so (e.g. `по штрих-коду (точно)` · `нашли в базе
   (проверь)` · `оценка по фото`). Never present a low-confidence number with high-confidence
   framing.

5. **Correcting is always one step away.** The user can adjust any surfaced value (grams,
   product, macros) at the existing confirmation step before it is saved. Best-guess defaults
   remove friction; the correction path keeps the user in control.

6. **Learn from the misses.** Record where the pipeline mispredicts — wrong product, wrong
   grams, wrong path — as data to tune the system over time. Every visible mistake the user
   corrects is a signal we should be able to capture and improve on later, not just discard.

## How this shows up in code

- **A pluggable resolution pipeline**, not hard-wired branches. Meal analysis runs through
  ordered strategies (barcode → structured DB, name search, label OCR, vision fallback) that
  can be extended and reordered per case. New accuracy paths are drop-in, not rewrites.
- **The resolution path is recorded**, not just the final numbers — on the meal and/or in
  `ai_call_logs` — so history shows how each entry was derived and so misses are analyzable
  (pairs with the inbound-persistence work, TD-009).
- **Best-guess + transparent + correctable** beats "refuse to answer until certain." We show
  our best answer with its confidence, not a wall of clarifying questions.

## Non-goals (what this philosophy is *not* a license for)

- Not a reason to add friction "for safety" — transparency is shown, not interrogated.
- Not multi-user product polish (onboarding funnels, paywalls for strangers, dashboards).
  See ROADMAP "Non-goals".
- Not a place for medical logic — that boundary belongs to my-health.

---

_Related: [`ROADMAP.md`](../ROADMAP.md) (direction + non-goals), [`docs/stages/`](stages/)
(staged plan), [`docs/photo-product-lookup.md`](photo-product-lookup.md) (the feature that
first crystallized these principles)._
