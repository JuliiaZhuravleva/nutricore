# Handoff — main (2026-07-12)

## State
On **`main`** (`8f19bb8`), synced with `origin/main`. **405 tests green** (`./scripts/test.sh`).
Worktree cleaned up (the old `plan/personal-food-db` worktree + branch are gone, local + remote).

One **feature branch pushed, awaiting release** (see below). Otherwise tree clean.

**Release protocol** (`docs/RELEASE.md` + `.claude/wrap.md`): runtime work ships as a pushed feature
branch → openclaw-setup's `nutricore-release`; docs / no-runtime-change merge to main directly.
**Git transport:** ESET blocks SSH 22 — git goes over **443** (`~/.ssh/config` routes `github.com` →
`ssh.github.com:443`, host key trusted).

## Shipped this session — Low-backlog debt sweep
- **TD-012** (flake8) — **landed on `main`** directly (`8f19bb8`, no-runtime cleanup): dropped unused
  imports + a dead no-op helper (undefined `_patched_extract_signals`, F821) in the product-lookup test
  files, and the long-standing `telegram.py` F841 `except ValueError as ve`.
- **TD-011** — reviewed, **won't-do** (kept open as an accepted residual): both parts (barcode
  check-digit collision downgrade, portion semantics) were *deliberately* not implemented — a
  name-vs-vision token-overlap check false-positives on correct matches (OFF English/brand vs Russian
  vision) and worsens UX. Nothing to action.
- **TD-010** — **deferred to its own mini-plan** (owner call): the disk-bytes archival needs a new
  bind-mount + compose volume on the mini (a manual deploy step at openclaw-setup), plus `/forget` and
  reprocess→meal are separate features. Not a "quick" item.

## Pending release — branch `fix/td-007-008-016-model-selection`
Runtime work, pushed, **awaiting `nutricore-release`** (do NOT self-merge). **405 green.**
- **TD-007 / TD-008** — extracted the persisted OpenAI model override out of `telegram.py` into a new
  **`app/services/model_selection.py`** (mirrors `access_control.py`): `OPENAI_MODEL_SETTING_KEY`,
  `get_persisted_model` / `persist_model` / `apply_persisted_model`. `OpenAIService.__init__` now
  best-effort loads the override so *every* instance (incl. the unmounted `ai.py` per-request path)
  honours the owner's in-chat model switch. Conftest autouse keeps unit construction hermetic; +2 tests.
- **TD-016** — `web_search_nutrition` moved to the GA Responses-API tool `{"type": "web_search"}`
  (from legacy `web_search_preview`); behaviour preserved, GA shape verified against OpenAI docs.
- **Release note:** NO new required env var, NO migration, NO schema/deploy delta (uses the existing
  `app_settings` table). Plain code-only release — no manual `sudo` step on the mini side.

## Next up — remaining `_tech-debt.md` (all Low)
- **TD-017** — quick-pick from saved/recent (deferred B5). Own small plan: ReplyKeyboard vs Inline.
- **TD-013** — confidence gate (identity/portion/nutrition + quick-select). Big track, via `/plan-fixes`;
  now has the personal-DB match as its strongest identity signal.
- **TD-010** — disk-bytes archival + `/forget` + reprocess→meal (own plan, deploy coordination).

## Gotchas / learnings
- **isort has no committed profile** but the repo is formatted **black-style** (`isort --profile black`);
  plain `isort` reformats into a black-incompatible style, and `main` is "dirty" under the default
  profile too. Use `isort --profile black` + `black` on **touched files only**. [[black-scope-touched-files]]
- **TD-007 side effect:** `OpenAIService()` now reads the DB on construction. Kept hermetic in tests via
  a conftest autouse that nulls `openai_service.get_persisted_model`; self-heal tests drive the
  `model_selection` module directly (patch `ms.SessionLocal`), so they're unaffected.
- Git over **443** (ESET blocks 22); the gate `./scripts/test.sh` is lock-drift-guarded
  (`poetry check --lock`) but does NOT run flake8/black — formatting is manual/scoped.
