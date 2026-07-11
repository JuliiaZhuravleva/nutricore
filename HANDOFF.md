# Handoff — main (2026-07-11)

## State
On **`main`**, synced with **`origin/main`**. **403 tests green** (`./scripts/test.sh`).
**TD-014 (personal-food DB + RAG reuse) shipped & deployed.** One worktree remains:
`../nutricore.personal-food-db-wt` on `plan/personal-food-db` — now fully represented on main,
so it's safe to `git worktree remove` + delete the branch (local + remote) when convenient.

**Release protocol** (`docs/RELEASE.md` + `.claude/wrap.md`): runtime work ships as a pushed
feature branch → openclaw-setup's `nutricore-release`; docs / no-runtime-change merge to main directly.
**Git transport:** corporate **ESET blocks SSH port 22** — git goes over **443** (`~/.ssh/config`
routes `github.com` → `ssh.github.com:443`, host key trusted). Push/pull work normally now.

## Shipped this session — TD-014 via the `plan/personal-food-db` plan (ADR-0003)
- New `personal_foods` + `personal_food_embeddings` (pgvector `VECTOR(1536)` + HNSW),
  `SavedFoodRAGStrategy` (`saved_rag`: exact-barcode short-circuit + user-scoped embedding ANN,
  registered after `barcode_off`), `OpenAIService.embed_text`, and a **Celery fire-and-forget learning
  loop** (upserts each confirmed food + alias embeddings, dedup). Deploy: Postgres image →
  `pgvector/pgvector:pg15`; **no new required env**. Merged via `nutricore-release` (`9505093`, `e6ccb92`).
- **`/review-deep` (F1–F11) before release** caught a **critical `Decimal×float` bug** (feature was dead
  on Postgres, hidden by the SQLite test gap) + barcode-vs-fuzzy ordering + a Celery event-loop bug; all
  fixed, +5 regression tests, 403 green.
- **Lock-drift gate:** `./scripts/test.sh` now runs `poetry check --lock` before pytest — a dep added to
  `pyproject.toml` without re-lock passes the prebuilt-venv suite but breaks the clean Docker build (this
  session's pgvector drift, fixed in `444de52`).

## Next up — remaining `_tech-debt.md` (all Low)
- **TD-013** — confidence gate (three scores identity/portion/nutrition + quick-select clarification).
  Big «Следующее» track; now has the personal-DB match as its strongest *identity* signal (it was
  deliberately sequenced **after** TD-014). Start via `/plan-fixes`.
- **TD-017** — quick-pick from saved/recent (the deferred **B5** of the personal-food-db plan). Own small
  plan: ReplyKeyboard vs InlineKeyboard. `times_used` / `last_used_at` on `personal_foods` already feed it.
- **TD-007/008**, **TD-010**, **TD-011**, **TD-012** (quick flake8), **TD-016** (web_search GA).

## Gotchas / learnings
- **Git over 443** (ESET blocks 22) — works via the ssh config route; on a fresh host,
  `ssh-keyscan -p443 ssh.github.com` (verify fingerprints vs GitHub's published) → `known_hosts`.
- **Lock drift is invisible to the local gate by design** (prebuilt venv) — now guarded by
  `poetry check --lock`; always regenerate `poetry.lock` when adding a dep. [[poetry-lock-drift-gate-blindspot]]
- **SQLite masks Decimal bugs:** `Numeric` → `float` on SQLite but `Decimal` on Postgres, so
  `Decimal*float` TypeErrors pass local tests (that was F1). Also the `db_session` fixture can't survive
  an in-code `rollback()` — use a standalone real session. [[sqlalchemy-sqlite-test-gotchas]]
- **SessionStart HANDOFF cruft** (`HANDOFF.md` deleted + a timestamped copy) trips the plan-fixes/execute
  scope gate → false-positive `GATE FAIL … exit 71`. Clean with `git checkout HANDOFF.md; rm HANDOFF-*.md`
  (hit it on every plan-fixes/execute run this session).
- **execute-plan specialists sometimes don't commit** deliverables (the architect left ADR-0003
  uncommitted) — `git status` the worktree after a run.
- **envelope.py / poetry need a yaml-capable `python3`** — a refreshed shell may resolve `python3` to a
  non-pyenv interpreter without PyYAML; use `~/.pyenv/versions/3.12.1/bin/python`.
