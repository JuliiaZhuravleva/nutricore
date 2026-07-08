# Handoff — main (2026-07-08)

## State
On **`main`**, clean tree, pushed. **255 tests green** (`./scripts/test.sh` — the cache-venv
wrapper; `poetry run`/bare `python -m pytest` are broken, TD-001). Single branch, no worktrees.
**Release protocol now locked** (`docs/RELEASE.md`): release work is handed off as a **pushed
feature branch** — not merged to main from this side; openclaw-setup's `nutricore-release` owns
merge→main + deploy on the mini. Docs/no-runtime-change still merge to main directly.

## What shipped this session
- **photo-product-lookup round-1 — merged** (`41abeaa`): packaged-food КБЖУ via a **vision-read
  barcode → Open Food Facts** lookup on a **pluggable resolution pipeline** (`ADR-0001`), with the
  vision estimate as fallback. New `product_caches` table + `meals.resolution_source/_signals`
  (migration `f5a6b7c8d9e0`), `open_food_facts_service.py`, `product_lookup_service.py`,
  `openai_service.extract_barcode_from_image`, and a `get_openai_service()` factory. Auto-triggers on a
  detected barcode; reply shows a transparent source/confidence badge + the gram basis (correctable at
  confirm). Built by the **plan-fixes multi-agent flow** (items A1–A7 + A11), then hardened via
  **/review-deep** (C1/H1/H2 + mediums — all fixed) and **/review-security** (OWASP → ACCEPTABLE RISK,
  defense-in-depth folded in).
- **Product philosophy** captured in [`docs/product-philosophy.md`](docs/product-philosophy.md)
  (best-guess + seamless, but transparent + correctable; tie-breaker = long-term insight; medical logic
  is the my-health boundary line).
- **Orchestration infra:** `scripts/test.sh` (TD-001 test wrapper), `.claude/conventions/sessions.md`
  (raises the plan per-item budget + tells specialists the working test command), `.gitignore` for
  orchestrator scratch (`*.raw/`, sidecars, `docs/.draft/`).

## Next up
1. **Deploy round-1 + A8 on the mini** — openclaw's side. Round-1 is in main (no new required-env):
   signal them to run `claw-deploy release nutricore` (migrate-before-start applies `f5a6b7c8d9e0`).
   **A8** is on branch `feat/product-lookup-a8-name-off` (pushed) → `nutricore-release feat/product-lookup-a8-name-off`
   (no migration, no env). Prereq: the release verb installed once on the mini (commands in `docs/RELEASE.md`).
2. **Round 2 of product-lookup** — **A8 (OFF name search) DONE** (branch pushed, 273 green). Remaining:
   A10 (label OCR), A9 (web_search — needs a Responses-API-migration ADR first). See `_tech-debt.md` **TD-011**.
   Runtime work → **feature branch** per the release protocol.
3. **Doc revamp — DONE** (this session): README honest personal-tool framing + real feature list +
   Python 3.12; `docs/README.md` navigation index; stages/README is the canonical status source
   (Stage 0 ✅); consult-relay re-statused SHIPPED + trimmed to contract; `Business_description.md`
   → `docs/archive/origin-brief.md`; RELEASE.md host redacted; `docs/_doc-revamp.md` removed.
4. Open debt: **TD-001** (poetry venv, Med); **TD-007/008** (self-heal coverage + `telegram.py`
   decomposition); **TD-010** (TD-009 follow-ups); **TD-011** (product-lookup accuracy residuals);
   **TD-012** (pre-existing flake8/dead-helper in the new specialist test files).

## Gotchas / learnings
- **Tests:** `./scripts/test.sh` (wraps the cache-venv python) — NOT `poetry run` / bare `python -m pytest` (TD-001).
- **Plan-fixes orchestration:** per-item `claude -p` budget comes from `.claude/conventions/sessions.md`
  `## Orchestrator config` (NOT the envelope's `budget` block). Specialists read that same file for
  conventions — if they can't run tests, they burn budget on denied commands and stall.
- `--revise` mode has a wrapper quirk: it resolves plans_dir under the **main** repo, not the worktree,
  and exits 70 even though the in-place revision succeeded — verify the envelope before assuming failure.
- Specialists sometimes **don't commit** doc/test deliverables (evaluator PASSes them off disk) — check
  `git status` in the worktree after a run and commit strays.
- `get_openai_service()` in `openai_service.py` is the shared singleton — use it, don't import
  `telegram_service` from a service module (that was the circular import H2 fixed).
