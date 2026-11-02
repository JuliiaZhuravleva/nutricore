# Tech Debt — активное

<!-- split by scripts/debt-split/split.py — item bodies are byte-identical to the original -->

Critical / High / в работе. **Единственный файл, который читают целиком.**
Остальное: `_tech-debt-backlog.md` (Medium/Low) · `_tech-debt-archive.md` (закрытое) — только грепом.

## Заметки из исходного файла

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
