# Release protocol — nutricore ⇄ openclaw-setup

Nutricore is **built** here and **shipped** by the openclaw-setup tooling on the Mac mini.
Two agents, one seam. This file is the source of truth for who owns what.

## The seam

The boundary is a **pushed, reviewed feature branch + a release note**. Everything up to
that point is the nutricore side; everything from that branch onward is the openclaw side.

```
 nutricore Claude  │  openclaw Claude
 (this repo)       │  (my-projects/openclaw-setup)
───────────────────┼──────────────────────────────────────────────
 implement on      │  bin/nutricore-release <branch>:
 feature branch    │   2. GATE tests (isolated worktree + poetry venv)
 verify + review   │   3. GATE alembic single head; warn on required-env drift
 push the branch  ─┼─▶ 4. merge <branch> → main + push
 "готово, ветка X" │   5. claw-deploy release → build → migrate-before-start → up
 + release note    │   6. verify (services Up, clean start, bot polling)
```

The release pipeline **owns the merge to main** (step 4, resolves `origin/<branch>`).
So release work is **not** merged to main from this side — it is handed off as a branch.

## nutricore side (me) — done when the branch is pushed

1. Implement on a **feature branch off `main`**.
2. Verify locally: `./scripts/test.sh` green (cache-venv wrapper, TD-001 — never `poetry run`).
3. Review as warranted: `/review-deep`, `/review-security`.
4. Migrations: **single linear head**, reversible. `.env.example` updated for any new var.
5. **Push the branch. Do NOT merge to main.**
6. Hand off: **"готово, ветка X"** + a release note (template below).

**Deploy lane vs direct merge** — the lane is triggered by *"does this run differently on
the mini?"*:
- Runtime / schema / deps / config change → feature branch → `nutricore-release`.
- Docs-only / pure refactor with no runtime effect (e.g. `docs/_doc-revamp.md`) → deploy
  not needed, merge to `main` directly.

### ⚠ New required env is the one hard coordination point

The migration/env gate (step 3) only **warns** when a new *required* Settings field (typed,
no default in `app/core/config.py`) is missing from the mini env map — it does not block. A
missing key can **crash-loop** the app, and adding it to the mini map needs `sudo` on the
openclaw side, done **before** the release. So: if the branch adds a required config field,
**call it out loudly in the release note** (name + example value). Fields with a default are
safe and need no action.

### Release-note template

```
готово, ветка <X>
- Что меняется: <one line>
- Миграция: <нет | да — rev, что делает>
- Новый required-env: <нет | да — KEY (пример значения) → добавить в mini env map ДО релиза>
- Ручной шаг: <нет | да — …>
```

## openclaw side (their agent) — from the branch onward

`bin/nutricore-release <branch>` (on the MacBook; reaches the mini via `ssh-claw`):
tests gate → migrations/env gate → merge→main+push → `claw-deploy release nutricore`
(build → migrate-before-start via `release.d/nutricore.conf` → up) → verify. Any gate
failure aborts **before** the merge — nothing ships. `--dry-run` stops after the gates.

The mini is entirely theirs: **I never SSH the mini, never run `alembic upgrade head` on
prod, never merge release branches to main.**

## Already-in-main special case

`nutricore-release` always merges branch→main, so it does **not** fit work already on `main`
(round-1 photo-product-lookup, `41abeaa`). For that: signal openclaw to run
**`claw-deploy release nutricore`** directly — migrate-before-start still applies the pending
migration. Round-1 added no new required env, so this is safe as-is.

## One-time setup (Julia, `sudo jay`)

The release verb must be installed on the mini once (from openclaw-setup):

```bash
cd /Users/julia/my-projects/openclaw-setup
scp bin/claw-deploy examples/claw-deploy/nutricore.release.conf jay@100.113.229.27:/tmp/
ssh -t jay@100.113.229.27 'sudo install -m0755 -o root -g wheel /tmp/claw-deploy /usr/local/bin/claw-deploy && sudo install -d -m0755 -o root -g wheel /usr/local/etc/claw-deploy/release.d && sudo install -m0644 -o root -g wheel /tmp/nutricore.release.conf /usr/local/etc/claw-deploy/release.d/nutricore.conf'
```
