# Handoff — main (2026-07-08)

## State
On **`main`** (`7fb51e1`), clean tree, **5 docs-only commits ahead of `origin/main` (NOT pushed)**:
input-processing diagram, `.gitignore` (macOS), TD-013/014, TD-015, research doc + TD-016.
**276 tests green** on main (`./scripts/test.sh` — cache-venv wrapper; `poetry run` broken, TD-001).
A sibling worktree holds the round-2 branch (below).

**Release protocol** (`docs/RELEASE.md`): runtime work ships as a **pushed feature branch** handed to
openclaw-setup's `nutricore-release`; docs/no-runtime-change merge to main directly (not pushed here yet).

## What shipped this session
- **photo-product-lookup round-2 — DONE + reviewed, on a pushed branch** (`plan/photo-product-lookup-round2`,
  worktree `../nutricore.photo-product-lookup-round2-wt`, tip `2b10a73`, pushed). Built via the multi-agent
  **plan-fixes → execute-plan** flow: **A12** ADR-0002 (Responses-API split-client), **A10** `LabelOCRStrategy`
  (on-pack label OCR, `label_ocr`), **A9** `NameWebSearchStrategy` (Responses-API `web_search`, `name_web`,
  dynamic medium/low), **A13** pipeline-order + autouse-mock gate. Pipeline order now
  `barcode_off → name_off → label_ocr → name_web → vision`; distinct badges 🏷/🌐. Then a **4-agent
  /review-deep** → 1 CRITICAL (LabelOCR bare `float()` on model output → hard-fail; 3× corroborated) + 2
  findings, all fixed (+3 regression tests). **320 green.** Code-only: **no migration, no new env.**
- **Input-processing diagram** (`docs/diagrams/input-processing-flow.{html,md}`) — 4-phase × 3-tier maturity
  matrix (Сейчас → Следующее → North-star); HTML for humans, MD mirror for LLMs. Ratified by Julia.
- **Debt tracked:** TD-013 (confidence gate), TD-014 (personal-DB/RAG), TD-015 (confirm Да/Нет buttons —
  Julia hit live), TD-016 (Responses-API `web_search_preview` → GA).

## Next up
1. **Push `main`** (5 docs commits) when ready — docs go to main directly per the protocol (not pushed yet).
2. **Deploy round-2** — openclaw's side. Relay the ready handoff (see below / this session's tail):
   `bin/nutricore-release plan/photo-product-lookup-round2` — no migration, no env, no manual step, 320 green.
   (A8 already released → `4e8f458`. Round-1 migration `f5a6b7c8d9e0` is on main.)
3. **Round-2 residual / future:** TD-016 (GA web_search), then the bigger «Следующее» tracks — TD-013
   (confidence gate + quick buttons) and TD-014 (personal-DB/RAG). TD-015 is the immediate confirm papercut.

## Gotchas / learnings
- **Tests:** `./scripts/test.sh` (cache-venv python) — NOT `poetry run` (TD-001).
- **execute-plan specialists sometimes don't commit** doc/test deliverables (evaluator PASSes them off-disk).
  After a run: `git status` in the worktree and commit strays (this session: A12 ADR + A13 mocks were uncommitted).
- **SessionStart HANDOFF-hook cruft** (`HANDOFF.md` deleted + a timestamped copy) trips the plan-fixes/execute
  **scope gate** → false-positive `GATE FAIL … exit 71`. Clean the cruft (`git checkout HANDOFF.md; rm HANDOFF-*.md`)
  and the run is fine — the envelope/deliverables are real.
- **plan-fixes wrapper** needs source path + flags as **separate** args; quoting them into one string → exit 66.
- **envelope approve** only blocks on pending `human_feedback`, not `clarifying_questions`.
- `get_openai_service()` is the shared singleton — services use it, never import the handler layer (H2 lesson).
