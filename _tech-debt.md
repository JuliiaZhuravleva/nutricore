# Tech Debt — активное

<!-- split by scripts/debt-split/split.py — item bodies are byte-identical to the original -->

Critical / High / в работе. **Единственный файл, который читают целиком.**
Остальное: `_tech-debt-backlog.md` (Medium/Low) · `_tech-debt-archive.md` (закрытое) — только грепом.

## Заметки из исходного файла
- [ ] **TD-243**: One real photo failed the read every time (first send and several 🔁, 2026-11-02). Cause not proven: prod logs were out of reach from that session. To tell apart: a model refusal or empty answer, or a parse/write error in the read. Look at `Meal N not read` / `not written` in the bot's log and `ai_call_logs` for that window. Also left by the old M7c: a dish named by a whole typed sentence stays in «Блюда»
  - **Priority:** High
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
