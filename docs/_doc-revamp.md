# Docs revamp — scoping brief (deferred)

> **Trigger:** start this **after** the `photo-product-lookup` plan execution is launched
> (so it runs alongside the specialists' implementation, not before). Agreed with Julia
> 2026-07-07. Delete this file once the revamp is done.

## Goal

Go through **all** project documentation and properly structure + actualize it. Four
objectives, all in scope (Julia picked all):

1. **Single source of truth** — kill duplication between `ROADMAP.md`, `docs/stages/`, and
   `_tech-debt.md`. One topic lives in one place; others link to it.
2. **Split human-facing vs AI-facing docs** — separate docs meant for a person (README,
   ROADMAP, product-philosophy, stages) from agent/assistant instructions (`CLAUDE.md`,
   coordination files, `_registry.md`) so they don't tangle.
3. **Actualize** — prune stale content and mark what is current. Many layers accreted across
   sessions (HANDOFFs, roadmap rewrites, tech-debt churn); reconcile to present reality.
4. **Navigation / index** — a clear docs entry point (`docs/README.md` or top-level index)
   so anything is findable in one hop.

## Audience constraint

The repo is **public** (a showcase/portfolio) but the product is a **personal single-user**
tool. So: docs must read cleanly for an outside visitor (accurate README, coherent ROADMAP),
without pretending to be a multi-user product. Keep the honest "personal tool" framing.

## Known inputs / current doc surface

- `README.md`, `ROADMAP.md`, `Business_description.md` (root)
- `docs/stages/` (staged plan, source of truth for the build order)
- `docs/product-philosophy.md` (the north-star principles — new, 2026-07-07)
- `docs/photo-product-lookup.md` (feature spec) + `docs/plans/` (execution envelopes)
- `_registry.md`, `_tech-debt.md` (coordination/AI-facing)
- `CLAUDE.md` (AI-facing project guide — large)
- Stray `HANDOFF-*.md` timestamped files (cruft — candidates to prune)

## Not now

Do **not** start the mechanical restructure yet. This file only records the agreed scope so
it survives context compaction. Kick off when execution is running.
