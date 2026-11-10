# Stage 6: 🔔 Напоминания о еде (revision 2)

Status: spec accepted 2026-11-06, amended 2026-11-07 (D6–D9); R0 + R1 (evening) + R1c building on `feat/meal-reminders`. Written by
three design agents, merged, then critiqued by four persona agents (busy owner, hard days, rare
logger, shift worker). The **owner's decisions below override the text further down** wherever
they disagree.

## Owner's decisions (2026-11-06) — these win

- **D1. On for everyone by default** (answer to Q1: «Всем сразу»). An absent config reads as
  `{"mode": "evening", "evening": "21:00"}` for every person, owner or invited. **There is no
  one-time offer**: §3.0, the `offered` key, the `rm:y` / `rm:no` verbs, `_reminder_offer`, the
  offer tests and acceptance C1 are dropped. R3 (no record ever → nothing; last record older than
  7 local days → nothing), R9 (quiet after unanswered) and ✖ on every message still hold.
  Because the default zone may be wrong for an invited person: the **first** reminder a person
  ever gets names the time and zone in its footnote (as §3.1), and when `settings["timezone"]` is
  absent its keyboard gets one more row `[🕐 Часовой пояс]` (`settings_screen.ZONES_CALLBACK`).
  Acceptance C3 becomes: no zone chosen → the first reminder names «Тбилиси (UTC+4)» and has the
  zone button; after choosing Novosibirsk the next one comes at 21:00 Novosibirsk.
- **D2. No list of today's times in any reminder, for anyone** (Q5 answered «нет»). As §3.1.
- **D3. Sound: silent by default, a switch in the 🔔 sub-screen** (Q2: «Выбор в настройках»).
  `reminders.sound: true` sends with sound; absent/false → `disable_notification=True`. Button
  `[🔕 Без звука]` / `[🔔 Со звуком]` is NOT allowed (🔕 has no tile, R14) — use one toggle row
  `[🔔 Звук: выкл]` / `[🔔 Звук: вкл]` (`rm:snd`), notice `✓ Со звуком` / `✓ Без звука`. The
  sub-screen state text says «без звука» / «со звуком» accordingly.
- **D4. Order: evening first** (Q-order: «Сначала вечер»). This branch = S0 (chat simulator,
  below) + R0 + R1. R2 and R3 come on a later branch after the owner has looked at R1.
- **D5. R4 is not planned.** The owner: no paid OpenAI calls at all for now. Nothing in this
  stage may make or price a paid call; tests and the simulator use fakes only.
- Q3 (answer without a time → that day, no clock), Q4 (🗓 Итог недели moves into 🔔; hub keeps
  5 rows) and Q6 (meal names → fixed ≈ clock) stay at the defaults the spec took.

## Owner's decisions (2026-11-07, from docs/researches/reminders-retention-evidence.md) — these win too

Slice **R1c** on this branch. Where these disagree with R3, R9, §3.1, §5.6, §10 or §9, these win.

- **D6. Re-entry after a lapse: one message at 7, one at 14, then silence** («Как в исследовании:
  7 и 14»). R3 keeps «no regular reminder when the last meal row is older than 7 local days» and
  «no meal row ever → nothing». On top of it, for a person whose evening reminders are on (not ✖,
  mode evening):
  - Let L = the local day of the last meal row (any row, drafts and deleted included), and
    gap = today − L in local days, at the evening slot time.
    - **Re-entry #1** is due when 7 ≤ gap < 14 and no `reentry7` send exists after L's last row.
    - **Re-entry #2** is due when 14 ≤ gap < 30 and no `reentry14` send exists after L's last row.
    - gap ≥ 30 → nothing ever (the report's «stop at 30»; this also keeps people who left long
      before the release from getting anything at launch).
    - Any new meal row starts a new count: the next lapse may get #1 and #2 again.
  - The slot names `reentry7` / `reentry14` live in `reminder_sends.slot`, so
    UNIQUE(user_id, local_day, slot) still claims once. No new settings keys and no migration
    change.
  - It goes **even when the ladder has paused** (D7): this is the one designed message for someone
    who stopped. It does not go after ✖, nor for anyone R2/R7 excludes. The same due checks apply
    as §5.3 except rules 4 (R3), 8 (ladder) and R8 (a fresh record means gap = 0 anyway). On a
    Sunday with the week card on (R17), it moves to the next evening.
  - **Copy**, constant title, no footnote, keyboard `[✅ ОК] [✖ Выключить]` only (no ⭐ chips):
    ```
    🔔 <b>Еда за сегодня</b>
    Можно начать с сегодняшнего. Прошлые дни можно оставить как есть.
    ```
    ```
    🔔 <b>Еда за сегодня</b>
    Дальше без напоминаний. Если дневник пригодится — можно просто отправить еду в любой момент.
    ```
    The owner chose these words; R10's «never names an absence» is waived for exactly these two
    strings. They hold no count of days. #1 first read «Прошлые дни восстанавливать не нужно.»;
    the owner asked on 2026-11-08 for «прошлые дни просто пропускаем» «или что-то такое»; the
    built «…можно оставить как есть.» keeps that sense without R10's banned «пропуск» or «нужно».
    #2 first read «Могу больше не напоминать. Если захочется — …»; reworded 2026-11-08 into a
    statement so that ✅ ОК is an acknowledgement.
  - A re-entry send is **neutral** for the ladder (like `ok`). It never opens a recall window
    (§3.2): an answer after it is an ordinary capture at the moment it arrives. R4 still holds: it
    deletes the previous untouched reminder, and the next send deletes it.
- **D7. Unanswered ladder: rarer, then a pause** («Реже, раз в 3 дня»). Replaces §5.6's
  single-slot rule:
  - streak ≥ 3 → `quiet = {"level": "sparse", "on": local_day}`: a regular reminder is due only
    when its local day ≥ the local day of the last regular send + 3.
  - streak ≥ 6 → `quiet = {"level": "off", "on": local_day}`: the pause as before, ended only by
    the return rule or a ⚙️ tap.
  - `fragile` (after a resume): streak ≥ 1 → `off`, as before.
  - An answered send breaks the streak, so the level drops back to daily (`quiet = null`) at the
    next ladder write. ✅ ОК stays neutral.
  - The resume rule (a day without records, then 2 record days) applies to `off` only. `sparse`
    needs no resume: it ends on an answer or a ⚙️ tap on the ✓ mode (same as the pause).
  - Multi-slot modes (R2/R3, later branch): ≥ k → `one`, ≥ 2k → `sparse`, ≥ 3k → `off`.
  - The 🔔 sub-screen, while sparse: `Сейчас приходят реже, потому что остаются без ответа. Как
    раньше — кнопкой ✓ 🕘 Вечером.` Nothing is said in the chat.
- **D8. «Можно примерно и не всё»** («Добавить «можно примерно»»). The reminder text:
  - first: `Можно не отвечать. Если что-то вспоминается, можно рассказать голосовым или написать — примерно и не всё. Время можно назвать: «в 13 суп». Без времени запишется на этот день, без часов.`
  - later, with ⭐: `Можно не отвечать. Если что-то вспоминается — голосовым, текстом или блюдом из ⭐, можно примерно и не всё:`
  - later, no ⭐: `Можно не отвечать. Если что-то вспоминается — голосовым или текстом, можно примерно и не всё.`
  - §10 `what`: `…можно рассказать голосовым, написать или нажать на блюдо из ⭐, примерно и не всё. Отвечать не обязательно.`
- **§10 `how`, ladder and re-entry sentence** (no counts of days, R10): replace «Если три
  напоминания подряд остаются без ответа, они перестают приходить и возвращаются после перерыва в
  записях или кнопкой в настройках.» with «Если напоминания подряд остаются без ответа, они
  приходят реже, а потом перестают и возвращаются после перерыва в записях или кнопкой в
  настройках. Если записей долго нет, придут одно-два коротких сообщения о том, что можно начать с
  любого дня, и дальше бот молчит, пока не придёт еда.»
- **D9. Metrics later** («Позже, отдельной задачей»): TD-247.
- **Acceptance changes (§9):**
  - B1: messages Tue, Wed, Thu; sparse after Thu; Sunday is the week card; Monday (gap 7) is
    re-entry #1; the Monday after (gap 14) is re-entry #2; nothing after.
  - B3: sparse after the 3rd evening (one every 3rd evening), pause after the 6th. Breakfasts never
    resume it; ⚙️ does, the same evening.
  - B4: ≤ 3 in the first calendar week (≤ 4 in the first 7 days), then about one every 3 days
    while unanswered, then the pause.
  - C2: last record 20 days ago and no reminder ever sent → nothing at launch (no «Могу больше не
    напоминать…» as a first message; acceptance 2026-11-07). Last record 40 days ago → nothing.
    Either way, a new record makes rule 4 pass again.
  - New L1: gap 7 with the ladder at `off` → re-entry #1 goes. After ✖ → nothing.
  - New L2: a text «суп» 10 min after re-entry #1 → an exact-time record at its arrival, no
    day_only; the re-entry send stays neutral.

## Owner's decisions (2026-11-08, from docs/researches/reminder-copy-caring-evidence.md)

- Re-entry #1: «Можно начать с сегодняшнего. Прошлые дни можно оставить как есть.»
- Re-entry #2 is a statement, so ✅ ОК reads «понятно», not «yes, stop»: «Дальше без
  напоминаний. Если дневник пригодится — можно просто отправить еду в любой момент.»
- The week card names no pause (stage-5 «✨ Снова с записями — после паузы» removed); the
  finding is only a food new to the records.
- Grams-or-time corrections keep not renewing the recall window (TD-252 closed).
- **Next branch, after this one is released** (all four chosen; not built here):
  1. rotate the evening body among 4–6 variants of the same meaning (research library 1–6);
  2. a rare purpose line, about one evening in six, never two special evenings in a row;
  3. the observation «this dish was in the records several times → add to ⭐?» (≥ 3 times on
     ≥ 2 different days, not already in ⭐);
  4. descriptive observations of time of day and variety: at most one a week, data thresholds
     from the research table, worded about «the records there are», a switch in 🔔 settings to
     turn them off. No causes, no moral words about food, no weight, no inferred «hard day».

## Owner's decisions (2026-11-11): the morning question «how much of yesterday is recorded»

Not built. Idea: each morning ask how complete yesterday's records are, only when yesterday had
at least one record, so averages are not drawn from days with only part of the food in them.
Mock screens were read by 4 isolated fresh-eyes viewers (round 1: «Почти всё / Больше половины
/ Малую часть»).

- **D10. Sent at 10:00 local**, silent, a separate message (not attached to the day's first
  interaction). Independent of the evening reminder.
- **D11. An answer meaning "very little recorded" takes the day out of the 7-day averages
  only.** Observations and the week summary still use the day and mark it. Records always stay.
- **D12. The evening «✖ Выкл» does not stop the morning question**, and the 🔔 screen says so.
  The morning question has its own switch there and its own «✖ Не спрашивать».
- **D13. "Yesterday" ends at local midnight** by default; a per-person boundary for night
  shifts is TD-261. The 🔔 screen as a whole (5 action rows with the new switch) is TD-262.
- **D14. Wording (owner's yes, 2026-11-11, after fresh-eyes round 2).** Round 2 compared the
  owner's «Удалось вчера записать всю еду?» (👍/✍️/✋ Нет), «Вчерашняя еда вся в записях?»
  (👍/✍️/✋ Почти нет) and the same with fill bars ▰▰▱. The records wording with hands won
  (10 of 12 points; «Удалось» read as a test to 3 of 4; bars read as a score of the day to 4 of 4
  and were too small for 2). Copy, exact:
  - **Question** (10:00 local, silent, only when yesterday has ≥ 1 record):
    «☀️ <b>Записи за вчера, вт 10 ноября</b>
    Вчерашняя еда вся в записях?
    Можно не отвечать — тогда день считается как обычно. Да или частично — тоже.
    Почти нет — вторник не войдёт в средние за 7 дней, а в итогах будет с пометкой.»
    Buttons: `[👍 Да] [✍️ Частично] [✋ Почти нет]` / `[✖ Не спрашивать утром]`.
  - **Effect:** Да, Частично or no answer → the day counts as usual. Почти нет → out of the 7-day
    averages only; observations and the week summary keep it with a mark. Records never change.
  - **After an answer** (the same message, edited): «✓ Ответ сохранён: почти нет. Вторник не
    войдёт в средние за 7 дней, а в итогах будет с пометкой. Записи за него остаются в дневнике.»
    (for Да/Частично: «✓ Ответ сохранён: частично. Вторник считается как обычно.»)
    Buttons: `[✅ ОК]` / `[✏️ Изменить ответ]`. ✏️ brings the three answers back. The callback is
    answered empty: a «Принято» toast read as a second, redundant confirmation to 3 of 4.
  - **After «✖ Не спрашивать утром»:** «✓ Утром больше не спрашиваю, сколько записано за вчера.
    Вечерние напоминания остаются. Включить потом — ⚙️ Настройки → 🔔 Напоминания → ☀️ Вопрос
    утром.» Buttons: `[✅ ОК]` / `[☀️ Вернуть вопрос]` (hanging-undo rule: ✅ ОК first). The
    return → «✓ Вопрос утром включён: завтра в 10:00, если за сегодня будут записи.»
  - **🔢 week numbers:** «🔢 В среднем за день (пт, пн): …», bars and numbers only for the
    averaged days, then «Вторник отдельно: в записях только часть дня.» («Вторник не
    считается: там записана малая часть» read as a punishment to 2 of 4).
  - **🔔 settings:** a line «☀️ Утром, в 10:00 — вопрос про вчерашние записи: присылаю / не
    присылаю.», the sentence ««✖ Выкл» выключает только его.» after the evening paragraph, the
    button `☀️ Вопрос утром: вкл/выкл`. Its state-or-action ambiguity (4 of 4) is TD-262.
  - **Not drawn yet:** the mark on a «почти нет» day in the Sunday summary and in observations.
- Round-1 findings kept above: the effect line in plain text, never an italic footnote (4 of 4
  skipped it); the excluded day without its bar (a short bar read as "ate little" to 4 of 4, to
  0 of 4 in round 2); «утром больше не спрашиваю…», not «про вчерашний день» (read as "only that
  day" 4 of 4, 0 of 4 in round 2); the 🔔 text no longer promises «не больше одного сообщения в
  день».

## Built differently from the text below (R1a/R1b, recorded 2026-11-07)

- The 🗓 week toggle lives in the 🔔 sub-screen (Q4), the hub keeps 5 rows.
- The first reminder can have 4 rows (the extra `[🕐 Часовой пояс]` row of D1).
- «снова» is removed from all reminder copy (R10), including the sub-screen paused text: it reads
  «Сейчас не приходят. Включить — кнопкой ✓ 🕘 Вечером.»
- `reminder_sends` is purged by its own beat task at 04:15 (`purge-reminder-sends`).
- R16 is wider than the text: «нет, ничего» and a trailing «, спасибо» also close a reminder.
  A «ничего» over a reminder that already holds a ⭐ log is answer `fav_none` (answered).
- A voice R16 edits the voice's own line to «✓ Принято.» with no buttons.
- Re-entry slot names are `re7` / `re14` (kind `reentry`), not `reentry7` / `reentry14`:
  `reminder_sends.slot` is String(5) and Postgres enforces it.
- Re-entry on gap 7 stops that day's regular reminder; R3 stays «older than 7 days». A #1 held
  back by a Sunday week card on gap 13 goes on Monday (gap 14), and #2 the evening after.
- Sparse counts its +3 from the last non-re-entry send of any outcome, ✅ ОК included.
- Inside the window, a reply to a meal line that changes only grams or time does **not** renew the
  window. The owner confirmed on 2026-11-08 that it should stay so (TD-252 closed).

Persona acceptance (2026-11-07), the lead's calls:
- Re-entry goes only to a person who has received a reminder send before. At launch a long-quiet
  person gets nothing until they log.
- The return after a re-entry: no regular reminder on the local day of the first record after it.
  From the next evening the usual rules apply.
- The 🔔 sub-screen keeps the time and zone line when sparse or stopped. During a stop it reads
  «Сейчас не приходят: придут на следующий день после любой записи.»
- R4: a reminder message that ✖, a zone pick or a late ⭐ list turned into another screen is left
  alone at the next send. One too old to delete is edited to a dated title only, «🔔 Еда за пн
  9 ноября», with no buttons. B2's «deleted» therefore means «deleted or titled».
- The first reminder's 🕐 Часовой пояс closes the send as neutral and comes back to the reminder.
- D2 «в обед» etc. count as a timed clause only in the several-times rule. On their own they give
  no clock (Q6 stays unbuilt; R0 shrink e6ae630).
- R16 is wider again («не ела ничего», «да ничего», «ничего больше», «ничо»). «ничего особенного»
  and «ничего кроме …» are still read as food.

## S0. Chat simulator for persona acceptance (test-only, no runtime change)

A CLI that lets an agent use the real bot as a person would, with no Telegram, no network and no
model: `scripts/sim.sh <state-dir> <command> [args]`, backed by `tests/sim/` (reuse `Chats` /
`Wire` from `tests/test_two_people.py` / `tests/test_capture_router.py`, a file SQLite DB in the
state dir so state survives between invocations, the real `telegram.py` application).

- Commands: `new <name> [--owner] [--zone Europe/Berlin]` (a person), `as <name>`,
  `say "<text>"`, `voice "<transcript>"` (the transcription is faked to this text),
  `photo "<what the fake vision returns>"`, `tap "<button label>"` (on the latest message that
  has it; `--msg N` for another), `reply N "<text>"`, `clock 2026-11-06T21:00` (local to the
  person) / `advance 40m`, `tick` (runs every periodic push that is due at the clock: week card,
  reminders), `chat [--last N]` (the person's chat as text: each bot message with its buttons,
  edits applied, deleted messages marked), `day` (the person's records for the day: name, time,
  precision, time_source).
- Food reading is faked deterministically: a text is split on commas/«и» into dishes with fixed
  plausible numbers; never calls OpenAI (assert: the OpenAI client is patched to raise).
- Output is plain text an agent can read. Each command prints only what changed.
- Tests: a smoke test drives one meal + one ⭐ tap through the CLI entry point.

## 1. Why

The owner: «я и другие люди просто забываем вносить еду в течение дня». The record (job 1 of G5-R24) gets holes because people forget. Several pieces already exist:
- a native push channel: the Sunday week card (`app/services/week_push.py`);
- one-tap logging with ⭐ (`app/services/favorites.py:1-5`, "calls no model");
- one-voice-note logging (G5-R31/R32).

Three findings set the frame:
- The same reminder felt like structure to one person and like surveillance to another (`docs/researches/hard-days-progress-evidence.md:46`).
- Reminders can feed compulsive logging, and breaks help (`gentle-feedback-evidence.md:145`).
- Prompt burden recurs across dietary JITAIs (`gentle-feedback-evidence.md:121`).

The design follows five lines:
- **Asked, not imposed.** On by default only for the owner. Each invited person is asked once, after their own record, and nothing happens without their «да».
- **One a day by default.** More only by the person's choice, and never more than 3.
- **The arrival says nothing.** A reminder comes on schedule. It never depends on how much was logged today, so its arrival cannot read as a verdict.
- **Quiet by itself, off in one tap.** It goes silent after unanswered reminders, logging something does not wake it at once, and ✖ is on every message.
- **Honest record.** An answer never gets a time it did not have, and never loses a time it did have. This is the philosophy tie-breaker: "never sacrifice the integrity of the record".

What exists today: no reminder code (`grep -ri "remind\|напомин" app celery_app` is empty). Stage 3's reminder plan was revised by stage 5 (`docs/stages/03-retention.md`, "Revised by stage 5").

---

## 2. Rules

- **R1. Zero model calls to send.** Text and buttons come from the DB only. A ⭐ tap copies stored numbers (`favorites._log`, favorites.py:339-361). A free voice or text answer goes through the normal capture read and costs what any capture costs. A «ничего»-answer (R16) costs nothing.
- **R2. Who gets reminders.**
  - The owner (`settings.admin_ids`, config.py:98-100): on by default, evening 21:00.
  - Everyone else: **off** until they say «да» to a one-time offer (§3.0).
  - Nobody else's settings change at launch.
  - **[decision, Q1]**
- **R3. No reminder for someone with no meal record at all**, and **none when the last meal record (any row, drafts and deleted included) is older than 7 local days.** The bot does not chase someone who never started or who stopped.
- **R4. At most 3 a day, and at most one live reminder in the chat.** Before a new one goes out, the previous one is deleted unless a ⭐ was logged from it. If the delete fails, its keyboard is stripped.
- **R5. Silent**: `disable_notification=True`, as the week card does (card_delivery.py:115,127; G5-R15). **[decision, Q2]**
- **R6. Local clock only**: `settings_screen.user_zone` (settings_screen.py:105-106). Slots fall within **05:00–23:30** local time; 00:00–04:59 stays out because of DST. The zone is named wherever the time is named (§3.0, §3.1 footnote, §4.2). The default zone is `DEFAULT_TIMEZONE = "Asia/Tbilisi"` (config.py:207), so the zone must be visible.
- **R7. Only to people who may receive**: `access_control.may_receive` (access_control.py:82-89).
- **R8. Skipped when the record is fresh**: nothing is sent when a meal row arrived in the 3 h before the slot. That equals `CORRECTION_WINDOW` (capture.py:195), so «забыла: суп» never joins an old meal (§6.4). This is the **only** skip that looks at the record. There is no count-based skip.
- **R9. Quiet when unanswered** (§5.6).
  - After 3 unanswered reminders the person goes down a step: several a day → only the last one → none.
  - Only an answer counts: a ⭐ log, a message bound to the reminder, or a «ничего». A meal logged at some other time is not an answer.
  - A pause ends only on a return after a break: a local day with no meal record, then meal records on 2 different local days. It never ends on the day of the first record. A tap in ⚙️ also ends it. Weights and routine ticks do not count.
  - After a pause ends, one unanswered reminder brings the pause back.
  - Nothing is said in the chat about any of this, and the pause shows only inside the 🔔 sub-screen.
- **R10. Text never names an absence or the day's content.**
  - Banned in every reminder, offer and explanation string: «пропуск/пропущено», «забыл*», «не записано», «ещё не», «пусто», «снова», «опять», «если было что-то ещё», and any count of days.
  - Also banned: everything in `tests/test_user_text.py:25-41` (надо, нужно, пора, хорошо, плохо, осталось, gendered past forms, «вы»).
  - A reminder never lists today's times or counts.
- **R11. Honest time for an answer** (§3.2).
  - An answer with no time words is stored **on its day, without a clock** (`day_only`, `time_source="reminder"`). Two exceptions: a ⭐ tap on, or an explicit reply to, a **meal-slot** reminder gets ≈ the slot centre.
  - Time words always win.
  - Two or more different time phrases in one message give `day_only`, not the first phrase found (R0).
  - Photos are never re-timed unless they are an explicit reply.
  - «сейчас», «только что» keep the sent time.
  - The 🕐 screen for a `day_only` meal offers the sent moment and parts of the day in one tap (R0).
- **R12. A result that holds an undo gets ✅ ОК first** (close_row.py:1-9).
- **R13. The owner sees usage only.**
  - The journal stores the verb `rm` (action_event.py:25-37), never a dish.
  - Owner screens never show per-person reminder counts (sent, answered, ignored). At most there is a total across everyone.
- **R14. Every new string is gender-neutral and uses «ты» or the infinitive** (G5-R19).
  - Every new mark is drawn: 🔔 needs a tile.
  - 🔁 (`build_tiles.py:67`), 🕘, 🕐, 😴, ✖, ↩, ⚙, ✏, ⭐ and ✅ are already drawn.
  - 🌙 and 🔕 are not used.
- **R15. Explanation entry** in `explain_screen.TOPICS` (G5-R22, §10).
- **R16. «Ничего» is an answer.** Inside a reminder's window, a text with no dish costs no model call and gets no read. Examples: «ничего», «больше ничего», «почти ничего не ела», «не ел», «не было», «сегодня ничего». Instead, the reminder is answered and closed, and its message is edited to «✓ Принято.» with no buttons. The existing `says_no` («нет, …», meal_words.py:206-215) stays silent as today.
- **R17. Sunday: one unprompted evening message.** On Sunday, while `week_push_on`, no evening-kind reminder is sent. Meal-slot reminders still go.

---

## 3. User flows and exact copy

### 3.0 The one-time offer to an invited person (R1)

- **When**: after a meal read, on the person's **3rd distinct local day with a meal record**. It applies only when the person is not the owner, `reminders.mode` is absent and `reminders.offered` is unset.
- **How**: a separate silent message after the reply, in the same pattern as `_first_record_hint` (telegram.py:995-1013). It is a reply to something the person just did, never a push. `offered = true` is set before sending, so a lost offer beats a repeated one (the `take_hint` rule).

```
🔔 Можно получать раз в день короткое напоминание без звука: рассказать про еду голосовым или нажать на блюдо из ⭐. Приходит в 21:00 по времени Тбилиси (UTC+4). Можно не отвечать, выключить — в любой момент.
[🔔 Да, в 21:00] [Не надо]                 rm:y · rm:no
[🕐 Часовой пояс]                          settings_screen.ZONES_CALLBACK (only when no zone was chosen)
```
- The zone comes from `settings_screen.zone_label` (settings_screen.py:129-134). The «Часовой пояс» row appears only when `settings["timezone"]` is absent (the same test as `welcome_screen.zone_line`, welcome_screen.py:155-170).
- **«🔔 Да, в 21:00»** sets mode `evening`, `since = now`. The message is edited to `✓ Напоминания: вечером, в 21:00 по времени Тбилиси (UTC+4). Настроить — ⚙️ Настройки → 🔔 Напоминания.` with `[⚙️ Настроить]` (`rm:o`).
- **«Не надо»** sets mode `off`. The message is edited to `✓ Не присылаю. Включить можно в ⚙️ Настройки → 🔔 Напоминания.` with no buttons.
- **No answer** means it stays off and is never asked again. The buttons keep working later.

### 3.1 The evening reminder (R1)

The title is constant. There is no list of today's times on any day: the arrival must say nothing about the day (R10). **[decision, Q5]**

**First reminder a person ever gets** (`reminders.noted` unset; set after sending):
```
🔔 <b>Еда за сегодня</b>
Можно не отвечать. Если что-то вспоминается, можно рассказать голосовым или написать. Время можно назвать: «в 13 суп». Без времени запишется на этот день, без часов.
Или нажать на блюдо из ⭐:
<i>Приходит в 21:00 по времени Тбилиси (UTC+4), без звука. Если были записи за три часа до него, не приходит. Выключить — кнопкой ✖. Текст на экране блокировки можно скрыть в настройках уведомлений Telegram.</i>
```

**Every later reminder:**
```
🔔 <b>Еда за сегодня</b>
Можно не отвечать. Если что-то вспоминается — голосовым, текстом или блюдом из ⭐:
```
- With no ⭐: the first text drops «Или нажать на блюдо из ⭐:», and the later text ends «— голосовым или текстом.»
- The lock-screen preview carries only the title and «Можно не отвечать».
- The footnote's time and zone come from the config and `zone_label`.

**Keyboard**: at most 3 rows, 2 per row. Labels are cut with `meal_card._cut` (meal_card.py:238) to `favorites.PAIR_LABEL_MAX = 20` (favorites.py:50).
```
[Овсянка с бананом] [Кофе с молоком]      rm:f:{send_id}:{fav_id}
[Суп чечевичный]    [Хумус с хлебом]
[✅ ОК]             [✖ Выключить]         rm:k:{send_id} · rm:x:{send_id}
```
- **No 😴 in evening mode.** With one a day, «не сегодня» promises nothing that ✅ ОК doesn't. 😴 appears only in multi-slot modes (§3.6).
- **✅ ОК** (`rm:k`) takes the buttons off, with toast «Понятно». It sets `answer = "ok"` and `closed_at`. For the ladder it is **neutral**: it neither answers nor counts as unanswered. The message is deleted at the next send (R4).
- No 🎙 button: voice needs none.

**Which ⭐**:
- Candidates come from `crud_favorite_meal.list_active` (crud_favorite_meal.py:22-30).
- Ranking, in order:
  1. a dish already logged today goes last;
  2. distinct local days in the last 28 with that dish (`meal_items.personal_food_id`, live and not draft, as `dishes._live_items`, dishes.py:143-154);
  3. `list_active` order.
- Take the top 4, with no model.

### 3.2 Voice or text after the reminder: the recall binding (R1)

No state is opened and no `ASK` slot is used, so this never collides with `meal_card`'s single slot (meal_card.py:54-55). Routing is unchanged:
- voice → `route_media` → NEW_MEAL (capture.py:1066-1079);
- text → `route_text` (capture.py:1081-1136).

Only the time columns and the send's `answer` change.

**A message is bound to a send when** it is from this person, not a forward, not a photo, and one of these holds:
- **(a) explicit reply** to the reminder message, any kind including a photo, within 2 local days of the send;
- **(b) window**: the send is live (`status` in `sent`/`maybe`, `closed_at` null) and the message came before `window_end = min(sent_at + 90 min, max(sent_at, answered_at) + 30 min)`. The window is 30 min after the send, renewed for 30 min by each bound answer, and never longer than 90 min.

**Never bound:**
- photos outside a reply, because a photo is food in front of the person now;
- a text or transcript containing «сейчас», «только что» or «прямо сейчас»;
- weights, routine names, `says_no`, and a bare «12:30» (it re-times the last meal as today, capture.py:1101-1110).

**Time of a bound meal** (`capture._time_columns`, capture.py:1644-1672, gains `recall=`; order `on_day > forward > said > recall > sent`):
- **evening send**: `meal_time = meal_card.noon_of(send.local_day)` (meal_card.py:1840), `day_only`, `time_source="reminder"`;
- **meal-slot send** (R3): reply (a) → `centre`, `approx`; window (b) → as evening (`day_only` on `send.local_day`).
- **Said time wins.** Voice gets its time later from the transcript (`spoken_words`, capture.py:2645-2657), which today applies only when `time_source == "sent"`. Widen it to `in ("sent", "reminder")` at **capture.py:2652** and at **capture.py:1627** (album caption).
- **Binding sets `send.answer = "words"` and `answered_at = now`.** This also renews the window.

**Reply line**: `meal_card.line_head` (meal_card.py:258-266) gains `time_source == "reminder"` → `f"Записано на {_when(meal, zone)}"`.
- `_when` (meal_card.py:253-255) renders `stats_screen._card_title` (stats_screen.py:114-115), whose weekday is short.
- So the line reads «Записано на пт 6 ноября, без времени: суп.», with the usual `Исправить` / `🕐 Время`.

**«Ничего» answers (R16)**: in `route_text`, before NEW_MEAL and only while a send is bound by (a) or (b), a full match of
`(сегодня |больше |почти |вообще )*(ничего|не ел[аио]?|не было)( не ел[аио]?| не было)?[.!…]*` (case-folded) means:
- no meal and no read;
- `send.answer = "none"`, `answered_at`, `closed_at`;
- the reminder message is edited to «🔔 <b>Еда за сегодня</b>\n✓ Принято.» with no buttons;
- no reply in the chat.

**Unchanged routes inside the window:**
- «нет», «нет, не ел» → silent and kept (meal_words.py:198, capture.py:1130-1132).
- «70.5» → a weight.
- A routine name → a tick.
- «забыла: йогурт» → R8 guarantees no meal was sent in the 3 h before the slot, so as a first answer it becomes a new meal (capture.py:1126).
- **«а ещё днём был йогурт»** after a first answer «суп»: `_ADD_WORDS` (meal_words.py:186-192) would join it to the soup. New rule: **inside a bound window, a corrective-add whose rest carries a time phrase becomes a new bound meal** with that time. Without a time phrase («а ещё йогурт») it joins the first answer, as today. Both are day_only on the same day, so no time is falsified.

### 3.3 Tap on a ⭐ chip (R1)

1. A new `favorites.log_at(t, fav, dish, name, *, meal_time, precision, source, extra_signals)` is extracted from `_log` (favorites.py:339-361). `_log` becomes `log_at(..., meal_time=t.now, precision="exact", source="sent")`.
2. The reminder calls it with:
   - `meal_time = noon_of(send.local_day)`, `day_only`, `source="reminder"` (meal slot: `centre`, `approx`);
   - `signals += {"via": "reminder", "send_id": id}`;
   - `resolution_source = "favorite"` kept, so `favorites.just_logged` (favorites.py:317-336, 3 s) and the undo checks keep working.
   - It sets `send.answer = "fav"` and `answered_at`.
3. The handler returns `Screen(meal_id=meal.id)`, so `capture.tapped` maps the message to the latest meal (capture.py:382-391, the pattern at telegram.py:1874-1893).
4. The message is edited in place, with toast `favorites.LOG_TOAST` «Записано»:
```
🔔 <b>Еда за сегодня</b>
✓ Записано на сегодня, без времени: Овсянка с бананом.
Можно нажать ещё или рассказать голосовым.
[✅ ОК]                                            ok:close (existing)
[↩️ Отменить] [🕐 Время]                           rm:u:{send_id}:{meal_id} · ml:t:{meal_id}:r
[Овсянка с бананом] [Кофе с молоком]               all chips stay, ≤ 2 rows
```
- **Tapped chips stay.** A later tap is a real second portion: `just_logged` already blocks an accidental double tap within 3 s.
- Each tap appends one `✓ Записано…` line. ↩️ and 🕐 act on the latest log.
- The day word: «на сегодня» when `send.local_day` is today. Otherwise the day is named: «✓ Записано на чт 5 ноября, без времени: …».
- The ✅ ОК / ✖ bottom row leaves after the first log. That gives 4 rows at most.

**When a chip is tapped late:**
- A tap after midnight logs to the **send's day**, not now. The message asked about that day, and an explicit reply follows the same rule. **[decision]**
- The result line names the day. The 🕐 screen then offers «Сегодня, 1:30» (R0), so a shift worker who meant "now" is one tap away.
- At more than 2 local days → the ⭐ list with a notice (below).

**↩️ Отменить** (`rm:u`):
- It runs the same checks as `favorites._v_undo` (favorites.py:426-441), then `meal_items.delete_meal` (meal_items.py:1997).
- The latest line becomes `↩️ Отменено: Овсянка с бананом.`, with toast «Отменено».
- With no undo left, the ✅ ОК row goes too.

**Stale cases:**
- The ⭐ was removed → toast «Этого блюда уже нет в избранном», and the keyboard is redrawn (favorites.py:60).
- The send is older than 2 local days → `favorites.open_list(db, telegram_id, 0, notice)` with `<i>Это напоминание за прошлые дни. Вот ⭐ Избранное.</i>`
- An unknown or foreign send id → today's card with a warning (`favorites._unknown`, favorites.py:367-370).

### 3.4 «✖ Выключить» (`rm:x`)

It applies at once, with no confirmation (SKILL §4). It sets `answer="off"` (neutral) and `closed_at`.
```
🔔 <b>Напоминания</b>
✓ Напоминания выключены. Включить снова: ⚙️ Настройки → 🔔 Напоминания.
[✅ ОК]
[↩️ Вернуть] [⚙️ Настроить]           rm:b:{send_id} · rm:o
```
- `↩️ Вернуть` restores `reminders.prev` exactly: `✓ Напоминания снова включены: вечером, в 21:00.` with no buttons.
- `⚙️ Настроить` opens §4.2 in place.

### 3.5 The 🕐 time screen for a day_only meal (slice R0, used by R1)

Today the day_only branch offers only «Вчера, …» plus typing (VERIFIED meal_card.py:1872-1874). Every ⭐ tap and untimed recall from a reminder is day_only, so in R0 that branch becomes:
```
<b>🕐 Время приёма</b>
Записано на: пт 6 ноября, без времени.
Другое время можно написать, например 12:40 или вчера 23:10.
[Утром] [Днём] [Вечером]                   ≈ 9:00 / 14:00 / 20:00 on the meal's day, approx
[В 21:40] [Вчера, 5 ноя]                   the meal's sent_at, exact · existing "y" chip
[↩️ …undo when present]
[⬅️ back]
```
- Part-of-day clocks are `meal_words._PARTS` (meal_words.py:18: утром 9, днём 14, вечером 20).
- **«В HH:MM»** sets `meal_time = meal.sent_at`, `exact`. When `sent_at`'s local day differs from the meal's day, the label is «Сегодня, 1:30».
- All three set `time_source="edited"` through the existing `_apply_time`.
- A part chip whose clock is later than now on today is not drawn.
- A typed clock lands on the meal's own day (VERIFIED meal_card.py:1941, `meal_day(meal, t.user)`). So «1:30» typed after midnight for yesterday's day_only meal means yesterday 1:30, and «Сегодня, 1:30» is the way to "now". This goes into the explanation entry.

### 3.6 Meal-slot reminder (slice R3)

```
🔔 <b>Еда около 13:30</b>
Можно не отвечать. Если что-то было — голосовым, текстом или блюдом из ⭐:
[chips ×≤4]
[😴 Не сегодня] [⚙️ Настроить]           rm:n:{send_id} · rm:o
```
- **⭐ tap or explicit reply** → `centre`, `approx`, `time_source="reminder"`. The line reads «Записано на пт 6 ноября, ≈ 13:30», and the result screen carries 🕐.
- **Window-bound free text or voice** → day_only on the send's day. A guessed centre is never stamped on a live message.
- **«😴 Не сегодня»** sets `off_day = local_day`. The message reads `✓ Сегодня больше без напоминаний.` with no buttons, `answer = "ok"` (neutral). It exists only in multi-slot modes, where it actually skips later slots.
- **Invariant**: a slot whose `centre` is later than its send time is never sent.

---

## 4. Settings

### 4.1 The ⚙️ hub (settings_screen.py:144-193)

**Text**: one new line in the first group, after «🗓 Итог недели…». It shows the **mode only**, never the pause:
- `🔔 Напоминания: вечером, 21:00`
- `🔔 Напоминания: выкл`
- from R2: `🔔 Напоминания: 13:00, 21:00`
- from R3: `🔔 Напоминания: по записям, 13:45 и 21:00`

**Buttons**: the hub stays at 5 rows / 11 buttons. Row 2's `[🗓 Итог недели: вкл]` is replaced by `[🔔 Напоминания]` (`rm:o`), and the week toggle moves into the 🔔 sub-screen. The hub's «🗓 Итог недели: вс, 22:00» text line stays. Old `set:week` buttons keep working (settings_screen.py:334). Update `tests/test_menus.py:416`. **[decision, Q4]**

### 4.2 Sub-screen «🔔 Напоминания» (routines.py sub-screen pattern)

**R1:**
```
<b>🔔 Напоминания</b>

Сейчас: вечером, в 21:00 по времени Тбилиси (UTC+4).
Одно сообщение в день без звука: можно рассказать голосовым, что было, или нажать на блюдо из ⭐. Если были записи за три часа до него, оно не приходит. В воскресенье вместо него приходит итог недели.
🗓 Итог недели: вс, 22:00.

[✖ Выкл]        [✓ 🕘 Вечером]                    rm:m:off · rm:m:evening
[20:00] [✓ 21:00] [22:00] [23:00]                   rm:e:2000 …
[🗓 Итог недели: вкл] [🕐 Часовой пояс]            rm:week · settings_screen.ZONES_CALLBACK
[⬅️ Настройки]                                      set:open
```

State lines:
- Off: `Сейчас: выключены.`
- **Paused** (replaces «Сейчас: …»): `Сейчас не приходят. Включить снова — кнопкой ✓ 🕘 Вечером.` A tap on the ✓ mode clears `quiet` and `fragile`, sets `count_from = now`, and shows the notice `✓ Напоминания снова приходят`.
- Week off: `🗓 Итог недели: не присылаю.` (In that case the Sunday sentence is dropped.)

Notices on a change, in italics on top (SKILL §4): `✓ Напоминания: вечером, в 21:00`, `✓ Напоминания выключены`, `✓ Время: 22:00`, and the week notices settings_screen already uses for `set:week`.

**R2 «✏️ Своё время»**: row 1 becomes `[✖ Выкл] [🕘 Вечером] [✏️ Своё время]`. In own mode, row 2 is `[✏️ Часы]`, which opens the picker:
```
<b>🕘 Своё время</b>
До трёх в день. Нажатие ставит или убирает.
[7:00]  [9:00]  [11:00]
[13:00] [15:00] [17:00]
[19:00] [20:00] [21:00]
[22:00] [23:00] [23:30]
[⬅️ Напоминания]
```
- A 4th tap gives the toast `Не больше трёх в день`.
- Removing the last one gives `Хотя бы одно время. Выключить — кнопкой ✖ Выкл.`
- No typed time: a bare «12:30» belongs to capture (capture.py:1101-1110).

**R3** adds a mode, and rows 1–2 become:
```
[✖ Выкл]        [🕘 Вечером]
[✏️ Своё время] [🔁 По записям]
[row 3: time chips (evening) | [✏️ Часы] (own) | nothing (по записям)]
[🗓 Итог недели: вкл] [🕐 Часовой пояс]
[⬅️ Настройки]
```
- 🔁 is drawn (build_tiles.py:67), so 🕐 keeps meaning clocks and zone.
- State lines for «По записям»:
  - with data: `Сейчас: по записям — 13:45 и 21:00 по времени Тбилиси (UTC+4). Часы взяты из записей за 4 недели и подстраиваются сами.`
  - too little data: `Сейчас: по записям. Пока записей мало — только вечером, в 21:00; часы появятся из записей сами.`
  - energy «минимум сил»: `В режиме «минимум сил» приходит только последнее за день.`
- «✏️ Своё время» opens pre-filled with the current learned hours (rounded to the nearest picker chip), so one tap removes a wrong one.

---

## 5. Scheduling

### 5.1 The sweep

- **Beat entry**, as `week-summaries` (celery_app.py:41-45): `"meal-reminders": {"task": "celery_app.tasks.reminders.send_reminders", "schedule": crontab(minute="*/15"), "options": {"expires": 840}}`. Add the module to `include=[...]` (celery_app.py:10-15).
- **Task module**, copying celery_app/tasks/week_push.py:15-40:
  - clamp httpx/httpcore to WARNING;
  - cheap `users_due` before `asyncio.run`;
  - `custom_emoji.build_bot` and `bot.emoji.load`;
  - no renderer.
- **Service** `app/services/reminder_push.py`, mirroring week_push.py:56-137: `users_due` with a try/except per user, `_claim`, `_send`, and `run` → counts sent, skipped, failed and blocked.

### 5.2 Slots of a day (pure: `reminders.slots_today(cfg, learned, local_date) -> list[Slot]`)

`Slot(at, kind: "evening"|"meal", centre|None)`.
- **evening** (R1): `[Slot(cfg.evening, "evening", None)]`. Choices are 20:00–23:00.
- **own** (R2): each chosen time `h` is a meal slot with centre `h − 45 min`. The last one at or after 18:00 is evening-kind.
- **usual** (R3): learned slots (§5.5). When `learn_slots` returns None → **evening only** at `cfg.evening`. There are no invented day hours. If the last learned slot is before 18:00 and fewer than 3 exist, `cfg.evening` is added as evening-kind.

### 5.3 A slot S is due at sweep time `now` when ALL of these hold

The cheap checks run first and the DB checks last. The first failure skips, and each skip is logged at DEBUG with its reason and no text.
1. The mode is not off. An absent mode reads as `evening 21:00` for the owner and `off` for everyone else (R2).
2. `may_receive(db, telegram_id)`.
3. At least one live meal ever (R3).
4. The last meal row (any, drafts and deleted included) is within 7 local days of S (R3).
5. `S ≤ local_now < S + 60 min`.
6. `S` is after `reminders.since`.
7. `local_day != reminders.off_day` (😴, R3).
8. The quiet level allows it (§5.6). With energy SIMPLE, only the last slot of the day goes (R3).
9. No `reminder_sends` row for `(user, local_day, S)`, and fewer than 3 today. From R2 on, also no send in the last 2 h 30.
10. **Fresh record (R8)**: no meal row with `sent_at ∈ [S − 3 h, now]`. Drafts and deleted rows count.
11. **Meal slot only**: no meal with a clock and `meal_time ∈ [centre − 2 h, now]`, and `centre ≤ now`.
12. `meal_card.has_slot(db, telegram_id)` is false (meal_card.py:960-962). If true, retry at the next sweep inside the hour.
13. **Sunday (R17)**: when `week_push_on(user)` (settings_screen.py:109-110) and the local day is Sunday, every evening-kind slot is skipped.

There is **no rule on how many meals were logged today** (former rule 10, removed).

### 5.4 Claim, send, settle

- **Claim** = `INSERT reminder_sends(status="claimed")` with `UNIQUE(user_id, local_day, slot)`, then commit. An `IntegrityError` → roll back, "skipped". This is mark-before-send (week_push.py:5-8).
- **Before sending**, close the previous send (`closed_at = now`). If its `answer != "fav"`, it has a `message_id` and it is younger than 48 h, `bot.delete_message` it. On failure, `edit_message_reply_markup(None)`. Errors are ignored.
- **Send** `bot.send_message(chat_id, text, parse_mode=HTML, reply_markup, disable_notification=True)`. Store `message_id`, `status="sent"` and `sent_at`.
- **Failures**, as week_push.py:99-125:
  - `Forbidden` → `blocked` (unanswered).
  - `may_have_arrived` (card_delivery.py:57-63) → `maybe` (neutral for the ladder).
  - Other errors → delete the row so the next sweep retries.
  - A worker killed between claim and send loses that slot. This is accepted.
- **Ladder write** (§5.6): after settling, the sweep recomputes the level and writes `reminders.quiet` when it changed.

### 5.5 Learned hours (R3, pure `reminders.learn_slots(meals, zone) -> list[Slot] | None`)

The result is cached once per local day in `reminders.learned`.
1. **Input**: meals over the 28 local days before today, live and not draft (`crud_meal.get_user_meals_between`, crud_meal.py:87-102).
2. **Moments**: `step_count._moments` (step_count.py:51-59) drops `day_only` and `no_food`. Also drop `time_source == "reminder"`. Use `meal_time`, not `sent_at`.
3. **Enough data**: at least 7 distinct days and at least 10 moments. Otherwise None.
4. **Midnight**: `step_count._turn` and `_offset` (step_count.py:62-79).
5. **Clusters**: start a new cluster at every gap of at least 90 min. A cluster wider than 4 h is split at its largest inner gap, repeatedly.
6. **Support**: keep clusters with food on at least 40 % of logged days and on at least 4 days. Keep at most 3 by support, ordered by time.
7. **Per cluster**: `centre = median`, `at = p80 + 45 min`, rounded up to the quarter hour. **A cluster whose `at` falls outside 05:00–23:30 is dropped, not clamped**: a night cluster is not used. If two `at` values fall within 2 h 30, keep the one with more support.
8. **Kind**: a slot at or after 18:00 is evening, the rest are meal.

The thresholds are the project's guesses (as `step_count._ENOUGH_DAYS`, step_count.py:24).

### 5.6 Unanswered → quieter → silent (R9)

**Send outcomes** (`reminder_sends.answer`):
- `fav`, `words`, `none` → **answered**;
- `ok` (✅ ОК / 😴), `off` (✖), and `status="maybe"` → **neutral**;
- null with `status` in (`sent`, `blocked`) → **unanswered**.

A meal logged at another time is **not** an answer.

**Streak**: walk sends with `sent_at > reminders.count_from`, newest first. Skip neutral ones, count unanswered ones, and stop at the first answered one.

**Threshold** `k = 1` if `reminders.fragile`, else 3.
- **Single-slot modes** (evening, or one own time): streak ≥ k → `quiet = {"level": "off", "on": local_day}`.
- **Multi-slot modes**: streak ≥ k → `level "one"` (last slot of the day only). Streak ≥ 2k → `"off"`.
- **An answered send** clears `fragile`.

**Resume** (lazy, checked at each sweep for a quiet person). It requires both:
- a local day D > `quiet.on` with **no meal row** (any, drafts and deleted included);
- meal rows on **2 distinct local days after D**.

The slot's day may be the 2nd of those days, never the 1st. On resume: `quiet = null`, `fragile = true`, `count_from = now`. Weights and routine ticks are ignored, and nothing is said.

**⚙️ resume**: a tap on the ✓ mode → `quiet = null`, `fragile = false`, `count_from = now`.

**Worked cases:**
- Daily breakfast logger who ignores evenings: paused after 3 evenings and stays paused. There is no gap day, so it never resumes unless ⚙️ is tapped.
- Hard week: 3 evenings, then nothing. Return Saturday: no reminder that day. One comes back on the evening of the 2nd record day.
- Mon/Thu/Sat logger who never answers: about 3 in week 1, then about 1 a week.

---

## 6. Data model

### 6.1 `User.settings["reminders"]` (JSON, no migration, always written as a new dict, as `settings_screen._save`, settings_screen.py:300-307)

```json
{
  "mode": "off" | "evening" | "own" | "usual",
  "evening": "21:00",
  "own": ["13:00", "21:00"],
  "since": "2026-11-06T17:20:00+00:00",
  "off_day": "2026-11-06",
  "prev": {"mode": "evening", "evening": "21:00", "own": []},
  "noted": true,
  "offered": true,
  "quiet": {"level": "off", "on": "2026-11-04"},
  "fragile": true,
  "count_from": "2026-11-06T17:20:00+00:00",
  "learned": {"on": "2026-11-06", "slots": [["13:45","meal","13:00"],["21:00","evening",null]], "days": 12}
}
```
- An absent dict → owner `{"mode":"evening","evening":"21:00"}`, others `{"mode":"off"}` with `offered` unset.
- `reminders.config(user) -> Config` resets bad values to the defaults, as `zone_name` does (settings_screen.py:95-102).
- The sweep writes `quiet`, `fragile` and `noted` with one read-modify-write. A rare race with a ⚙️ tap in the same second is accepted (INFERRED: the same race already exists for any two `_save` calls).

### 6.2 New table `reminder_sends` (one Alembic migration, no new env var)

| column | type | note |
|---|---|---|
| id | Integer PK | |
| user_id | FK users.id, not null | |
| local_day | Date, not null | the person's date at the slot |
| slot | String(5), not null | "21:00" |
| kind | String(8), not null | evening, meal |
| centre | String(5), null | meal slots |
| sent_at | DateTime(tz), not null | claim moment, updated on send |
| chat_id | BigInteger, not null | |
| message_id | BigInteger, null | |
| status | String(8), not null | claimed, sent, maybe, blocked |
| answer | String(8), null | fav, words, none, ok, off |
| answered_at | DateTime(tz), null | the last bound answer; renews the window |
| closed_at | DateTime(tz), null | ОК, 😴, ✖, «ничего», or the next send |
| created_at / updated_at | | BaseClass |

- `UNIQUE(user_id, local_day, slot)`.
- `INDEX(user_id, sent_at)`.
- `INDEX(chat_id, message_id)` for replies.
- No text, no dish.
- Purged after 60 days in `celery_app/tasks/periodic.py` beside `purge_action_events` (celery_app.py:36-39). The ladder state lives in settings, so the purge does not reset a pause.

### 6.3 Other changes

- **`app/schemas/meal.py:29`**: `TimeSource` gains `"reminder"`. There is no CHECK on `meals.time_source` (models/meal.py:66), so no migration. Readers: capture.py:1627 and :2652 (widened), meal_card.py:260-264 (new case), meal_items.py:107/1746 (copy), :2062 (`set_time` → "edited"), crud_meal.py:144.
- **`BUTTON_VERBS`** gains `"rm"` (action_event.py:25-37), and `BUTTON_NAMES["rm"] = "🔔 Напоминания"` (action_journal.py:42).
- **🔔 tile**: a `bell` path in `render/emoji/marks.js` and `("🔔", "bell", ["напоминание", "reminder"])` in `SET`; otherwise `tests/test_emoji_set.py:39-41` fails. 🔁 already exists.

### 6.4 Why the 3 h skip matters (VERIFIED)

`_ADD_WORDS` (meal_words.py:186-192) joins «забыла: суп» to `_last_meal` within `CORRECTION_WINDOW = 3 h` by `sent_at` (capture.py:195, :935-945, :1113-1126). With R8, the first «забыла: …» after a reminder becomes a new meal.

---

## 7. Reuse map (file:line)

| Need | Reuse |
|---|---|
| Due window, users_due, claim, send, failures | `app/services/week_push.py:31-137` |
| Task shell | `celery_app/tasks/week_push.py:15-40` |
| Beat, include, purge | `celery_app/celery_app.py:10-15, 28-45` |
| Access gate | `app/services/access_control.py:82-89` |
| Owner ids | `app/core/config.py:98-100` (`admin_ids`) |
| Maybe arrived | `app/services/card_delivery.py:57-63` |
| Zone, zone label, zone button, save, hub, week flag | `settings_screen.py:47, 55, 95-110, 129-134, 144-193, 300-307` |
| Post-read extra message (offer) | `telegram.py:995-1013` (`_first_record_hint`); `welcome_screen.py:155-170` |
| Local time, clock text | `app/services/day_card.py:26-30, 47-58` |
| Moments, turn, offset | `app/services/step_count.py:51-79` |
| Meals in a range | `app/crud/crud_meal.py:87-102` |
| ⭐ list, log, guard, undo, labels | `crud_favorite_meal.py:22-30`; `favorites.py:50, 60, 65-66, 317-361, 367-370, 426-441`; `meal_items.py:1997` |
| ✅ ОК row | `close_row.py:15-21`; handler `telegram.py:3043` |
| Time screen, typed time | `meal_card.py:1850-1891` (day_only branch 1872-1874), `:1933-1943`, verbs `:1949, :2111` |
| Part clocks | `meal_words.py:18` |
| Time finders, time_words | `meal_words.py:139-147, 160-180` |
| says_no | `meal_words.py:206-215` |
| Day without clock | `meal_card.noon_of` (`:1836-1840`); `capture._time_columns` (`capture.py:1644-1672`) |
| Reply line, date text | `meal_card.py:253-266`; `stats_screen.py:114-115` |
| Said-time application | `capture.py:1627, 2645-2657` |
| Born, message map | `capture.py:351-391, 1729-1766` |
| Callback handler shape | `telegram.py:1874-1893, 3046-3050` |
| Typed slot check | `meal_card.py:960-962` |
| Energy mode | `next_step.py:33-38` |
| Explain topics | `explain_screen.py:49-57, 75` |
| Text and emoji gates | `tests/test_user_text.py:25-41`; `tests/test_emoji_set.py:39-41` |
| Real-app fake, Postgres | `tests/test_two_people.py:95-98`; `Wire` in `tests/test_capture_router.py:57` |
| Beat test pattern | `tests/test_week_card.py:753-760` |

---

## 8. Implementation slices

Each slice goes on its own feature branch, and the hand-off is «готово, ветка X» plus a release note. Gate: `./scripts/test.sh`, the Postgres run of the claim path, and ≤ 4 review agents.

### R0. Honest time in capture (S–M, about 0.5 session; independent of reminders, releasable alone)

**Files:**
- **`app/services/meal_words.py`**:
  - **`time_phrases(text, sent, zone)`**: run each finder in `_FINDERS` repeatedly, masking each found span with spaces, until None. This returns all non-overlapping phrases in text order and reuses the finders unchanged.
  - **`time_words`**: if ≥ 2 phrases resolve to different moments → `SaidTime(noon of the common day (yesterday if all say «вчера», else the sent day), DAY_ONLY, phrase=None, rest=text, of_day=True)`. One phrase → as today.
  - **Meal-name finder** (the old R2), `APPROX`: «на завтрак», «за завтраком» → 9:00; «в обед», «на обед», «за обедом» → 13:00; «на полдник» → 16:00; «на ужин», «за ужином» → 19:00. These are ASSUMED, like `_PARTS`.
- **`app/services/meal_card.py`**: the day_only branch of `_time_screen` (1872-1874) gets `[Утром][Днём][Вечером]` and `[В HH:MM][Вчера, …]` (§3.5), with new chip kinds through `_v_time_set`.

**Tests:**
- «утром каша, в 19:30 паста» → day_only, not 19:30. «в обед суп, а вечером паста» → day_only.
- «вчера утром каша, вечером суп» → day_only yesterday.
- «вчера утром каша» → ≈ 9:00 yesterday (one phrase, overlap masked). «в 13 суп» → 13:00 exact (control).
- «в обед суп» → ≈ 13:00. «обеденный стол» stays food.
- Through `capture._said`: a typed «утром каша, в обед суп» → line «Записано на пт 6 ноября, без времени».
- Time screen for a day_only meal: «Днём» → approx 14:00, `time_source="edited"`.
- «В 21:40» → exact `sent_at`. A sent_at on the next day → label «Сегодня, 1:30» and the moment lands on that day.
- A part later than now is not drawn. The exact-time branch is unchanged (control).

### R1. Evening reminder with voice and ⭐ (L, about 2 sessions; one branch, two commits: R1a send/⭐/settings/offer, R1b recall/«ничего»)

**Files:**
- **New `app/services/reminders.py`**:
  - `config`, `slots_today` (evening), `is_due` (§5.3), `outcome`/`streak`/`level`/`resume_due` (§5.6), `pick_favorites`;
  - the texts and `reminder_view`;
  - the offer;
  - the `rm:` verbs `f u k x b o m e week y no`;
  - the sub-screen;
  - `bound_send(db, user, came)` and `is_nothing(text)`.
- **New `app/services/reminder_push.py`**. **New `celery_app/tasks/reminders.py`**. Edit `celery_app/celery_app.py` and `celery_app/tasks/periodic.py`.
- **New `app/models/reminder_send.py`** (registered in `app/db/base.py`), **`app/crud/crud_reminder_send.py`**, and a migration.
- **`app/crud/crud_meal.py`**: `any_sent_between(user_id, start, end)`, `last_sent_at(user_id)` and `record_days(user_id, start, end)`, all including drafts and deleted rows.
- **`app/services/favorites.py`**: `log_at`.
- **`app/services/capture.py`**:
  - `_time_columns(..., recall=None)`;
  - `bound_send` in `born` / `_born_exact`;
  - the «ничего» branch in `route_text`;
  - the corrective-add-with-time rule;
  - widen :1627 and :2652.
- **`app/services/meal_card.py`**: the `line_head` case. **`app/schemas/meal.py:29`**: the Literal.
- **`app/services/settings_screen.py`**: the hub line and the row-2 swap.
- **`app/services/telegram.py`**: `reminders_callback` (`^rm:`) and `_reminder_offer` after a read (beside `_first_record_hint`).
- **`action_event.py`, `action_journal.py`**: `rm`. **`explain_screen.py`**: the topic. **`render/emoji/*`**: 🔔.

**Tests** (`tests/test_reminders.py` unless noted):
- **Due:**
  - one test per §5.3 rule, plus a control that sends when no rule matches;
  - 21:00 / 21:59 / 22:00 edges;
  - `since`;
  - owner absent-config → due; invited absent-config → not due;
  - last record 8 days ago → skip; 6 days → due;
  - Kolkata at a :30 sweep;
  - Sunday with week on → skip; Sunday with week off → send;
  - **records at 8:40, 11:00, 13:10 → sent** (no count rule).
- **Ladder:**
  - 3 unanswered → quiet off;
  - ОК and ✖ neutral (3 ОК in a row → still full);
  - `maybe` neutral; blocked unanswered;
  - **a breakfast the next morning does not answer and does not resume**;
  - resume needs a gap day plus 2 record days, and never sends on the first;
  - weight and routine records do not resume;
  - after resume, 1 unanswered → quiet again;
  - an answered send clears `fragile`;
  - ⚙️ tap resumes;
  - a 60-day purge keeps the pause.
- **Claim:**
  - two `run()` calls → one send;
  - IntegrityError → skipped;
  - maybe → no resend;
  - ConnectError → retry;
  - Forbidden → blocked;
  - **also on Postgres** via `TWO_PEOPLE_DB_URL`.
- **Zero model calls**: patch `OpenAIService` and every read entry to raise; run a sweep, a ⭐ tap, the offer, and a «почти ничего не ела».
- **Message:**
  - first text with footnote naming the zone, once;
  - later text short;
  - no time list on any day (assert no `\d:\d\d` in the body apart from the footnote's slot);
  - no ⭐ → no chips and no ⭐ wording;
  - ranking;
  - labels ≤ 20;
  - ≤ 3 rows;
  - ОК deletes nothing at once but the next send deletes it.
- **⭐ tap:**
  - day_only on the send's day, `reminder`, `favorite`;
  - ✅ ОК first;
  - chips stay;
  - a second tap of the same chip after 3 s → two meals; within 3 s → one;
  - ↩️;
  - stale ⭐;
  - a send over 2 days old → ⭐ list;
  - a foreign id → card;
  - a tap after midnight → the line names yesterday, and 🕐 offers «Сегодня, …».
- **Recall** (via `Wire` / `Chats`):
  - «суп» at +10 min → day_only, line «Записано на пт 6 ноября, без времени»;
  - «в 13 суп» → 13:00;
  - voice «утром каша» → ≈ 9:00;
  - photo → sent time;
  - **«йогурт» at +40 min with no earlier answer → exact sent time**;
  - «суп» at +10 then «хлеб» at +35 → both bound (window renewed);
  - a bound answer at +80 then a message at +95 → not bound (90-min cap);
  - «ем сейчас пасту» at +5 → exact;
  - a reply after 3 h → day_only;
  - «забыла: йогурт» → new meal;
  - «суп» then «а ещё днём был йогурт» → two meals, the yogurt ≈ 14:00;
  - «суп» then «а ещё хлеб» → joins (control);
  - «нет» → silent and unanswered;
  - **«почти ничего не ела» → no read, «✓ Принято.», answered**; the same text outside a window → normal route (control).
- **Offer:**
  - on the 3rd record day, once, never for the owner;
  - zone row only without a chosen zone;
  - Да → evening, Не надо → off;
  - no answer → never re-sent.
- **Settings:**
  - hub 5 rows / 11 buttons;
  - the hub line never says the pause;
  - `set:week` legacy;
  - the sub-screen ≤ 4 action rows + back, the zone named, the paused line.
- **Gates:**
  - `test_user_text` scans new strings;
  - an R10 list over reminder, offer and explain strings, with a positive control (a scratch string with «пропущено» fails);
  - `test_emoji_set` with 🔔;
  - beat entry and include.

**Release note**: one migration, no new env var. Reminders turn on only for the owner. Invited people are asked once after their own record on their 3rd day with records.

### R2. «✏️ Своё время» (S–M, about 0.5 session; before R3 because it needs no learning)

- The picker (§4.2), own slots with centre h − 45, a 😴 row on meal-kind messages, and the 2 h 30 guard.
- **Tests**: the cap of 3; the last time can't be removed; one own time follows the single-slot ladder; a 15:00 meal slot answer by ⭐ → ≈ 14:15; 😴 skips later slots and is neutral; an untouched 13:00 is deleted before the 20:00.

### R3. «🔁 По записям» (M–L, about 1.5 sessions)

- `learn_slots` with its daily cache, the meal-slot message (§3.6), chips ranked within ±90 min of the centre, the multi-slot ladder, energy SIMPLE → last slot only, rule 11, the evening-only fallback, and the pre-filled own-time picker.
- **Tests:**
  - 3 clear clusters;
  - too little data → None → evening only, with the screen line;
  - a 23:30–01:30 cluster → one cluster, dropped (at outside band);
  - a mixed 3-night/4-day schedule: no slot with centre > at;
  - grazing split;
  - day_only, no_food and reminder-sourced rows ignored;
  - two slots within 2 h 30 → one;
  - a sliding window moves a stable slot ≤ 15 min;
  - Berlin DST, March and October: no double and no lost send;
  - a zone change mid-day → no second send within 2 h 30;
  - **text 55 min after a meal-slot send → day_only, not the centre**;
  - a reply → centre ≈.

### R4 (optional, owner's yes and price first). Splitting a day recall into several meals

- `time_phrases` already returns spans (R0).
- A `meal_sources.part` column joins `uq_meal_sources_message` (models/meal_source.py), with a migration.
- `born` makes N meals, with one read per part: about N× the paid text cost.
- Precedent: `meal_items.split_album` (meal_items.py:1694).
- Price one call first, and stop and ask above $0.20.

---

## 9. Acceptance scenarios (persona agents)

The real application runs through `tests/test_two_people.py`'s `Chats` (real handlers, fake Telegram, SQLite and `TWO_PEOPLE_DB_URL` Postgres). The sweep is `reminder_push.run(db, fake_bot, now)` at fixed clocks. Each agent records every message text and its buttons, then checks the expectation. The zone is Asia/Tbilisi unless stated. «Invited, opted in» means the person tapped «🔔 Да, в 21:00».

| # | Persona | Slice | Steps | Expected |
|---|---|---|---|---|
| A1 | Owner, logs most meals, 6 ⭐ | R1 | Logs 8:40, 13:10, 19:50; sweeps 20:45–22:15 | No reminder (19:50 within 3 h). |
| A2 | Owner | R1 | Logs only 8:40; sweep 21:00, then 21:15 | One silent message: the first-ever text with the footnote «Приходит в 21:00 по времени Тбилиси (UTC+4)…», no list of times, ≤ 4 chips, [✅ ОК] [✖ Выключить]. Nothing at 21:15. The next day the short text. |
| A3 | Owner taps two ⭐ then undoes | R1 | Taps soup, hummus, soup again after 5 s, ↩️ | Three ✓ lines, then «↩️ Отменено: Суп чечевичный.». Chips stay. ✅ ОК first. The day card shows one soup and one hummus «без времени». The journal holds only `rm`. |
| A4 | Owner, morning logger | R1 | Logs 8:40, 11:00, 13:10; sweep 21:00 | The reminder **is** sent. |
| B1 | Invited, opted in, hard week | R1 | Last record Monday; sweeps Tue–Sat 21:00 | Messages Tue, Wed, Thu only. No text names days, absence or «снова». The ⚙️ hub line still reads «вечером, 21:00». The 🔔 sub-screen reads «Сейчас не приходят…». |
| B2 | Same person returns | R1 | Photo Saturday 12:00; sweep Sat 21:00; record Mon 9:00; sweep Mon 21:00 | **No reminder Saturday.** Sunday skipped. A reminder on Monday 21:00. Thursday's untouched reminder was deleted at Monday's send. |
| B3 | Owner logs breakfast daily, ignores evenings | R1 | 3 evenings with no tap; breakfast each morning; sweeps for 7 more evenings | Quiet after the 3rd. Breakfasts never resume it. A tap on ✓ 🕘 Вечером in ⚙️ resumes it the same evening. |
| B4 | Invited, opted in, logs Mon/Thu/Sat, never answers | R1 | 4 weeks of sweeps | ≤ 3 reminders in week 1, ≤ 1–2 a week after. |
| B5 | Invited, opted in, taps ✅ ОК every evening | R1 | 5 evenings | 5 reminders. ОК is neutral, so no pause. Each previous one is deleted at the next send, so the chat holds one. |
| C1 | Invited newcomer | R1 | Joins; records on days 1, 2, 3; sweeps each evening | No reminder ever. After the day-3 read, one offer with «по времени Тбилиси (UTC+4)». Nothing more if ignored. |
| C2 | Invited, last record 20 days ago, opted in long ago | R1 | Sweeps at launch | Nothing until a new record. Then rule 4 passes. |
| C3 | Invited, no zone chosen, real zones Berlin and Novosibirsk | R1 | Offer shown | The offer names Тбилиси (UTC+4) and has [🕐 Часовой пояс]. No reminder before Да. After choosing Novosibirsk and Да, the reminder arrives at 21:00 Novosibirsk. |
| D1 | Recall by voice | R1 | 21:05 voice «утром каша»; 21:12 text «суп»; 21:20 photo | Voice ≈ 9:00 said. Text day_only, «Записано на пт 6 ноября, без времени: суп.» Photo 21:20 exact. Nothing joins an older meal. |
| D2 | Day recall in one message | R0+R1 | 21:05 voice «утром овсянка, в обед суп» | One meal, **day_only**, «без времени». 🕐 offers Утром/Днём/Вечером. No false clock. |
| D3 | Late eater | R1 | Ignores the 21:00 reminder; 21:40 text «йогурт» | **21:40 exact** (window over). At 21:20 instead: day_only, and 🕐 → «В 21:20» → exact. |
| E1 | No ⭐ | R1 | Sweep | No chips and no ⭐ wording. Keyboard [✅ ОК] [✖ Выключить]. |
| E2 | Turns off | R1 | ✖, ↩️ Вернуть, ✖, ⚙️ Настроить | Off at once. Restored exactly. The sub-screen opens in place. Nothing while off. |
| F1 | «забыла» | R1 | Last record 14:00; 21:02 «забыла: йогурт» | A new meal, day_only. Not joined to 14:00. |
| F2 | «нет» | R1 | 21:01 «нет» | Silent. Unanswered for the ladder. |
| F3 | Low-intake day | R1 | 21:10 «почти ничего не ела» | No read, no M3A line. The reminder reads «✓ Принято.» and counts as answered. |
| F4 | Second recall | R1 | 21:05 «суп»; 21:08 «а ещё днём был йогурт» | Two meals: soup without a clock, yogurt ≈ 14:00. |
| G1 | Kolkata +5:30 | R1, R3 | Evening 21:00 | Sent at the 15:30 UTC sweep. In R3 with only dinners at ~20:00: one learned slot, no meal slots. |
| H1 | Blocked the bot | R1 | Forbidden at 21:00 | `blocked`, no retry. After 3, quiet. Others are served. |
| H2 | Crafted callback | R1 | Person 2 sends `rm:f:{person1_send}:{fav}` | Today's card with a warning. Nothing is logged. |
| H3 | Taps yesterday's reminder next morning | R1 | Taps a chip at 08:00 | «✓ Записано на чт 5 ноября, без времени: …». 🕐 offers «Сегодня, 8:00». At 3 days old, the ⭐ list. |
| H4 | Sunday | R1 | Week on; sweeps 20:00–23:00 | No reminder. The week card at 22:00. With week off, the 21:00 reminder goes. |
| I1 | Owner «🔁 По записям», 4 weeks | R3 | Opens it | The learned hours and the zone are shown. With breakfast logged, the morning slot is skipped. «Еда около 13:00» comes at about 13:45. A ⭐ tap → ≈ 13:00. A typed «суп» at 14:10 → day_only. |
| I2 | Night shift, eats 23:30–01:30 | R3 | 2 weeks | The night cluster is not used. Only day and evening slots. No slot asks about a later centre. |
| I3 | «минимум сил» | R3 | 3 slots | Only the last slot. The screen says so. |
| I4 | Shift worker, meal slot | R3 | Send 11:45 «около 11:00»; text «омлет» at 12:40 | Day_only, not ≈ 11:00. |
| J1 | Traveller | R3 | Tbilisi → Berlin at noon | No second send within 2 h 30. The sub-screen names Berlin after the change. |
| K1 | Own times | R2 | Picks 13:00 and 20:00, tries a 4th | Toast «Не больше трёх в день». An untouched 13:00 is deleted before the 20:00. |
| K2 | Night owl | R1 | Evening 23:00 | Allowed and sent at 23:00. |

---

## 10. Explanation entry (`explain_screen.TOPICS["reminders"]`)

- **label**: «🔔 Напоминания»
- **what**: «Короткое сообщение без звука: если что-то из еды вспоминается, можно рассказать голосовым, написать или нажать на блюдо из ⭐. Отвечать не обязательно.»
- **how**: «По умолчанию раз в день, вечером. В ⚙️ Настройки → 🔔 Напоминания можно выбрать время, выключить их или (позже) получать по часам из записей, не больше трёх в день. Если были записи за три часа до напоминания, оно не приходит. В воскресенье вечером вместо него приходит итог недели. Ответ без времени записывается на этот день без часов, время можно поправить кнопкой 🕐: там есть «утром», «днём», «вечером» и время, когда ответ был прислан. Одно сообщение — один приём; если в нём несколько времён, оно записывается без часов. Кнопка ✅ ОК убирает напоминание. Если три напоминания подряд остаются без ответа, они перестают приходить и возвращаются после перерыва в записях или кнопкой в настройках.»
- **why**: «Одним людям напоминания помогают не забывать, другим мешают, поэтому их легко выключить, а без ответа они затихают сами. Сколько напоминаний лучше, исследования не говорят: числа здесь выбраны проектом.»
- **source**: from `docs/researches/gentle-feedback-evidence.md:121,145` and `hard-days-progress-evidence.md:46`, rewritten per G5-R22. No ED material.
- The R10 list runs over this text in tests.

---

## 11. Edge cases (decided)

- **Draft mode**: drafts count for R8, for rule 4 and for resume. A ⭐ tap saves directly (as `_log`). A recall capture follows `draft_on` (capture.py:1767).
- **Zone change across midnight**: one extra or one missed reminder is possible. Accepted (05-decisions, slice F).
- **DST**: slots are wall clock and claims are keyed by local day. 00:00–04:59 is excluded.
- **Calendar day**: «Еда за сегодня» and the day of a day_only answer use the calendar day, not `step_count`'s turn. The message lists no times, so a night owl's 01:05 is never shown as «сегодня». A tap after midnight is covered by «Сегодня, HH:MM» on 🕐.
- **Subscription expired**: no send. Taps go through the usual gate.
- **A person who logs live**: R8 skips most reminders, and the ladder pauses the rest if they are ignored.
- **Two people**: separate zones, slots, claims and ladders. The owner sees `rm` taps and at most global totals (R13).
