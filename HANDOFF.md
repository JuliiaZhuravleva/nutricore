# Handoff — main (2026-07-08)

## State
On **`main`**, in sync with **`origin/main`** (`846fb28`), clean tree. **325 tests green**
(`./scripts/test.sh` — cache-venv; `poetry run` **also works now**, TD-001 fixed on the branch below).
Round-2 product-lookup is **merged + deployed** — `origin/main` `d6f0c64` is the `nutricore-release`
merge of `plan/photo-product-lookup-round2` (label-OCR 🏷 + web-search 🌐 strategies live).

**Release protocol** (`docs/RELEASE.md`): runtime work ships as a pushed feature branch handed to
openclaw-setup's `nutricore-release`; docs/no-runtime-change merge to main directly.

## Awaiting release — one branch
**`fix/td-015-confirm-buttons`** (pushed; 2 commits, clean diff vs `origin/main`):
- **TD-015** (`46b1357`, runtime) — meal-confirm step: added **Да/Нет** buttons (`confirm_keyboard`),
  and a free-text reply is now treated as a **correction** (re-analyzed as text, stays in
  `CONFIRMING_MEAL`, `meal_time` preserved, prior attempt's stale photo/`resolution_source` dropped)
  instead of the old `if text == "Да": save else: discard+restart` that silently wiped the analyzed
  draft on a lowercase `да` / mistyped `Нет` / real correction (owner hit it live). `_confirm_intent`
  classifies affirm/reject/correction (case-insensitive, punctuation-tolerant). Touches only
  `app/services/telegram.py` (`confirm_meal` / `_run_meal_analysis`). Photo+text *merge* still out of
  scope (Gap ① → TD-013). 5 new tests.
- **TD-001** (`2ffada7`, dev-only) — `.python-version` (3.12.1) pins the interpreter so the poetry
  venv base can't dangle again; `poetry run` works. No Docker/deploy impact. (Bundled here rather than
  merged to main directly to avoid a `_tech-debt.md` split.)
- **No migration, no new env, no manual step.** 325 green.
- **Relay:** `nutricore-release fix/td-015-confirm-buttons`.

## Shipped to origin/main this session (docs-lane, pushed)
6 docs commits (`d6f0c64..846fb28`): input-processing diagram, `.gitignore` (macOS), TD-013/014,
TD-015 entry, research doc + TD-016, handoff refresh.

## Open debt — all Low (Critical/High/Medium: none)
TD-007/008 (self-heal coverage + `telegram.py` decomposition), TD-010 (inbound bytes/delete/
reprocess→meal), TD-011 (product-lookup accuracy residuals), TD-012 (flake8 in product-lookup tests),
**TD-013** (confidence gate — 3 scores + quick-select buttons), **TD-014** (personal-DB/RAG reuse),
TD-016 (Responses-API `web_search` GA). **TD-013 / TD-014 are the big «Следующее» tracks** (from the
input-processing diagram) — most weight, largest friction/cost win for a repeat-logging single owner.
_(TD-015 + TD-001 → Resolved once `fix/td-015-confirm-buttons` merges; the Resolved entries already
live on that branch's `_tech-debt.md`.)_

## Gotchas / learnings
- **Tests:** `./scripts/test.sh` (cache-venv) is canonical/allowlisted. `poetry run` now works too
  (TD-001 pin), but test.sh stays the source of truth.
- **SessionStart HANDOFF-hook cruft** (`HANDOFF.md` deleted + a timestamped copy) trips the
  plan-fixes/execute **scope gate** → false-positive `GATE FAIL … exit 71`. Clean with
  `git checkout HANDOFF.md; rm HANDOFF-*.md`.
- **execute-plan specialists sometimes don't commit** doc/test deliverables — `git status` in the
  worktree after a run and commit strays.
- `get_openai_service()` is the shared singleton — services use it, never import the handler layer.
