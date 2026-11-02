# Tech Debt — активное

<!-- split by scripts/debt-split/split.py — item bodies are byte-identical to the original -->

Critical / High / в работе. **Единственный файл, который читают целиком.**
Остальное: `_tech-debt-backlog.md` (Medium/Low) · `_tech-debt-archive.md` (закрытое) — только грепом.

## Заметки из исходного файла
- [ ] **TD-243**: A dish named by a whole typed sentence, left by the old M7c, stays in «Блюда» (the delete is the owner's). The empty-balance refusal it came with is fixed on this branch: `ai_errors` matches the error type, not only `code`
  - **Priority:** Low
  - **Source:** owner's report
  - **Created:** 2026-11-02

Track deferred improvements. Review monthly.


_None open._


_None open._


_None open._


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
