# Tech Debt

Track deferred improvements. Review monthly.

## Critical
_Blocks feature work or security risk._

## High
_Causes recurring problems._

## Medium
_Slows development but doesn't block._

- [ ] **TD-001**: Poetry venv recreation loop — `poetry run` keeps recreating an empty
  in-project/cache venv without the project deps (its base interpreter appears dangling),
  so `poetry run pytest`/`black` intermittently fail with `ModuleNotFoundError`. Workaround:
  `poetry install` then invoke the cache-venv python directly
  (`~/Library/Caches/pypoetry/virtualenvs/nutricore-SKSdxrGe-py3.12/bin/python -m pytest`).
  Proper fix: recreate the venv against a stable Python (`poetry env remove --all && poetry install`),
  and pin the interpreter so nvm/pyenv changes don't dangle it.
  - **Priority:** Medium
  - **Source:** /wrap session 2026-07-02 (env broke mid-wrap; code was green)
  - **Created:** 2026-07-02

## Low
_Track for later._

## Resolved
_Keep 90 days then remove._

---

### When to Add
- Skipped tests to meet deadline
- Used workaround instead of proper fix
- Copy-paste instead of abstract
- Disabled linter rules
- Known performance issue deferred
- Bug acknowledged but deprioritized

### Entry Format
```
- [ ] **TD-NNN**: Brief description
  - **Priority:** Critical | High | Medium | Low
  - **Source:** what identified this (review, bug report, etc.)
  - **Created:** YYYY-MM-DD
```
