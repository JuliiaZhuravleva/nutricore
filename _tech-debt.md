# Tech Debt — активное

<!-- split by scripts/debt-split/split.py — item bodies are byte-identical to the original -->

Critical / High / в работе. **Единственный файл, который читают целиком.**
Остальное: `_tech-debt-backlog.md` (Medium/Low) · `_tech-debt-archive.md` (закрытое) — только грепом.

## Заметки из исходного файла
- [ ] **TD-243**: The read failures of 2026-11-02 were the OpenAI balance: 429 «You have no credits remaining» in `ai_call_logs` on every call in the window. Open: (1) whether `ai_errors.out_of_credit` knew this refusal, since it matches `code == insufficient_quota` only; any other code counts as a passing rate limit, so no 💳 alert and automatic retries; the real code and the `meal_readings` status are needed to tell. (2) A dish named by a whole typed sentence, left by the old M7c, stays in «Блюда»
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
