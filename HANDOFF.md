# Handoff — main (2026-07-09)

## State
On **`main`**, in sync with **`origin/main`** (`36d9118`), clean tree, no stray branches or worktrees.
**325 tests green** (`./scripts/test.sh` — cache-venv; `poetry run` also works now, TD-001 fixed).
Nothing awaiting release. Git is tidy: `main` is the only branch (local + remote).

**Release protocol** (`docs/RELEASE.md` + `.claude/wrap.md`): runtime work ships as a pushed feature
branch handed to openclaw-setup's `nutricore-release`; docs/no-runtime-change merge to main directly.

## Shipped & deployed (recent)
- **TD-015** (`46b1357`) — meal-confirm step: **Да/Нет** buttons (`confirm_keyboard`) + a free-text
  reply is treated as a **correction** (re-analyzed as text, stays in `CONFIRMING_MEAL`, `meal_time`
  preserved, stale photo/`resolution_source` dropped) instead of silently discarding the draft.
  `_confirm_intent` classifies affirm/reject/correction (case-insensitive). Photo+text *merge* still
  out of scope (Gap ① → TD-013). Merged+deployed via `nutricore-release` (`36d9118`).
- **TD-001** (`2ffada7`) — `.python-version` (3.12.1) pins the interpreter so the poetry venv base
  can't dangle; `poetry run` works again. `./scripts/test.sh` stays canonical/allowlisted.
- Earlier: round-2 product-lookup (label-OCR 🏷 + web-search 🌐) merged+deployed (`d6f0c64`).

## Next up — work through the rest of `_tech-debt.md` (all **Low** now)
Medium/High/Critical: **none**. Open (Low):
- **TD-013** — confidence gate: three separate scores (identity/portion/nutrition) + minimal
  clarification via quick-select buttons. Big «Следующее» track (from `docs/diagrams/input-processing-flow.md`).
- **TD-014** — personal-food DB + RAG reuse (quick-pick saved meals, text→JSON→retrieval). Big track;
  likely the largest friction/cost win for a repeat-logging single owner.
- **TD-007 / TD-008** — self-heal coverage on the unmounted `ai.py`; extract self-heal + reply
  formatting out of the ballooning `telegram.py` (`model_selection.py`, `ResolutionResult.to_reply_lines()`).
- **TD-010** — inbound bytes archival / `/forget` / reprocess→meal.
- **TD-011** — product-lookup accuracy residuals (misread barcode past check-digit; portion semantics).
- **TD-012** — flake8 cleanup in product-lookup test files (quick win).
- **TD-016** — Responses-API `web_search_preview` → GA `web_search`.

TD-013 / TD-014 are large → start each with `/plan-fixes`. TD-012 is a quick isort/flake8 pass.

## Gotchas / learnings
- **Tests:** `./scripts/test.sh` (cache-venv) is canonical/allowlisted. `poetry run` now works too
  (TD-001 pin), but test.sh stays the source of truth.
- **SessionStart HANDOFF-hook cruft** (`HANDOFF.md` deleted + a timestamped copy) trips the
  plan-fixes/execute **scope gate** → false-positive `GATE FAIL … exit 71`. Clean with
  `git checkout HANDOFF.md; rm HANDOFF-*.md`.
- **execute-plan specialists sometimes don't commit** doc/test deliverables — `git status` in the
  worktree after a run and commit strays.
- `get_openai_service()` is the shared singleton — services use it, never import the handler layer.
