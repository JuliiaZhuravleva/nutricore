# Wrap extension — nutricore

Project-specific steps for `/wrap` **Phase 4b**. Run these after the commit phase.

## Release handoff to `openclaw-setup`

nutricore is **built here** and **shipped by the sibling `openclaw-setup` repo** — its
`bin/nutricore-release` / `claw-deploy` tooling merges + deploys to the Mac mini. Full seam:
[`docs/RELEASE.md`](../docs/RELEASE.md). The rule: **release work is not merged to `main`
from this side** — it goes out as a *pushed feature branch* that the openclaw-setup agent
gates, merges, and deploys.

So the last step of a release-bearing wrap is to hand that agent a **ready-to-relay English
message**. During Phase 4b:

### Step 1 — decide if there is deploy-bound work

Deploy-bound = a **runtime / schema / deps / config** change that will run differently on the
mini. Determine which case applies:

```bash
git branch --show-current
git log --oneline origin/main..HEAD | head        # unpushed commits?
git diff --stat main...HEAD 2>/dev/null | tail -1  # feature-branch delta vs main
```

- **On a pushed feature branch with `app/`, `alembic/`, `celery_app/`, or config changes**
  → release via `nutricore-release <branch>`.
- **Runtime work merged to `main` this session with a pending migration or behavior change**
  → release via `claw-deploy release nutricore` (the "already-in-main" path).
- **Docs-only / pure refactor with no runtime effect** → **no handoff**. Print one line:
  `No deploy-bound work this session — nothing to hand to openclaw-setup.` and stop here.

### Step 2 — gather the gate facts

The openclaw-setup gate (`nutricore-release`) runs the tests, checks a single linear alembic
head, and **warns** (does not block) on a new required env var missing from the mini map. So
surface these explicitly:

```bash
# Migration in this branch?
git diff --name-only main...HEAD | grep '^alembic/versions/' || echo "(no migration)"

# New *required* Settings field (typed, no default) — needs a sudo add to the mini env map
# BEFORE the release, or the app can crash-loop. This mirrors nutricore-release's own check.
git diff main...HEAD -- app/core/config.py \
  | grep -E '^\+[[:space:]]+[A-Z_]+:[[:space:]]*(str|int|bool|float)[[:space:]]*$' \
  || echo "(no new required env)"
```

A new required env var is the **one hard coordination point** — flag it loudly in the message.

### Step 3 — emit the handoff message

Print a single fenced block the user can paste to the **openclaw-setup claude-code**. Keep it
**in English** (that agent's project language) and factual. Fill each field from Steps 1–2:

````
Ready to release — nutricore.

- Target: branch `<branch>`  (or: "already on main")
- What changed: <one line, user-facing effect>
- Migration: <none | yes — rev `<id>`, what it does>
- New required env: <none | yes — `KEY` (example value) → add to the mini env map (sudo) BEFORE release>
- Manual step: <none | ...>
- Verified here: <N tests green via ./scripts/test.sh; reviews run, if any>

Please run: `bin/nutricore-release <branch>`
  (or `claw-deploy release nutricore` if this is already on main).
The gates (tests, single alembic head, env-drift) + merge→main + migrate-before-start +
verify are yours per docs/RELEASE.md. Ping me if a gate fails.
````

If several release-bearing branches are outstanding, emit one block per branch.
