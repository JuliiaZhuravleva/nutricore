# Handoff — main (2026-07-23)

## State
On **`main`**, based on release merge `89948d4`, synced with `origin/main` at wrap time except
this handoff commit (see ⚠ below). **405 tests green** — ran twice as `nutricore-release` gates.
**No pending release branches** — the local `fix/td-007-008-016-model-selection` branch is deleted
(merged); the remote copy still exists on origin.

⚠ **Unpushed:** this handoff commit is local-only (wrap ran without `--push`). When convenient:
`git push` + delete the merged remote branch (`git push origin --delete fix/td-007-008-016-model-selection`).

**Release protocol** (`docs/RELEASE.md` + `.claude/wrap.md`): runtime work ships as a pushed feature
branch → openclaw-setup's `nutricore-release`; docs / no-runtime-change merge to main directly.
**Git transport:** ESET blocks SSH 22 — git goes over **443** (`~/.ssh/config` routes `github.com` →
`ssh.github.com:443`, host key trusted).

## Shipped this session — TD-007/008/016 RELEASED to the mini
`fix/td-007-008-016-model-selection` (tip `8b84afa`) went out via openclaw-setup's
`nutricore-release` → merge `89948d4`, all six steps green (405 tests, single alembic head, env
map covers required Settings, images built, no migrations, services Up, bot polling). Code-only
release: new `app/services/model_selection.py` (persisted model override extracted from
`telegram.py`; every `OpenAIService` instance honours the in-chat model switch), `web_search_nutrition`
on the GA `web_search` tool. `_tech-debt.md` already reflects TD-007/008/016 as Resolved (done on
the branch). Local tree was also tidied: a stray `HANDOFF-20260716-233146.md` backup (byte-identical
to the tracked HANDOFF) removed, deleted `HANDOFF.md` restored, main fast-forwarded.

## Next up — remaining `_tech-debt.md` (all Low)
- **TD-017** — quick-pick from saved/recent (deferred B5). Own small plan: ReplyKeyboard vs Inline.
- **TD-013** — confidence gate (identity/portion/nutrition + quick-select). Big track, via `/plan-fixes`;
  now has the personal-DB match as its strongest identity signal.
- **TD-010** — disk-bytes archival + `/forget` + reprocess→meal (own plan, deploy coordination).
- (TD-011 stays open as an accepted residual — reviewed 2026-07-12, nothing to action.)

## Gotchas / learnings
- **Non-interactive shells on the MacBook lack `/opt/homebrew/bin`** → `op` not found →
  `ssh-claw`/`nutricore-release` die with a misleading "failed to read the private key from
  1Password". Prefix `export PATH="/opt/homebrew/bin:$PATH"`. Documented in openclaw-setup
  `info/08-Docker-Deploy.md` (Release flow).
- **isort has no committed profile** but the repo is formatted **black-style** (`isort --profile black`);
  plain `isort` reformats into a black-incompatible style, and `main` is "dirty" under the default
  profile too. Use `isort --profile black` + `black` on **touched files only**. [[black-scope-touched-files]]
- **TD-007 side effect:** `OpenAIService()` now reads the DB on construction. Kept hermetic in tests via
  a conftest autouse that nulls `openai_service.get_persisted_model`; self-heal tests drive the
  `model_selection` module directly (patch `ms.SessionLocal`), so they're unaffected.
- The gate `./scripts/test.sh` is lock-drift-guarded (`poetry check --lock`) but does NOT run
  flake8/black — formatting is manual/scoped.
