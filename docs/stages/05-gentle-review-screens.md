# Stage 5: screens for slices N, M, O

The one spec for building the dish base, the meal card, capture, history and dishes. Stories: [`05-gentle-review-stories.md`](05-gentle-review-stories.md), slices N, M, O. Decisions: [`05-gentle-review-decisions.md`](05-gentle-review-decisions.md), G5-R24 and R30–R36. Build order is N, then M, then O (G5-R34).

**Labels.** VERIFIED: checked in this repo while writing. INFERRED: reasoned, with the reason given. ASSUMED: a project guess with no evidence behind the value. Every clock value, window and retry count here is ASSUMED, and lives in one named constant so it can change.

**How to read a screen.** A ` ```tg ` block is the exact HTML text. A ` ```kb ` block is the keyboard: one line per row, `label => callback`, cells split by ` | `. Placeholders: `<mid>` meal, `<iid>` item, `<fid>` dish, `<eid>` event, `<sid>` source, `<id>` favourite, `<day>` yyyymmdd, `<ts>` yyyymmddHHMM in UTC, `<pg>` page, `<b>` origin (section 2.4). Sample data is invented.

Decisions that bend a written story are marked **Decision**. Section 9 lists the ones the owner should see.

## 1. The design on one page

1. **Sending is the finish.** The row is committed before any download or model call. One silent line answers, and the result is edited into it. Nothing waits for an answer.
2. **Every food message is born as its own meal.** A relation only joins two meals that exist, so `➖ Отделить` always has something to return to.
3. **What was sent never changes.** Sources are immutable, readings are versioned, corrections sit beside them.
4. **A button carries the target state, never a delta.** A double tap or a two-week-old button sets the same value.
5. **Undo is checked against the present.** `↩️ Вернуть` applies only if the fields still hold what its event wrote.
6. **A dish correction never reaches a past meal.** Items keep their own name, grams and numbers.
7. **An unknown is shown as unknown.** No time reads «без времени», no numbers reads «не посчитано», never an invented dot or a 0.
8. **No silence, no counting at the person.** Every message type gets a line saying whether it entered the diary, apart from a short small-talk list. No line counts meals, helpings or photos as a comment.

## 2. Shared rules

### 2.1 Names

| Thing | Word in text and buttons | Back label |
|---|---|---|
| The bot's one-line reply | Записано | `⬅️ К ответу бота` |
| A meal's card | Приём | `⬅️ К приёму` |
| A dish inside one meal | Блюдо в приёме | `⬅️ К блюду в приёме` |
| A dish in the base | Блюдо | `⬅️ К блюду` |
| A day's meals | Приёмы | `⬅️ Приёмы дня` |
| All dishes | Блюда | `⬅️ Все блюда` |
| A dish's meals | Когда было | `⬅️ Когда было` |
| Favourites | Избранное | `⬅️ Избранное` |
| Day and week cards | Сегодня, Вчера, 7 дней | `⬅️ Назад: сегодня`, `⬅️ Назад: вчера`, `⬅️ Назад: 7 дней` |
| A favourite's own screen | Избранное блюдо | `⬅️ К избранному блюду` |

**A back label for one thing says where it goes (`⬅️ К приёму`), one for a list names the list (`⬅️ Приёмы дня`).** `⬅️ Назад: сегодня` beside day arrows says «back», so it does not read as one more day, and it names the card the person came from, not the day being walked.

**Verbs.** A 🗑 label names what it leaves: `🗑 Из приёма` takes a dish out of one meal, `🗑 Из списков` hides a dish from the lists, `🗑 Из избранного` takes off the ⭐. `🗑 Удалить` is for a whole meal, including a meal that holds only a photo. No screen says a bare `🗑 Убрать`. `↩️ Вернуть` undoes an edit or a removal. `↩️ Отменить` undoes a fresh favourite log. `🔁 Ещё раз` repeats a reading. `🔄 Заменить` swaps. `🔗 Объединить` joins, `➖ Отделить` separates. A line in the chat is a message, not a place, so it has no back button.

### 2.2 Modules and callbacks

- `app/services/meal_card.py`, prefix `ml:`: line, card, dish, picker, time, sources, day list. `ml:` does not match the old `^meal:` pattern (VERIFIED by regex form).
- `app/services/dishes.py`, prefix `dh:`: dishes list, dish card, when it was, merge.
- `app/services/favorites.py`, prefix `fav:` stays.
- Every new screen returns `favorites.Screen`, and is registered outside the conversation. Screens that may meet a photo card go through `card_delivery.tap`.
- Every verb checks ownership by hand (map of today's code: `favorites.handle_callback`). A foreign or malformed id gets the same answer as unknown data: today's card and a `logger.warning`.
- Layout limits: at most 4 action rows and 1 back row, at most 3 buttons per row (`tests/test_menus.py`), callback at most 64 bytes, label budget 34 alone, 20 at two per row, 13 at three per row.
- **A pager says where it is.** `◀️`, `1/3`, `▶️`, the middle cell a no-op (`ml:nop`, `dh:nop`, `hw:nop`, `fav:x`). Where the middle holds `🔍 Найти`, the text carries `Страница 1 из 3.` One page: no pager. Chip pages on `N1` and `O1` are exempt, because the text already lists every dish or meal.

| Verb | Meaning |
|---|---|
| `ml:r:<mid>` | back to the line |
| `ml:o:<mid>:<b>`, `ml:op:<mid>:<pg>:<b>`, `ml:mo:<mid>:<b>` | card, next page of dish chips, the card's `Ещё ›` |
| `ml:i:<iid>:<b>`, `ml:q:<iid>:<h\|1\|2>:<b>`, `ml:g:<iid>:<b>`, `ml:ga:<iid>:<grams>:<b>`, `ml:x:<iid>:<b>` | dish, quantity, grams, apply grams from a reply (`M5d`), remove |
| `ml:pk:<mid>:a:<b>:<pg>`, `ml:pk:<iid>:r:<b>:<pg>`, `ml:pa:<mid>:<fid>:<b>`, `ml:pr:<iid>:<fid>:<b>` | picker, add picked, replace picked |
| `ml:t:<mid>:<b>`, `ml:ts:<mid>:<ts>:<e\|a\|d\|y>:<b>` | time, set time (exact, approx, day only, day only on the button's day) |
| `ml:d:<mid>:<b>`, `ml:u:<eid>:<b>` | delete, undo by event |
| `ml:n:<mid>:<0\|1>:<b>`, `ml:ni:<iid>:<0\|1>:<b>` | numbers, as on the day card |
| `ml:fm:<mid>:<0\|1>:<b>`, `ml:fi:<iid>:<0\|1>:<b>` | ⭐ whole meal, ⭐ dish |
| `ml:gr:<mid>:<b>`, `ml:gt:<mid>:<key>:<b>` | groups, toggle a group |
| `ml:j:<mid>:<mid>:<b>`, `ml:jl:<mid>:<b>`, `ml:z:<sid>:<b>` | join two meals, picker, separate a source |
| `ml:src:<mid>:<b>`, `ml:sf:<mid>`, `ml:rr:<mid>:<b>`, `ml:w:<mid>:<b>` | sources, show photo, read again, write |
| `ml:vt:<mid>:<0\|1>`, `ml:nf:<sid>`, `ml:ok:<mid>` | voice text, «это еда», confirm a draft |
| `ml:ka:<sid>:<iid>`, `ml:kw:<sid>`, `ml:kn:<sid>` | chooser: this dish, whole meal, separate meal |
| `ml:zq:<mid>:<b>`, `ml:za:<mid>` | album split: question, do it |
| `ml:dl:<day>:<b>`, `ml:da:<day>:<b>` | day's meals, add to that day |
| `dh:l:<pg>`, `dh:f`, `dh:o:<fid>:<b>`, `dh:mo:<fid>:<b>` | dishes, find, dish card, the card's `Ещё ›` |
| `dh:rn:<fid>`, `dh:nm:<fid>`, `dh:n:<fid>:<0\|1>`, `dh:h:<fid>:<pg>` | rename, edit numbers, numbers, when it was |
| `dh:fv:<fid>:<0\|1>`, `dh:log:<fid>`, `dh:x:<fid>`, `dh:u:<eid>` | ⭐, log, hide, undo |
| `dh:jl:<fid>:<pg>`, `dh:jf:<fid>`, `dh:j:<fid>:<fid>` | merge: picker, find, do it |
| `fav:p:<pg>`, `fav:l:<id>:n`, `fav:o:<id>:<pg>`, `fav:u:<mid>`, `fav:g:<id>`, `fav:r:<id>:<pg>`, `fav:b:<id>:<pg>`, `fav:x` | shipped verbs; `fav:l` now fires from the list, `fav:g` is new |

### 2.3 One typed slot

`User.settings["ask"] = {kind, meal_id, item_id, food_id, day, message_id, at}`. It lives in the database, so a restart between tap and text cannot turn an answer into a meal. TTL 10 minutes, the same as today's waits. Any other inline tap closes it (one leave handler, like `routine_left_by_tap`).

Kinds: `grams`, `time`, `dish_add`, `dish_replace`, `meal_text`, `day_add`, `dish_name`, `dish_numbers`, `dish_find`, `merge_find`, `portion`.

**A wait never eats food.** A kind that expects a number or a clock takes only what parses. Anything else closes the slot and continues down the router. Kinds that expect a name take any text, because the person just asked to type one. No new conversation state is added.

### 2.4 Origin

`<b>` travels through every nested tap and decides the back row:

| Value | Opened from | Back from the card |
|---|---|---|
| `r` | the line | `⬅️ К ответу бота` => `ml:r:<mid>` |
| `t`, `y`, `w` | Приёмы, opened from Сегодня, Вчера, 7 дней | `⬅️ Приёмы дня` => `ml:dl:<day>:<b>` |
| `h<fid>` | Когда было | `⬅️ Когда было` => `dh:h:<fid>:0` |

The dish card's own back token, `l<pg>`, `i<iid>` or `f<id>`, uses the same slot and leads to `⬅️ Все блюда`, `⬅️ К блюду в приёме` or `⬅️ Избранное`.

### 2.5 Notices and undo

Change notices go on top as `<i>✓ …</i>` followed by an empty line. After a change the screen keeps its layout and gains `↩️ Вернуть` for one render (`ml:u:<eid>:<b>`). The undo writes a new event, so the undo can itself be undone.

Guard: when the fields no longer hold what the event wrote, nothing changes and the current screen shows `<i>С тех пор запись менялась, поэтому возврат не применён.</i>`.

### 2.6 Numbers and quantity

- Numbers follow the shipped tri-state of `numbers.py`: `🔢 Цифры` / `✓ 🔢 Цифры`, and the button is absent when `🔢 Цифры сразу` is on. Open numbers sit in `<blockquote expandable>`.
- **Decision (rule 1 vs US-N2 «с количеством», R35 «grams secondary»):** a dish line shows `½ порции`, `2 порции`, the person's own words (`amount_text`, for example `чипсы, три пачки`), or grams the person typed. Model grams appear only inside open numbers. ×1 writes no quantity.
- A number is printed the same at any size: no cap, comment or colour. A test asserts no clamp on 3000.
- **An estimate carries `≈` on every line that shows it,** totals included, so a total never reads more exact than its parts. Only barcode, label and typed numbers go without.

### 2.7 Hard days

The record is complete after sending. Nothing below asks for a tap, offers tidying, or remarks on a time, a repeat or a size. `минимум сил` hides `Ещё ›` and other rare rows from the bottom up, so the rows that stay do not move. A count beside food is hidden at `минимум сил` everywhere (`O2h`, `O3h`). The bot does not infer the mode (G5-R11). Each screen names its variant, and `h` ids are the ones whose text or keyboard differs.

### 2.8 Stale buttons, one rule

- Meal deleted: today's card through `card_delivery.tap`, with `<i>Этого приёма уже нет, вот сегодняшний день.</i>` on top.
- Meal merged away: the surviving meal's card with `<i>Этот приём объединён с приёмом пн 26 октября, 13:20.</i>`.
- Dish removed from a meal: the card with `<i>Этого блюда в приёме уже нет.</i>`.
- The same target twice: the state is unchanged, toast `Уже так`.

Screens say "S" below when this rule is all there is.

### 2.9 How a line reads

- **A meal named in a line or a button carries its day:** `пн 26 октября, 13:20`, shortened to `пн 26 окт, 13:20` on a button. A bare `13:20` is not enough, because a reply or a corrective word can reach an older meal. The day, the same way, on anything that names one meal: join, add, replace, grams, time, draft, a dead photo.
- **Every line about a message that did not become a meal says so:** `в дневник не записано`, plus what is possible next when something is.
- **A line about a message that is saved but unread says it is in the diary, and with what:** `В дневнике запись с этим фото.`, the word following the source. A person should not have to guess the opposite of `в дневник не записано`, or ask a record of what.
- **An example is written as an example:** `например 150`, never `числом: 150`, which reads as the current value.
- **Italic is for notices, source tags and optional hints.** What the person needs in order to know the state of the record is plain text, because italic is the first thing to go in glare or at low vision.
- **A button named in a sentence carries its whole label,** `«🔁 Прочитать заново»`, because a bare mark can mean anything. `✏️` on `O9` is that button's whole label.
- **An earlier record is not called old.** `Старая запись` read as a lesser one, and a person with a long history meets it most. The line says what differs: the numbers are for the whole record.
- **A choice can be left unanswered, and says so right under the title** (`M5d`, `M6a`).
- **No word the person has not been shown.** `одной строкой`, `из списков` without saying which, `исходное`: each one is said in plain words where it matters.
- **Quantity is explained in words, not as a formula** (`N2`, `N2h`), because `×1` says nothing about what was recorded.

## 3. Data model and migration

One hand-written additive migration per slice, first on `down_revision = "a9b8c7d6e5f4"` (VERIFIED, `alembic heads`). All new columns nullable or with a server default, because old containers may meet the new schema. New tables have NOT NULL timestamps with a server default. New models go into `app/models/__init__.py`. Migrations do not run in tests, so each slice gets one free check on a local Postgres before handoff.

### 3.1 Slice N

**`meal_items` (new).**

| Column | Rule |
|---|---|
| `meal_id` FK, indexed | current meal, CASCADE |
| `origin_meal_id` | immutable, the meal it was born in |
| `personal_food_id` FK, indexed, SET NULL | current dish |
| `original_food_id` | immutable, makes dish merge undoable at any time |
| `position`, `name` | own copy of the name, up to 120 |
| `amount_text` | the person's words |
| `grams`, `base_grams` | nullable |
| `base_calories`, `base_proteins`, `base_fats`, `base_carbohydrates` | nullable, as read |
| `factor` | default 1; shown numbers are base × factor, so ½ after ×2 is half of the base |
| `numbers_source` | `barcode\|label\|dish\|model\|plate_share\|user\|none` |
| `by_user` | a person's edit; a re-read leaves this item alone |
| `replaced_by_item_id`, `removed_at` | soft removal and replace chain |
| `created_at`, `updated_at` | |

**`meals`, new columns.**

| Column | Backfill | Rule |
|---|---|---|
| `sent_at` | `created_at`, else `meal_time` | `message.date`, never updated |
| `time_precision` | `exact` | `exact\|approx\|day_only`. `meal_time` keeps its name and means eaten-at |
| `time_source`, `time_phrase` | `sent`, NULL | `sent\|said\|forward\|edited`, plus the words |
| `numbers_basis` | `plate` | `items\|plate\|none` |
| `plate_totals` JSON | NULL | whole-plate numbers, written once at reading, never split |
| `deleted_at` | NULL | soft delete |
| `rev` | 0 | +1 on any change; a late model result applies only if `rev` still matches |

Index `(user_id, meal_time)` (H8 in the data map: none exists today).

**`record_events` (new).** `user_id, meal_id, food_id, kind, before JSON, after JSON, source_id, undoes_event_id`, timestamps. Kinds: `qty, grams, replace, add, remove, time, join, split, delete, restore, rename, numbers, food_merge, food_hide, reread, write, groups`.

**`personal_foods`, new columns.** `usual_grams`, `usual_grams_by_user`, `numbers_basis` (`per_100|per_portion|none`, backfill `per_100`), `numbers_quality` (`unverified|barcode|label|user`, backfill `unverified`, `barcode` where a barcode exists), `numbers_by_user` (default false). `upsert` must skip macros when `numbers_by_user`, because today it overwrites them (VERIFIED in the data map).

**Rules of the sum.**

- `numbers_basis='items'`: totals are the sum of live items' known numbers, in the same transaction as the item change.
- `numbers_basis='plate'`: totals are `plate_totals` plus the known numbers of items added later. Dish lines carry no numbers of their own.
- `numbers_basis='none'`: nothing was read, totals stay NULL.
- A legacy meal is never recomputed on open, because `_sum` turns NULL into 0. Its first `➕ Блюдо` writes `plate_totals` from its totals and one synthetic item under its description with `numbers_source='plate_share'`.
- Any item change also rewrites `food_items`, `food_groups`, `nutrients["foods"]` and `description` as new dicts, or the day card and the numbers disagree.
- `upsert` and `crud_meal.create` commit by themselves, so an item transaction uses neither. `crud_meal.create` and `MealUpdate` list fields by hand: add the new ones there.

**Readers change.** One filter, `deleted_at IS NULL AND is_draft = false`, goes into `get_user_meals_between`, `get_user_meals`, `get_multi`, `get_for_export` and the raw select in `food_groups_retag.pending`. `is_draft` arrives with M, so N's filter has only the first half. `crud_meal.get` splits into `get` and `get_any`. `day_only` meals drop out of `step_count._moments`, `early_clock` and `_long_gap`. Approximate meals do not feed `early_clock` or `_recent_clock`. The picture dict and its caption twin both need `≈` and `без времени`. `food_lists.seed` counts item names and skips empty descriptions. `food_groups_retag.pending` skips unread meals, or it pays for nothing.

**Analysis.**

- **Decision:** per-dish numbers need the same call to return `foods:[{name, grams, kcal, p, f, c, groups}]`, and that needs a priced eval under the 20-cent rule, so the owner decides when (section 9, item 1).
- N ships with the plate fallback: a meal with several foods gets one item per name and `numbers_basis='plate'`.
- A meal with one food gets `numbers_basis='items'`, because its plate is its dish.
- Once the schema change lands, a reply whose foods do not sum to its total within 10% (ASSUMED) still falls back to `plate`.
- Plate numbers are not written under one food's name in the base any more (H6).

### 3.2 Slice M

**`meal_sources` (new; no update path except the current `meal_id`).** `user_id, meal_id, origin_meal_id, kind (photo|document_photo|text|voice|favorite|picker), chat_id, message_id, media_group_id, reply_to_message_id, forward_date, sent_at, text, file_id, file_unique_id, duration_s, audio_path, supersedes_id`, timestamps. Unique on `(chat_id, message_id, kind)`, so a replayed update cannot make a second meal. `inbound_messages` stays a 60-day diagnostic log, because its rows are mutable and purged. Photo bytes are not stored, voice bytes are (R35 lists audio apart from the id). EXIF and location never.

**`meal_readings` (new).** `meal_id, source_ids JSON, kind (vision|text|transcript|exact_name), backend, model, prompt_version, output JSON, status, error, applied`, timestamps. No purge, because `ai_call_logs` expires at 60 days. Logs carry no transcript and no message body.

**`meal_messages` (new).** `chat_id, message_id, meal_id, view (pending|line|card|source|other)`, unique on `(chat_id, message_id)`. Holds the bot's line, a card opened from a list, and the person's own message, so a reply always finds what the person is looking at. Rewritten when a message is edited to another meal. `Screen` gains an optional `meal_id`.

**`meals`, new columns.** `reading_status` (`pending|read|failed|no_food|no_speech`, backfill `read`), `read_attempts` (default 0), `is_draft` (default false), `merged_into_id` FK meals. **`meal_items`, new column:** `source_id`.

**`User.settings`.** `draft_mode`, `capture_hint_seen`, `voice_hint_seen`, `rollout_seen`.

**Config.** `WHISPER_URL` (optional, empty goes straight to the paid fallback), `OPENAI_TRANSCRIBE_MODEL`, and a `usage_meter.PRICES` entry for it. The default is `http://host.docker.internal:9876` only in the deployment's environment, not in code. **Release note:** this is an optional env var, flag it anyway. `allowed_updates` gains `edited_message`.

**Retired.** States `ADDING_MEAL`, `ADDING_MEAL_TIME`, `CONFIRMING_MEAL`, the big keyboard, chips and typed `Да/Нет` of `meal_draft.py`, and with them the paid re-analysis of a menu label and the album DROP (both VERIFIED in the intake map). `/reprocess` now fills the meal (closes TD-010) and must not send a voice row's empty content to the text model.

### 3.3 Slice O

**`personal_foods`, new columns.** `aliases` JSON, `hidden_at`, `merged_into_id`. Hidden and merged rows are filtered in both the barcode and the ANN lookup, and in every list. Rename to a taken name is caught in Python (SQLite `lower()` is ASCII only, and Postgres raises IntegrityError).

**`favorite_meals`, new columns.** `personal_food_id`, `usual_grams`. Backfill: find or create a dish by name and leave its numbers alone. The favourite keeps its own numbers as a portion (`per_portion`), and nothing is converted to per-100 g by a guess. A favourite log's undo becomes a soft delete, so a deletion has one meaning.

**Times eaten.** Live items plus legacy meals whose whole description equals the name. `times_used` is not displayed, because it only counts write-backs. When legacy meals may be missed, the card says `Было 12 раз или больше: не все ранние записи здесь считаются.`

### 3.4 Export

US-N5 says a deleted meal does not count in the export, but the feed cannot say "deleted", and `since` on `meal_time` misses a back-dated meal (H1, H2 in the data map, consumer outside this repo, not read). **Decision:** add `updated_since` and `deleted_at` to the payload, leave `since` as it is, and set `updated_at` on every change including direct JSON writes. A row already pulled stays on the consumer's side until the consumer reads the new field (section 9, item 3).

## 4. Slice N: the meal, its dishes, its time

Bridge until M: the saved draft's footer gains `🍽 Приём` beside `📊 Карточка дня`, opening `N1` with `<b>=r`. Saving a draft writes items. M removes the bridge.

#### `N1` meal-card

The dish chips open the smallest unit first. **A one-dish meal skips this card:** `✏️ Исправить` on the line goes straight to `N2`, whose back still leads here.

```tg
<b>🍽 Приём, пн 26 октября, 13:20</b>
рис
курица · 2 порции
овощи · ½ порции
<i>📷 Оценка по фото</i>
<i>Нажми на блюдо, чтобы поправить.</i>
```

```kb
рис => ml:i:<iid>:<b> | курица => ml:i:<iid>:<b> | овощи => ml:i:<iid>:<b>
➕ Блюдо => ml:pk:<mid>:a:<b>:0 | 🕐 Время => ml:t:<mid>:<b> | 🗑 Удалить => ml:d:<mid>:<b>
🔢 Цифры => ml:n:<mid>:1:<b> | Ещё › => ml:mo:<mid>:<b>
⬅️ К ответу бота => ml:r:<mid>
```

- **Trigger:** `✏️ Исправить` on the line, a clock chip on Приёмы or Когда было, `🍽 Приём` on the bridge.
- **Text rules:** the date is always written, because «сегодня» turns false as the chat scrolls. `≈ 13:20` for an approximate time, `без времени` for day only. `<i>Отправлено: вт 27 октября, 9:10.</i>` appears only when sent differs from eaten by more than 15 minutes. The source line uses the shipped badges. A stored label (`Обед`) replaces the word `Приём` only when old data has one; nothing sets it any more.
- **Rows:**
  - R1 holds chips cut to 11 + `…`. With more than 3 dishes, R1 is two chips and `▶️` (`ml:op:<mid>:<pg>:<b>`), which cycles the pages. The text always lists every dish.
  - R3: `🔢 Цифры` is absent when the setting shows numbers. `Ещё ›` opens `N7`.
  - After a change, R3's third slot is `↩️ Вернуть` for one render.
- **Reads:** meal, live items, sources, settings. **Writes:** nothing on open.
- **Back:** by origin, section 2.4. **Stale:** S.
- **Hard day:** `N1h`. Never needs opening.

**`N1a`** Numbers open.

```tg
<b>🍽 Приём, пн 26 октября, 13:20</b>
рис
курица · 2 порции
овощи · ½ порции
<i>📷 Оценка по фото</i>
<i>Нажми на блюдо, чтобы поправить.</i>
<blockquote expandable>🔥 ≈ 620 ккал · Б 38 · Ж 19 · У 70
рис · ≈ 180 г · ≈ 240 ккал
курица · ≈ 240 г · ≈ 330 ккал
овощи · ≈ 60 г · ≈ 50 ккал</blockquote>
```

```kb
рис => ml:i:<iid>:<b> | курица => ml:i:<iid>:<b> | овощи => ml:i:<iid>:<b>
➕ Блюдо => ml:pk:<mid>:a:<b>:0 | 🕐 Время => ml:t:<mid>:<b> | 🗑 Удалить => ml:d:<mid>:<b>
✓ 🔢 Цифры => ml:n:<mid>:0:<b> | Ещё › => ml:mo:<mid>:<b>
⬅️ К ответу бота => ml:r:<mid>
```

**`N1b`** Legacy meal, no items (G5-R33). No model call and no recomputing on open. `➕ Блюдо` works through the synthetic item.

```tg
<b>🍽 Приём, пн 12 октября, 13:20</b>
суп с курицей и хлебом
Цифры на всю запись, не по блюдам.
```

```kb
➕ Блюдо => ml:pk:<mid>:a:<b>:0 | 🕐 Время => ml:t:<mid>:<b> | 🗑 Удалить => ml:d:<mid>:<b>
⭐ Весь приём => ml:fm:<mid>:1:<b> | 🔢 Цифры => ml:n:<mid>:1:<b>
⬅️ Приёмы дня => ml:dl:<day>:<b>
```

**`N1c`** Plate: several dishes, one set of numbers. Chips still open a dish, but its quantity row is replaced by a line (`N2a`).

```tg
<b>🍽 Приём, пн 26 октября, 13:20</b>
рис
курица
овощи
<i>📷 Оценка по фото</i>
Цифры посчитаны на всю тарелку.
<i>Нажми на блюдо, чтобы поправить.</i>
```

```kb
рис => ml:i:<iid>:<b> | курица => ml:i:<iid>:<b> | овощи => ml:i:<iid>:<b>
➕ Блюдо => ml:pk:<mid>:a:<b>:0 | 🕐 Время => ml:t:<mid>:<b> | 🗑 Удалить => ml:d:<mid>:<b>
🔢 Цифры => ml:n:<mid>:1:<b> | Ещё › => ml:mo:<mid>:<b>
⬅️ К ответу бота => ml:r:<mid>
```

**`N1d`** Unread: the read is pending or failed. It stays in the diary. Arrives with M, and only a saved meal without items reaches it before that.

```tg
<b>🍽 Приём, пн 26 октября, 13:20</b>
Фото сохранено, распознать еду пока не вышло.
В дневнике запись с этим фото.
```

```kb
🔁 Ещё раз => ml:rr:<mid>:<b> | ✏️ Написать => ml:w:<mid>:<b>
🕐 Время => ml:t:<mid>:<b> | 🗑 Удалить => ml:d:<mid>:<b>
⬅️ К ответу бота => ml:r:<mid>
```

**`N1h`** `минимум сил`: row 3 is hidden, so `Ещё ›` too. The numbers follow the setting, as on the day card.

```tg
<b>🍽 Приём, пн 26 октября, 23:40</b>
чипсы, три пачки
```

```kb
➕ Блюдо => ml:pk:<mid>:a:<b>:0 | 🕐 Время => ml:t:<mid>:<b> | 🗑 Удалить => ml:d:<mid>:<b>
⬅️ К ответу бота => ml:r:<mid>
```

#### `N2` meal-dish

```tg
<b>🍲 Курица</b>
В приёме пн 26 октября, 13:20 · 2 порции
<i>Оценка по фото</i>
Считается от первой записи: ½ — вдвое меньше, ×1 — столько же, ×2 — вдвое больше.
```

```kb
🔄 Заменить => ml:pk:<iid>:r:<b>:0 | 🗑 Из приёма => ml:x:<iid>:<b>
½ => ml:q:<iid>:h:<b> | ×1 => ml:q:<iid>:1:<b> | ✓ ×2 => ml:q:<iid>:2:<b>
⚖️ Граммы => ml:g:<iid>:<b> | ⭐ В избранное => ml:fi:<iid>:1:<b>
🔢 Цифры => ml:ni:<iid>:1:<b> | 🍲 Всё о блюде => dh:o:<fid>:<b>
⬅️ К приёму => ml:o:<mid>:<b>
```

- **Trigger:** a dish chip on `N1`, or `✏️ Исправить` on a one-dish line.
- **Source tag,** one of: `по штрих-коду (точно)`, `с этикетки`, `оценка по фото`, `оценка по тексту`, `из твоих блюд`, `из твоих блюд, не проверено`, `указано вручную`.
- **Quantity:** `½`, `×1`, `×2` set `factor` relative to what was recorded, and `✓` marks the current one. After a tap the notice is in words: `<i>✓ 2 порции</i>`, `<i>✓ ½ порции</i>`, `<i>✓ Как записано</i>`, never a bare `×2`. **Decision (US-N3 asks for `↩️ Вернуть`):** `×1` is the way back, a second control for the same act only adds reading. A typed weight shows no `✓`, and `×1` returns to the base. A second tap on the current factor gives toast `Уже так`.
- **`🍲 Всё о блюде`** appears with slice O, and only with a linked dish. `✓ ⭐ В избранном` with `:0` undoes, toast `Убрано из избранного`. The portion comes from this item (US-O5).
- **Reads:** item, meal. **Writes:** item, meal totals, the JSON fields, event, `rev`, in one transaction, no model.
- **Remove:** back to `N1` with `<i>✓ Убрано: курица</i>` and `↩️ Вернуть`. The last dish goes to `N6`.
- **Back:** `⬅️ К приёму`, also when `N1` was skipped. **Stale:** S.
- **Hard day:** `N2h`.

**`N2a`** Plate: the dish has no numbers of its own, so no quantity. After a replace or a removal the notice says `<i>✓ Убрано: курица. Цифры посчитаны на всю тарелку, поэтому не менялись.</i>`.

```tg
<b>🍲 Курица</b>
В приёме пн 26 октября, 13:20
Цифры посчитаны на всю тарелку.
```

```kb
🔄 Заменить => ml:pk:<iid>:r:<b>:0 | 🗑 Из приёма => ml:x:<iid>:<b>
⚖️ Граммы => ml:g:<iid>:<b> | ⭐ В избранное => ml:fi:<iid>:1:<b>
⬅️ К приёму => ml:o:<mid>:<b>
```

**`N2b`** A dish the model could not count. Totals skip it, and open numbers say `Не посчитано блюд: 1`.

```tg
<b>🍲 Хлеб</b>
В приёме пн 26 октября, 13:20
Цифры не посчитаны.
```

```kb
🔄 Заменить => ml:pk:<iid>:r:<b>:0 | 🗑 Из приёма => ml:x:<iid>:<b>
⚖️ Граммы => ml:g:<iid>:<b> | ⭐ В избранное => ml:fi:<iid>:1:<b>
⬅️ К приёму => ml:o:<mid>:<b>
```

**`N2h`** `минимум сил`: quantity and removal only.

```tg
<b>🍲 Чипсы</b>
В приёме пн 26 октября, 23:40 · три пачки
Считается от первой записи: ½ — вдвое меньше, ×1 — столько же, ×2 — вдвое больше.
```

```kb
½ => ml:q:<iid>:h:<b> | ✓ ×1 => ml:q:<iid>:1:<b> | ×2 => ml:q:<iid>:2:<b>
🗑 Из приёма => ml:x:<iid>:<b>
⬅️ К приёму => ml:o:<mid>:<b>
```

#### `N3` meal-grams

```tg
<b>⚖️ Курица</b>
Напиши граммы числом, например 150.
```

```kb
⬅️ К блюду в приёме => ml:i:<iid>:<b>
```

- **Trigger:** `⚖️ Граммы`, ask `grams`.
- **Input:** a number from 1 to 5000 with an optional unit: `г`, `гр`, `грамм`, `граммов`, `g`, `gr`, a closing dot allowed. Inside this slot a bare `150` is grams, outside it is a weigh-in. Anything else closes the slot and goes down the router.
- **Result:** `N2`, sent as a new message with the old keyboard stripped, and `<i>✓ 150 г</i>`, `↩️ Вернуть`. With `base_grams`, factor = typed / base. Without it, but with per-100 g numbers on the dish, numbers come from the dish. With neither, grams are stored and the notice is `<i>✓ 150 г, цифры не менялись</i>`.
- **Reads:** item, dish. **Writes:** `grams`, `factor`, totals, event. **Back:** `⬅️ К блюду в приёме`. **Stale:** S. **Hard day:** not on the path, same screen.

#### `N4` dish-picker

One screen for add and replace (US-N4). The slot opens at once.

```tg
<b>➕ Блюдо в приём пн 26 октября, 13:20</b>
Нажми или напиши название.
```

```kb
⭐ овсянка => ml:pa:<mid>:<fid>:<b> | хлеб => ml:pa:<mid>:<fid>:<b>
⭐ омлет => ml:pa:<mid>:<fid>:<b> | сыр => ml:pa:<mid>:<fid>:<b>
◀️ => ml:pk:<mid>:a:<b>:<pg> | 1/3 => ml:nop | ▶️ => ml:pk:<mid>:a:<b>:<pg>
⬅️ К приёму => ml:o:<mid>:<b>
```

- **Trigger:** `➕ Блюдо`, ask `dish_add`.
- **Tap:** adds at the dish's usual portion, else its last one, and copies the numbers (`numbers_source='dish'`). No model, free.
- **Typed name:** whole name or alias, compared in Python: free and instant. Unknown: the item appears at once with `numbers_source='none'`, a new dish without a question, and the model fills the numbers in the background with no new message, marked `оценка по тексту`. If the model fails, the name stays with `Цифры не посчитаны.` (`N2b`).
- **Reads:** dishes, ⭐ first, then recent, not hidden, not merged. **Writes:** item, totals, dish, event.
- **Back:** `⬅️ К приёму`. **Stale:** S; a gone dish redraws with `<i>Этого блюда уже нет в списке.</i>`.
- **Hard day:** same; a word in reply does the same with no screen.
- **Empty base:** the second line and back only.

**`N4a`** Replace. Ask `dish_replace`, back `⬅️ К блюду в приёме`. Replace keeps grams and `amount_text`.

```tg
<b>🔄 Вместо «курица»</b>
Нажми или напиши название.
```

```kb
⭐ индейка => ml:pr:<iid>:<fid>:<b> | рыба => ml:pr:<iid>:<fid>:<b>
◀️ => ml:pk:<iid>:r:<b>:<pg> | 1/3 => ml:nop | ▶️ => ml:pk:<iid>:r:<b>:<pg>
⬅️ К блюду в приёме => ml:i:<iid>:<b>
```

**`N4b`** Result, on the card. The same for `<i>✓ Добавлено: хлеб</i>`.

```tg
<i>✓ Заменено: курица → индейка</i>

<b>🍽 Приём, пн 26 октября, 13:20</b>
рис
индейка · 2 порции
овощи · ½ порции
<i>📷 Оценка по фото</i>
<i>Нажми на блюдо, чтобы поправить.</i>
```

```kb
рис => ml:i:<iid>:<b> | индейка => ml:i:<iid>:<b> | овощи => ml:i:<iid>:<b>
➕ Блюдо => ml:pk:<mid>:a:<b>:0 | 🕐 Время => ml:t:<mid>:<b> | 🗑 Удалить => ml:d:<mid>:<b>
🔢 Цифры => ml:n:<mid>:1:<b> | Ещё › => ml:mo:<mid>:<b> | ↩️ Вернуть => ml:u:<eid>:<b>
⬅️ К ответу бота => ml:r:<mid>
```

#### `N5` meal-time

```tg
<b>🕐 Время приёма</b>
Записано на: пн 26 октября, 13:20.
Другое время можно написать, например 12:40 или вчера 23:10.
```

```kb
−15 мин => ml:ts:<mid>:<ts>:e:<b> | −1 ч => ml:ts:<mid>:<ts>:e:<b> | Вчера, 30 окт => ml:ts:<mid>:<ts>:a:<b>
Не помню => ml:ts:<mid>:<ts>:d:<b>
⬅️ К приёму => ml:o:<mid>:<b>
```

- **Trigger:** `🕐 Время` on the line or the card, ask `time`.
- **Chips:** targets are absolute. `Вчера, 30 окт` is the same clock on yesterday's local date (not −24 h), counted from today and not from the meal, marked approximate because it is a guess. The chip writes the date, because a meal of Monday opened on Saturday has its own «вчера» in the reader's head (section 9, item 47). `Не помню` sets `day_only`: it leaves the day's line and stays on Приёмы as `без времени`, sorted at 12:00.
- **Decision:** the chips are the story's. Research favours `Вчера вечером`, and no decision replaces US-N6 (section 9, item 5).
- **Typed:** `13:20`, `13.20` keep the meal's day, `вчера 23:10` moves it. A time still in the future: `<i>Это время ещё не наступило. Напиши другое.</i>` and the slot stays. Anything else closes the slot and goes down the router.
- **Writes:** `meal_time` through `astimezone(UTC)`, `time_precision`, `time_source='edited'`, event, `rev`. `sent_at` never changes. Day, week and steps recompute on open. A week message already sent stays as it is.
- **Reads:** meal, zone. **Back:** `⬅️ К ответу бота` from the line, else `⬅️ К приёму`. **Stale:** S.
- **Hard day:** same screen; no question ever asks for a time, and nothing marks a late entry.

**`N5a`** Result, same day.

```tg
<i>✓ Время: 13:05</i>

<b>🕐 Время приёма</b>
Записано на: пн 26 октября, 13:05.
Другое время можно написать, например 12:40 или вчера 23:10.
```

```kb
−15 мин => ml:ts:<mid>:<ts>:e:<b> | −1 ч => ml:ts:<mid>:<ts>:e:<b> | Вчера, 30 окт => ml:ts:<mid>:<ts>:a:<b>
Не помню => ml:ts:<mid>:<ts>:d:<b> | ↩️ Вернуть => ml:u:<eid>:<b>
⬅️ К приёму => ml:o:<mid>:<b>
```

**`N5b`** Result of typing `вчера 23:10` on Saturday, on the meal of Monday: it lands on another day, and the sentence names that day.

```tg
<i>✓ Время: пт 30 октября, 23:10. Приём теперь в этом дне.</i>

<b>🕐 Время приёма</b>
Записано на: пт 30 октября, 23:10.
Другое время можно написать, например 12:40 или вчера 23:10.
```

```kb
−15 мин => ml:ts:<mid>:<ts>:e:<b> | −1 ч => ml:ts:<mid>:<ts>:e:<b> | Вчера, 30 окт => ml:ts:<mid>:<ts>:a:<b>
Не помню => ml:ts:<mid>:<ts>:d:<b> | ↩️ Вернуть => ml:u:<eid>:<b>
⬅️ К приёму => ml:o:<mid>:<b>
```

**`N5d`** Result of the `Вчера, 30 окт` chip on Saturday: the same clock on yesterday's date, shown with `≈`, because the clock is a guess. A second tap gives toast `Уже так`.

```tg
<i>✓ Время примерное: пт 30 октября, ≈ 13:20. Приём теперь в этом дне.</i>

<b>🕐 Время приёма</b>
Записано на: пт 30 октября, ≈ 13:20.
Другое время можно написать, например 12:40 или вчера 23:10.
```

```kb
−15 мин => ml:ts:<mid>:<ts>:e:<b> | −1 ч => ml:ts:<mid>:<ts>:e:<b> | Вчера, 30 окт => ml:ts:<mid>:<ts>:a:<b>
Не помню => ml:ts:<mid>:<ts>:d:<b> | ↩️ Вернуть => ml:u:<eid>:<b>
⬅️ К приёму => ml:o:<mid>:<b>
```

**`N5c`** Result of `Не помню`. `−15 мин` and `−1 ч` go, because there is no clock left to count from; typing a time still works.

```tg
<i>✓ Время не указано. Приём остаётся в пн 26 октября, без времени.</i>

<b>🕐 Время приёма</b>
Записано на: пн 26 октября, без времени.
Другое время можно написать, например 12:40 или вчера 23:10.
```

```kb
Вчера, 30 окт => ml:ts:<mid>:<ts>:y:<b> | ↩️ Вернуть => ml:u:<eid>:<b>
⬅️ К приёму => ml:o:<mid>:<b>
```

`Вчера, 30 окт` here moves the day and keeps `day_only` (rule 7): no clock was given, so none is shown. Its result is this screen with the day changed and the notice `<i>✓ Время не указано. Приём теперь в пт 30 октября, без времени.</i>`.

#### `N6` meal-removed

```tg
<b>🗑 Приём удалён</b>
Ср 28 октября, 13:20: рис, курица и овощи.
Вернуть можно и позже, пока эта кнопка на экране.
```

```kb
↩️ Вернуть => ml:u:<eid>:<b>
⬅️ Приёмы дня => ml:dl:<day>:<b>
```

- **Trigger:** `🗑 Удалить`, or removing the last dish. Applied at once, no confirmation and no reason asked.
- **Writes:** `meals.deleted_at`, event. Items and totals stay, so undo restores exactly. No time limit on undo.
- **Soft delete:** the record stays stored. The explanation says so plainly (`M15`), and real erasure is left for R35's "delete media" later.
- **Reads:** meal. **Back:** `⬅️ Приёмы дня`; from `r`, `📊 Карточка дня` (`st:t`), because the line no longer describes anything.
- **Stale:** a second tap on undo opens the restored card. A second tap on `🗑 Удалить` or `🗑 Из приёма`, or on the undo that deleted it again, is this screen again with the same `↩️ Вернуть` and no new event. A tap on any other button of a deleted meal, or a deleted meal with no event to return to, is `N6a`.
- **Hard day:** same, quiet.

**`N6a`** A tap on a deleted meal's button.

```tg
<i>Этого приёма уже нет, вот сегодняшний день.</i>

<b>📊 Сегодня, сб 31 октября</b>
8:40 · 13:20
```

#### `N7` meal-more

What the card keeps out of the way: the rare actions, one tap further. **Decision:** this replaces rows 3 and 4 of the old card, see section 9, fresh eyes.

```tg
<b>🍽 Ещё: приём пн 26 октября, 13:20</b>
рис, курица и овощи
```

```kb
⭐ Весь приём => ml:fm:<mid>:1:<b> | 🔗 Объединить => ml:jl:<mid>:<b>
📋 Прислано => ml:src:<mid>:<b> | 🏷 Группы => ml:gr:<mid>:<b>
⬅️ К приёму => ml:o:<mid>:<b>
```

- **Trigger:** `Ещё ›` on `N1`.
- **Rows:** `⭐ Весь приём` only with 2+ dishes, state `✓ ⭐ Весь приём` with `:0`, and the text gains `Весь приём в избранном.` `🔗 Объединить` becomes `➖ Отделить` (`ml:z:<sid>:<b>`) when the meal has 2+ components, and is hidden when no other meal lies within 24 hours. Row 2 and `🔗 Объединить` arrive with M.
- **Reads:** meal, sources. **Writes:** nothing on open. **Back:** `⬅️ К приёму`. **Stale:** S.
- **Hard day:** not reachable at `минимум сил`.

## 5. Slice M: capture

### 5.1 The router

One handler for text and media, registered outside the conversation, private chats only. First match wins. A non-subscriber gets today's lock and no capture.

| # | Message | Result |
|---|---|---|
| 1 | Command, menu label | as today |
| 2 | Open `ask`, and the message fits it | the answer; otherwise the slot closes and this goes on |
| 3 | Stale reply-keyboard label `Сейчас`, `Завтрак`, `Обед`, `Ужин`, `Назад`, `Отмена` | never a meal, `M9b` |
| 4 | Reply to a message in `meal_messages` | `M5`; a bare number means grams, a bare time means time |
| 5 | Bare number matching `KG_PATTERN` | weigh-in, as today |
| 6 | Exact routine name on today's card | tick, as today |
| 7 | Bare clock with a colon, `13:20`, or `вчера 23:10` | `M5e` on the last meal |
| 8 | Corrective words at the start, meal within 3 h (ASSUMED) | `M6` |
| 9 | Stop list `привет`, `спасибо`, `ок`, `окей`, `да`, `нет`, `пока` | no reply, no meal, source kept |
| 10 | Text, photo, image file, voice | a new meal, `M1`–`M4` |
| 11 | Anything else | `M9`, one line |

Weight goes before time on purpose, so `13.20` outside a slot stays a weigh-in; only the time slot reads it as a clock. A forward uses `forward_origin.date` as an approximate time. `message.date` is used for `sent_at`, so messages handled after downtime keep their own time.

### 5.2 Timeline of one capture

1. Commit source and meal with `reading_status='pending'`. Failure: `M7f`, the one case where the person must resend.
2. Send the line at once as a silent reply: `disable_notification=True`, `reply_parameters` to the person's message. Store the mapping. It is true from the first moment, so no 0.3 s loader and no flash.
3. Read in a background task.
4. Edit the result into the same line. An edit makes no second push (ASSUMED from Telegram's behaviour, check on a locked phone).
5. A late result applies only if `rev` matches, and edits the message only if its `view` is still `pending`. Otherwise the reading is stored with `applied=false` and nothing moves on screen.

**Background execution, decided: an in-process task with replay from the database, not Celery.**

- The handler calls `application.create_task` and returns, so `max_concurrent_updates=1` no longer makes one person's read hold everyone (VERIFIED in the intake map: updates run in turn, and one web search can take 30 s).
- A small asyncio loop started in `post_init` is the sweep, because `job_queue` is not installed (VERIFIED in the UI map's facts). At startup it replays `pending` meals from `file_id`. Every 5 minutes it marks `pending` older than 5 minutes with no live task as `failed` and edits the line, so no line stays on «читаю».
- Why not Celery (INFERRED): the read needs the live bot to edit a message, the OpenAI service is async, and the meter is built for this process. Celery would add a second Telegram client and a sync bridge. Restart safety comes from the database row, not from the broker, so a broker adds no guarantee here.
- Limits: at most 3 reads at once (semaphore), each with the existing timeouts. Each task opens its own usage meter, because the handler's context ends when the handler returns.
- Automatic retries: temporary failures only, at 5 minutes, 1 hour and 6 hours, at most 3 per meal within 24 hours, at most 5 per sweep, none while the balance is out, counted in `read_attempts`. A photo read is 2+ paid calls, hence the cap. All values are ASSUMED. A success edits the line with no push. Voice retries are local only.

### 5.3 Time words

Rule-based, no model, word boundaries so «вчерашний суп» stays food. The phrase leaves the dish name and stays in `time_phrase`. `time_source='said'`. All clocks ASSUMED.

| Phrase | Eaten at | Precision |
|---|---|---|
| `13:20`, `в 13`, `в 13:20` | that clock today, or yesterday if it would be in the future | exact |
| `час назад`, `полчаса назад` | sent minus that | approx |
| `вчера` alone | yesterday, sorted at 12:00 | day_only |
| `вчера вечером`, `вчера днём`, `вчера утром` | yesterday at 20:00, 14:00, 09:00 | approx |
| `ночью` | the latest 03:00 before sent | approx |
| `утром`, `днём`, `вечером` | 09:00, 14:00, 20:00 today, or yesterday if in the future | approx |
| nothing | `sent_at` | exact, `time_source='sent'` |

### 5.4 Corrective words

One constant, free regex, start of the text only, last live meal sent under 3 hours ago. Anything else is a new meal with the join row, and the miss is logged without the text. The line always names the meal, so a wrong target is visible.

| Words | Action | Line |
|---|---|---|
| `ещё X`, `и ещё X`, `а ещё X`, `плюс X`, `добавь X`, and the "forgot" forms of the verb, all genders | born meal, joined | `Добавлено к приёму пн 26 октября, 13:20: сыр.` |
| `ещё порция`, `добавка`, `вторая порция`, `ещё одна` | second helping: a new item of the same dish, both captures kept | `Добавлено к приёму пн 26 октября, 13:20: суп.` |
| `это был X`, `это была X`, `это было X`, `это были X`, `не X, а Y`, `нет, X` | replace; one dish or a stem match directly, else `M6a` | `Заменено в приёме пн 26 октября, 13:20: курица → индейка.` |
| `убери X`, `убрать X` | remove on a stem match, else `M6a` | `Убрано из приёма пн 26 октября, 13:20: сыр.` |

**Decision:** a second helping reads «Добавлено», because a separate «Ещё порция» counts at the person on a hard day. The card simply shows `суп` twice. `×2` stays a correction.

### 5.5 Screens

#### `M1` capture-pending

```tg
Фото в дневнике, читаю…
```

- **Trigger:** photo or image file, nothing else matched in the router. Silent, reply to the person's message.
- **Reads:** nothing yet. **Writes:** source, meal, `meal_messages`.
- **Back:** none. **Stale:** none; a reply to this message already works.
- **Hard day:** same. No buttons, nothing to answer.

**`M1a`** The line (R35). Edited in.

```tg
Записано: рис, курица и овощи.
```

```kb
✏️ Исправить => ml:o:<mid>:r | 🕐 Время => ml:t:<mid>:r
```

- With one dish, `✏️ Исправить` is `ml:i:<iid>:r`.
- Names join up to 300 characters, then `…`; all of them sit on the card.
- **Reads:** dishes by name or barcode, previous meal, settings. **Writes:** items, reading, dish links, status.
- **Stale:** buttons live indefinitely; deleted is S.
- **Decision:** the two buttons stay at every energy level. R35 names both, and a line that changes by mode is one more thing to learn.

**`M1b`** First capture ever, once (`capture_hint_seen`).

```tg
Записано: рис, курица и овощи.
На этом всё. Поправить можно кнопками или ответом на это сообщение.
```

```kb
✏️ Исправить => ml:o:<mid>:r | 🕐 Время => ml:t:<mid>:r
```

**`M1c`** Join row. Appears when the person's previous live meal was sent less than 90 minutes ago (ASSUMED) and no relation fired. It shows a button and joins nothing. It carries both ids, so a week later it joins those two, never "the latest".

```tg
Записано: сыр.
```

```kb
✏️ Исправить => ml:i:<iid>:r | 🕐 Время => ml:t:<mid>:r
🔗 Объединить с пн 26 окт, 13:20 => ml:j:<mid>:<mid>:r
```

**`M1d`** `🔢 Цифры сразу` on. The owner's cost line stays a separate second line.

```tg
Записано: рис, курица и овощи · ≈ 620 ккал.
```

```kb
✏️ Исправить => ml:o:<mid>:r | 🕐 Время => ml:t:<mid>:r
```

**`M1e`** Time named by the words.

```tg
Записано на чт 29 октября, ≈ 20:00: суп.
```

```kb
✏️ Исправить => ml:i:<iid>:r | 🕐 Время => ml:t:<mid>:r
```

**`M1f`** Bare «вчера».

```tg
Записано на чт 29 октября, без времени: суп.
```

```kb
✏️ Исправить => ml:i:<iid>:r | 🕐 Время => ml:t:<mid>:r
```

**`M1g`** Forward.

```tg
Записано на вт 27 октября, ≈ 14:02 (время пересланного сообщения): суп.
```

```kb
✏️ Исправить => ml:i:<iid>:r | 🕐 Время => ml:t:<mid>:r
```

**`M1h`** Hard day, the whole product. Twelve messages at night are twelve lines, with no count.

```tg
Записано: чипсы.
```

```kb
✏️ Исправить => ml:i:<iid>:r | 🕐 Время => ml:t:<mid>:r
```

**`M1h2`** Numbers on, a large number, no comment and no colour.

```tg
Записано: чипсы, три пачки · ≈ 3000 ккал.
```

```kb
✏️ Исправить => ml:i:<iid>:r | 🕐 Время => ml:t:<mid>:r
```

**`M1h3`** The next morning.

```tg
Записано на пт 30 октября, ≈ 3:00: чипсы.
```

```kb
✏️ Исправить => ml:i:<iid>:r | 🕐 Время => ml:t:<mid>:r
```

**`M1h4`** The same meal sent again by mistake: no dedupe guess, the join row does the work and `🗑` is on the card.

```tg
Записано: чипсы.
```

```kb
✏️ Исправить => ml:i:<iid>:r | 🕐 Время => ml:t:<mid>:r
🔗 Объединить с чт 29 окт, 23:40 => ml:j:<mid>:<mid>:r
```

#### `M2` album-pending

```tg
3 фото в дневнике, читаю…
```

- **Trigger:** photos with one `media_group_id`. Each writes its source on arrival, and the first creates the meal, found by the database rather than memory. The read starts 2 s after the last item (ASSUMED), up to 10 photos in one call, any caption counts. A straggler joins by id and re-reads, editing the same line.
- **Reads:** sources by album. **Writes:** sources, one meal. **Back, stale:** as `M1`.
- **Hard day:** a whole bad day can go in one album. No remark.
- Real albums were not run while designing: check on a phone.

**`M2a`** Result. No split button on the line.

```tg
Записано по 3 фото: суп, хлеб и йогурт.
```

```kb
✏️ Исправить => ml:o:<mid>:r | 🕐 Время => ml:t:<mid>:r
```

**`M2b`** Split by photo, from `📋 Прислано` only. The one question in this design, because it costs three paid reads. Old items are retired and restorable with `↩️ Вернуть`.

```tg
<b>➖ Разделить 3 фото на отдельные приёмы?</b>
Каждое фото будет распознано заново, отдельно от других. Вернуть как было можно кнопкой «↩️ Вернуть».
```

```kb
➖ Разделить => ml:za:<mid>
⬅️ К приёму => ml:o:<mid>:<b>
```

#### `M3` text-line

The person's own words appear at once, with no loader. The model splits them behind the scenes, and the line is edited silently only when the names differ.

```tg
Записано: кофе и печенье.
```

```kb
✏️ Исправить => ml:o:<mid>:r | 🕐 Время => ml:t:<mid>:r
```

- **Trigger:** text that reaches row 10 of the router.
- **Free match:** the whole text, or every comma-separated part, equals a dish or alias: no model call and final at once (US-N1). Numbers from an `unverified` row are copied and tagged `из твоих блюд, не проверено`, and a plate row is never served as numbers (H6).
- **Reads:** dishes. **Writes:** source, meal, items, reading. **Back:** none. **Stale:** as `M1a`.
- **Hard day:** same; a word costs less than a photo.

**`M3a`** The model reads no food. No meal, source kept. `🍽 Это еда` reads it as food at `sent_at`.

```tg
Еды здесь не видно, в дневник не записано.
```

```kb
🍽 Это еда => ml:nf:<sid>
```

**`M3b`** A question, by the same read or by a final `?`.

```tg
Вопросы здесь пока не разбираются, в дневник не записано.
```

```kb
🍽 Это еда => ml:nf:<sid>
```

#### `M4` voice-pending

```tg
Голосовое в дневнике, слушаю…
```

- **Trigger:** `voice`. Audio saved first, then a background task: `WHISPER_URL` (the shared local-whisper service, `/v1/audio/transcriptions`), then the paid fallback when it is unreachable, refuses or runs out of time. Empty `WHISPER_URL` goes straight to the fallback.
- **Decision:** `/v1` rather than R32's `/jobs/upload`, which stalls during a GPU claim (from the voice map, not re-checked here). The screens do not change either way (section 9, item 2).
- **Facts for the build:** Telegram voice is OGG and the upload's extension must match the bytes. No speech is a 422 and takes no paid retry. Client timeout about 120 s for the local path (ASSUMED), 30 s and `max_retries=0` for the paid one. Plan on 5–15 s, up to a minute cold.
- **Logged per note, no text:** backend, duration, later correction pairs, for R35's Russian measure.
- **Reads:** settings. **Writes:** source with `audio_path`, meal, reading with transcript. **Back:** none. **Stale:** as `M1`.
- **Hard day:** the lowest-effort input, nothing about length or mumbling.

**`M4a`** Result, with the prompt rule "the last correction in a sentence wins".

```tg
Записано: два яйца, хлеб и кофе.
```

```kb
🎙 Текст => ml:vt:<mid>:1 | ✏️ Исправить => ml:o:<mid>:r | 🕐 Время => ml:t:<mid>:r
```

**`M4b`** Transcript open, in place, not a new screen.

```tg
Записано: два яйца, хлеб и кофе.
<blockquote expandable>Два яйца, один… нет, хлеб и кофе.</blockquote>
```

```kb
✓ 🎙 Текст => ml:vt:<mid>:0 | ✏️ Исправить => ml:o:<mid>:r | 🕐 Время => ml:t:<mid>:r
```

**`M4c`** First voice ever, once (`voice_hint_seen`).

```tg
Записано: два яйца, хлеб и кофе.
Голосовые пока в пробном режиме, но запись уже в дневнике. Что услышано, видно по кнопке «🎙 Текст». Если сервис проекта не отвечает, голосовое уходит во внешний сервис.
```

```kb
🎙 Текст => ml:vt:<mid>:1 | ✏️ Исправить => ml:o:<mid>:r | 🕐 Время => ml:t:<mid>:r
```

**`M4d`** Both services failed. The voice stays as the source and is read again later (R32).

```tg
Голосовое сохранено, разобрать сейчас не вышло.
В дневнике запись с этим голосовым.
```

```kb
🔁 Ещё раз => ml:rr:<mid>:r | ✏️ Написать => ml:w:<mid>:r
```

**`M4e`** No speech. Source kept, no meal in the day.

```tg
В голосовом не слышно речи, в дневник не записано.
```

```kb
✏️ Написать => ml:w:<mid>:r
```

**`M4f`** Over 20 MB. No buttons, and no meal.

```tg
Файл больше 20 МБ, Telegram такие боту не передаёт. В дневник не записано.
```

#### `M5` reply-add

A new message answering the reply. The original line is edited silently to the new list, best effort. Every message is born as its own meal first, then joined, which is why `➖ Отделить` always works.

```tg
Добавлено к приёму пн 26 октября, 13:20: оливковое масло.
```

```kb
➖ Отделить => ml:z:<sid>:r | ✏️ Исправить => ml:o:<mid>:r
```

- **Trigger:** a reply, to the person's message, the bot's line or a card, carrying food text, a photo or a voice note.
- **Reads:** `meal_messages`, meal. **Writes:** source, born meal, then join (items move, `merged_into_id` and `deleted_at` on the born meal, its time kept on its own row), event.
- **Undo:** `➖ Отделить`, not `↩️ Вернуть`: what was sent stays a meal of its own (`M8a`), and that screen offers `🗑 Удалить` when it should go altogether.
- **Back:** none. **Stale:** `M5a`.
- **Hard day:** replying is optional; a split loses nothing.

**`M5a`** Target already deleted.

```tg
Записано: суп. Тот приём был удалён раньше, поэтому это отдельная запись.
```

```kb
✏️ Исправить => ml:i:<iid>:r | 🕐 Время => ml:t:<mid>:r
```

**`M5b`** Replacement in a reply.

```tg
Заменено в приёме пн 26 октября, 13:20: курица → индейка.
```

```kb
↩️ Вернуть => ml:u:<eid>:r | ✏️ Исправить => ml:o:<mid>:r
```

**`M5c`** Bare grams, one dish.

```tg
Рис в приёме пн 26 октября, 13:20: 200 г.
```

```kb
↩️ Вернуть => ml:u:<eid>:r | ✏️ Исправить => ml:o:<mid>:r
```

**`M5d`** Bare grams, several dishes. A chip applies the typed grams at once and gives `M5c`, so the number is never asked for twice.

```tg
<b>200 г: у какого блюда?</b>
Можно не отвечать, ничего не изменится.
Приём пн 26 октября, 13:20: рис, курица и овощи.
```

```kb
рис => ml:ga:<iid>:200:r | курица => ml:ga:<iid>:200:r | овощи => ml:ga:<iid>:200:r
⬅️ К ответу бота => ml:r:<mid>
```

**`M5e`** Bare time. Target: the replied meal, else the open `N5`, else the last meal within 24 hours (ASSUMED). The day and the dishes are named, because the target may be a guess. No wait state: a clock is never food.

```tg
Время приёма теперь пн 26 октября, 13:20: рис, курица и овощи.
```

```kb
↩️ Вернуть => ml:u:<eid>:r | ✏️ Исправить => ml:o:<mid>:r
```

**`M5f`** Bare time, no meal within 24 hours. Nothing written.

```tg
Похоже на время, но не ясно, к какому приёму. В дневнике ничего не поменялось. Время можно указать ответом на нужную запись.
```

#### `M6` corrective-line

Same line and buttons as `M5` and `M5b`, target from section 5.4. Removal below; undo is `↩️ Вернуть`.

```tg
Убрано из приёма пн 26 октября, 13:20: сыр.
```

```kb
↩️ Вернуть => ml:u:<eid>:r | ✏️ Исправить => ml:o:<mid>:r
```

- **Trigger:** corrective words as the router's row 8, or inside a reply.
- **Reads:** last live meal. **Writes:** source, item, totals, event. **Back:** none. **Stale:** the meal gone means a new meal, `M5a`.
- **Hard day:** same.

**`M6a`** Which dish. Stem match first, this only if still ambiguous. Nothing is applied yet and the source is saved. Ignoring it changes nothing. For a second helping, the title is `<b>Какое блюдо добавить ещё раз?</b>` and one more button is `🍽 Весь приём` (`ml:kw:<sid>`).

```tg
<b>Какое блюдо это меняет?</b>
Можно не отвечать, ничего не изменится.
«это была индейка», приём пн 26 октября, 13:20
```

```kb
рис => ml:ka:<sid>:<iid> | курица => ml:ka:<sid>:<iid>
овощи => ml:ka:<sid>:<iid>
➕ Отдельный приём => ml:kn:<sid>
```

**Decision:** the chooser rather than the model's pick. INFERRED that R35's "asks nothing" is about the capture reply, and a model's silent replace would break "a wrong merge costs more" (section 9, item 7).

#### `M7` read-failed

R35's sentence stands as written, and the second line says what it leaves out (section 9, item 44). One sentence for rate limit, parse error, model gone, balance out and failed download. Owner alerts stay separate.

```tg
Фото сохранено, распознать еду сейчас не вышло.
В дневнике запись с этим фото.
```

```kb
🔁 Ещё раз => ml:rr:<mid>:r | ✏️ Написать => ml:w:<mid>:r
```

- **Trigger:** the read failed, or the sweep marked it so.
- **In the day:** the time on the line, `13:20 · 📷 фото сохранено` on Приёмы, and `Не посчитано приёмов: 1` under numbers, so a day of one failed photo never reads as 0 ккал.
- **`🔁 Ещё раз`:** toast `Читаю`, then the line is edited; a repeat failure gives toast `Пока не вышло, запись сохранена`.
- **Reads:** meal, source. **Writes:** `reading_status`, `read_attempts`, reading.
- **Back:** none. **Stale:** S.
- **Hard day:** nothing to do. The retry comes by itself, and the copy holds no demand.

**`M7a`** Text failed.

```tg
Текст сохранён, разобрать еду сейчас не вышло.
В дневнике запись с этим текстом.
```

```kb
🔁 Ещё раз => ml:rr:<mid>:r | ✏️ Написать иначе => ml:w:<mid>:r
```

**`M7b`** `✏️ Написать`, ask `meal_text`. Edits the same message. The text becomes this meal's reading and the photo stays.

```tg
<b>✏️ Что было?</b>
Напиши одним сообщением.
```

```kb
⬅️ К ответу бота => ml:r:<mid>
```

**`M7c`** Result of writing.

```tg
Записано: суп. Фото хранится при этой записи.
```

```kb
✏️ Исправить => ml:i:<iid>:r | 🕐 Время => ml:t:<mid>:r
```

**`M7d`** The photo shows no food. It stays, because the person sent it to a food diary.

```tg
Фото сохранено, еды на нём не видно.
В дневнике запись с этим фото.
```

```kb
✏️ Написать => ml:w:<mid>:r | 🗑 Удалить => ml:d:<mid>:r
```

**`M7e`** Dead file id on retry.

```tg
Фото пн 26 октября, 13:20 больше недоступно в Telegram. Запись в дневнике осталась, можно написать, что было.
```

```kb
✏️ Написать => ml:w:<mid>:r
```

**`M7f`** The save itself failed. The only line without a row behind it.

```tg
Записать сейчас не вышло, сервер не отвечает. Пришли ещё раз.
```

#### `M8` join-result

From the join row, the same message, at once. The earlier meal survives. The later one gets `merged_into_id`, with its time kept on its own row.

```tg
Объединено с приёмом пн 26 октября, 13:20: рис, курица, овощи и сыр.
```

```kb
➖ Отделить => ml:z:<sid>:r | ✏️ Исправить => ml:o:<mid>:r
```

- **Trigger:** the join row, or a clock on `M8b`. A pair already joined: toast `Уже вместе`.
- **Reads:** both meals. **Writes:** items, totals of both, event.
- **Back:** none. **Stale:** target gone, the card with `<i>Того приёма уже нет.</i>`.
- **Hard day:** nothing offers a merge at a distance; ignoring it is fine.

**`M8a`** Separated, from any later moment. Identities and time return. **Decision:** typed fixes stay, because a correction is the person's knowledge (the event's `before` still holds the older values; section 9, item 6). Nothing joined: toast `Уже отдельно`.

```tg
Отделено: сыр. Теперь это отдельный приём, пн 26 октября, 13:40.
```

```kb
🔗 Объединить с пн 26 окт, 13:20 => ml:j:<mid>:<mid>:r
✏️ Исправить => ml:i:<iid>:r | 🗑 Удалить => ml:d:<mid>:r
```

**`M8b`** Picker, from `N7`, across midnight too. The chips carry the dish, so nothing has to be matched against the lines above.

```tg
<b>🔗 С каким приёмом объединить?</b>
пн 26 октября, 20:10 · хлеб
пн 26 октября, 23:50 · чипсы
```

```kb
20:10 · хлеб => ml:j:<mid>:<mid>:<b> | 23:50 · чипсы => ml:j:<mid>:<mid>:<b>
⬅️ К приёму => ml:o:<mid>:<b>
```

#### `M9` not-read

```tg
Такое пока не читается, в дневник не записано. Подойдёт фото, текст или голосовое.
```

- **Trigger:** video, video note, audio file, non-image file, sticker, location. No meal, no raw row.
- **Decision:** stickers and locations answer too, because rule 8 says no silence, and the short copy costs nothing to read.
- **Reads, writes:** nothing. **Back, stale:** none. **Hard day:** same.

**`M9a`** The menu's `🍽 Добавить приём пищи`. Label and `st:add` stay (US-D2), stateless now. Reply labels strand phones, so the regex stays too.

```tg
<b>🍽 Новый приём</b>
Пришли фото, голосовое или напиши, что было. Время и остальное можно поправить потом.
```

```kb
⭐ Избранное => fav:p:0 | 🍲 Блюда => dh:l:0
```

`🍲 Блюда` appears with slice O.

**`M9b`** A stale label from an old reply keyboard, sent with the main keyboard.

```tg
Это кнопка старого меню, в дневник не записано. Новое меню внизу.
```

#### `M10` draft-setting

R31's opt-in. `⚙️` row 2 becomes `🗓 Итог недели: вкл` + `📋 Черновик: выкл`, still 5 rows (VERIFIED, the hub is at its cap). Hub line: `📋 Черновик: выкл. Еда записывается сразу.`

```tg
<i>✓ Черновик перед записью: вкл. Еда попадёт в дневник после «✅ Записать».</i>

<b>⚙️ Настройки</b>
```

```kb
🗓 Итог недели: вкл => set:week | 📋 Черновик: вкл => set:draft
```

- **Trigger:** `set:draft`. **Reads, writes:** `draft_mode`. **Back:** as the hub. **Stale:** absolute state, so it does not drift.
- **Hard day:** nothing suggests it.
- The `kb` here shows only the changed row.

**`M10a`** Turned off. Existing drafts stay as they are.

```tg
<i>✓ Черновик перед записью: выкл. Еда записывается сразу.</i>

<b>⚙️ Настройки</b>
```

```kb
🗓 Итог недели: вкл => set:week | 📋 Черновик: выкл => set:draft
```

**`M10b`** The draft with the setting on. It is a real row with `is_draft=true`, so a restart loses nothing, it never expires, and it sends no reminder. On Приёмы it reads `13:20 · черновик: рис`. A new photo during a draft is its own draft. `✅ Записать` turns it into `M1a`.

```tg
<b>📋 Черновик, пн 26 октября, 13:20</b>
рис, курица и овощи
В дневнике появится после кнопки «✅ Записать».
```

```kb
✅ Записать => ml:ok:<mid> | ✏️ Исправить => ml:o:<mid>:r
🕐 Время => ml:t:<mid>:r | 🗑 Не записывать => ml:d:<mid>:r
```

#### `M11` message-edited

A person edited a sent message. A new source with `supersedes_id` is stored at once, and the record stays. Deleted messages are invisible to bots, and the record keeps its copy (`M15`).

```tg
Сообщение изменено. В дневнике пока прежняя запись. Кнопка «🔁 Прочитать заново» прочитает новый текст.
```

```kb
🔁 Прочитать заново => ml:rr:<mid>:r | ✏️ Исправить => ml:o:<mid>:r
```

- **Trigger:** `edited_message` on a mapped message. PTB's behaviour here was not settled by reading: the slice needs a test that sends a real `edited_message` through the handler.
- **Reads:** `meal_messages`. **Writes:** source. **Back:** none. **Stale:** S. **Hard day:** same; ignoring it is fine.

#### `M12` rollout

Sent once, only to a person with meals from before this release, as a separate message right after the first capture's line (`M1`), never in its place: silent, and it carries the new main keyboard, because an inline reply cannot. A `/start` route would miss most phones (INFERRED: people do not re-run it). Until O ships, the second sentence is absent.

```tg
Теперь еда записывается сразу, без подтверждения. В меню появились «🍲 Блюда». Как это работает: ⚙️ Настройки → ℹ️ Как это работает.
```

- **Reads, writes:** `rollout_seen`. **Back, stale:** none. **Hard day:** same; it asks nothing.

#### `M13` meal-sources

Philosophy principle 3: the path is visible. The buttons take each source apart; the first source has none, because it is the meal.

```tg
<b>📋 Что прислано</b>
1. 📷 Фото, 13:20 · оценка по фото
2. ✏️ «ещё сыр», 13:25
3. 🎙 Голосовое, 13:40
<blockquote expandable>Два банана.</blockquote>
Здесь твои сообщения, как они пришли. Исправления меняют приём, а сообщения остаются.
«🔁 Прочитать заново» читает их ещё раз, твои исправления остаются.
«➖ Отделить» делает сообщение отдельным приёмом.
```

```kb
➖ Отделить №2 => ml:z:<sid>:<b> | ➖ Отделить №3 => ml:z:<sid>:<b>
📷 Показать фото => ml:sf:<mid> | 🔁 Прочитать заново => ml:rr:<mid>:<b>
⬅️ К приёму => ml:o:<mid>:<b>
```

- **Trigger:** `📋 Прислано` on `N7`.
- **`📷 Показать фото`** re-sends the file ids silently. Dead id: toast `Это фото больше недоступно в Telegram`.
- **`🔁 Прочитать заново`** is a paid call on an explicit tap. It replaces only items with `by_user=false`, so corrections survive, and it writes an undoable `reread` event. Hidden at `минимум сил`.
- An album adds `➖ Разделить` (`ml:zq:<mid>:<b>`) in place of the `➖ Отделить` row.
- **Reads:** sources, readings. **Writes:** nothing here. **Back:** `⬅️ К приёму`. **Stale:** S.
- **Hard day:** `M13h`.

**`M13h`** `минимум сил`: nothing to do here, only to see.

```tg
<b>📋 Что прислано</b>
1. 📷 Фото, 23:40
Здесь твои сообщения, как они пришли. Исправления меняют приём, а сообщения остаются.
```

```kb
⬅️ К приёму => ml:o:<mid>:<b>
```

**`M13a`** Legacy.

```tg
<b>📋 Что прислано</b>
Сообщение при этой записи не сохранено.
```

```kb
⬅️ К приёму => ml:o:<mid>:<b>
```

#### `M14` meal-groups

US-B1 lost its home with the draft, so `🏷 Группы` moves to `N7`. The copy and the toggle logic come from `meal_draft.py`, including remembering the choice for the dish. The topic `🏷 Группы еды` names the old place and changes in the same commit.

```tg
<b>🏷 Группы в этой записи</b>
Можно отметить, что было на тарелке. Это видно только на карточках дня и недели.
```

```kb
🫘 Бобовые => ml:gt:<mid>:legumes:<b> | ⚓ Опора => ml:gt:<mid>:anchor:<b> | ✓ 🥦 Овощи => ml:gt:<mid>:veg:<b>
🍎 Фрукты => ml:gt:<mid>:fruit:<b> | 🌾 Злаки => ml:gt:<mid>:grain:<b> | 🥜 Орехи => ml:gt:<mid>:nuts:<b>
🥛 Кальций => ml:gt:<mid>:calcium:<b> | 🌰 Омега-3 => ml:gt:<mid>:omega:<b>
⬅️ К приёму => ml:o:<mid>:<b>
```

- **Trigger:** `🏷 Группы`. The labels and keys are `food_groups.GROUPS`, as shipped (VERIFIED).
- **Reads:** `food_groups` of the meal. **Writes:** `food_groups` as a new dict, event `groups`.
- **Back:** `⬅️ К приёму`. **Stale:** S. **Hard day:** hidden with the row that holds it.

#### `M15` explanations

Per-slice topics in `explain_screen.TOPICS`, no model call. The list passes the row cap with these (VERIFIED: 7 rows today, a test pins the labels), so it pages at 4 with `hw:p:<pg>`. **The PMIDs come from the research report and nobody on this project has read them yet: R23 requires reading each abstract in PubMed before release.** Project guesses are labelled in the copy. Texts go to the owner before release (US-J1).

```tg
<b>ℹ️ Как это работает</b>
```

```kb
📊 Карточка дня => hw:t:day | 📊 Карточка недели => hw:t:week
👣 Один шаг => hw:t:step | 🏷 Группы еды => hw:t:groups
📷 Запись сразу => hw:t:capture | 🎙 Голосовые => hw:t:voice
◀️ => hw:p:<pg> | 1/2 => hw:nop | ▶️ => hw:p:<pg>
⬅️ Настройки => set:open
```

Topic screens render through the shipped `topic_screen`, with back `⬅️ Как это работает`. Ids in order below: `M15a` to `M15e`.

**`🍽 Приём и блюда`** (ships with N)

- Что это: `Приём состоит из блюд, и каждое можно поправить отдельно.`
- Как работает: `В приёме блюдо записано таким, каким было в тот день. Правка блюда меняет будущие приёмы, прошлые остаются как были. Ранние записи не разделены на блюда: цифры у них на всю запись. Удалённый приём не считается ни на карточке дня, ни на карточке недели, но хранится, чтобы его можно было вернуть.`
- Откуда идея: `С фото лучше определяется, что было, чем сколько, поэтому сначала выбирается блюдо, а граммы идут вторыми. Деление на блюда сделано проектом и в исследованиях не проверялась.`
- Источник: `PMID 30060528`

**`📷 Запись сразу`** (M)

- Что это: `Фото, текст или голосовое уже значит записать. Подтверждения нет, отвечать не требуется.`
- Как работает: `Сначала сохраняется то, что отправлено, потом бот читает это и отвечает одной строкой. То, что прислано, не меняется, а прочитанное можно поправить в любой день. Сообщение присоединяется к приёму, только если это ответ на него, один альбом или ясные слова вроде «ещё сыр». Иначе это новый приём, и кнопка «🔗 Объединить» соединит его с прошлым, а «➖ Отделить» вернёт как было. Границы вроде трёх часов для таких слов и знак ≈ в «вечером» выбраны проектом и не проверялись.`
- Откуда идея: `Подробный дневник быстро бросают, особенно в тяжёлые дни, поэтому важнее сохранить сам факт еды. Прямых исследований «сохранить сразу» против «проверить сначала» не нашлось: это решение проекта. Лишнее соединение стоит дороже лишнего разделения, поэтому без явного знака приёмы не соединяются.`
- Источник: `PMID 39190910, PMID 31155473`
- Приватность, отдельной строкой: `Сообщения боту не защищены сквозным шифрованием: их может прочитать тот, кто запускает этого бота. Сообщение, удалённое в чате, остаётся в дневнике. Убрать запись из дня: ✏️ Исправить, затем 🗑 Удалить. Сама запись при этом хранится, чтобы её можно было вернуть.`

**`🕐 Время приёма`** (M)

- Что это: `У записи два времени: когда отправлено и когда была еда.`
- Как работает: `Время отправки не меняется. Время еды сначала совпадает с ним, а слова «вчера», «ночью» или «в 13:20» его сдвигают, и ответ это называет. Знак ≈ значит «примерно». «Не помню» оставляет приём в том же дне, но без времени: он виден в списке приёмов, а в строке времён на карточке дня его нет.`
- Откуда идея: `По памяти время еды обычно называют позже, чем оно было, поэтому время не выдумывается.`
- Источник: `PMID 35198846`

**`🎙 Голосовые`** (M)

- Что это: `Голосовое записывается как фото. Это проба: качество русских названий еды пока измеряется.`
- Как работает: `Голосовое сохраняется, затем расшифровывается на собственном сервисе проекта. Кнопка «🎙 Текст» покажет, что услышано, а из оговорки «один… нет, два банана» берётся последнее.`
- Откуда идея: `В небольшой пробе на 18 человек голосом записывали чаще, чем текстом. С фото это не сравнивали, поэтому голос здесь не считается лучше.`
- Источник: `PMID 37191989`
- Приватность, отдельной строкой: `Если собственный сервис не отвечает, голосовое отправляется во внешний сервис: звук уходит за пределы проекта.`

**`🍲 Мои блюда`** (O)

- Что это: `Все блюда из записей в одном месте. ⭐ записывается одним нажатием.`
- Как работает: `Блюдо появляется само, когда приходит еда. «🔗 Объединить» соединяет два названия одного блюда, «🗑 Из списков» прячет блюдо. Прошлые приёмы и их цифры при этом не меняются, и оба действия отменяются.`
- Откуда идея: `Повторы в еде здесь не считаются недостатком. Решение проекта, отдельно не проверялось.`
- Источник: `Решение проекта.`

## 6. Slice O: history, dishes, favourites

#### `O1` day-meals

```tg
<b>🍽 Приёмы, пн 26 октября</b>
8:40 · овсянка
13:20 · рис, курица и овощи
≈ 19:00 · суп
без времени · яблоко
<i>Нажми на время, чтобы открыть приём и поправить.</i>
```

```kb
8:40 => ml:o:<mid>:<b> | 13:20 => ml:o:<mid>:<b> | ≈ 19:00 => ml:o:<mid>:<b>
без времени => ml:o:<mid>:<b>
‹ вс 25 => ml:dl:<day>:<b> | вт 27 › => ml:dl:<day>:<b>
⬅️ Назад: сегодня => st:t
```

- **Trigger:** `🍽 Приёмы` on Сегодня, Вчера and 7 дней (`O13`).
- **Text:** lines of what, chips of when: the chip's job is to go there, and the text must carry the whole name anyway. Names cut at 80 with `…`.
- **Chips:** 3 per row, up to 9, then the last slot is `▶️`. Duplicates of a clock read `13:20 · 2`.
- **Paging:** `‹ ›` walk the days. No `›` beyond today. No totals, counts or empty slots.
- **Reads:** `get_user_meals_between` with the filter, including `day_only` and drafts as their own line. **Writes:** nothing.
- **Back:** by origin, kept while walking: `⬅️ Назад: сегодня`, `⬅️ Назад: вчера` (`st:y`), `⬅️ Назад: 7 дней` (`st:w`).
- **Stale:** the day is in the data, so it always renders that day. Crosses photo and text through `card_delivery.tap`.
- **Hard day:** `O1h`.

**`O1a`** Empty day.

```tg
<b>🍽 Приёмы, вс 25 октября</b>
В этом дне записей нет.
```

```kb
➕ Приём в этот день => ml:da:<day>:<b>
‹ сб 24 => ml:dl:<day>:<b> | пн 26 › => ml:dl:<day>:<b>
⬅️ Назад: сегодня => st:t
```

**`O1b`** Add to that day, ask `day_add`. The next text or photo becomes a meal with `time_precision='day_only'` on that day.

```tg
<b>➕ Приём, вс 25 октября</b>
Напиши или пришли, что было. Запись будет в этом дне, без времени.
```

```kb
⬅️ Приёмы дня => ml:dl:<day>:<b>
```

**`O1h`** Hard day. One line or nine read the same. A 4:10 meal is on the line, the day widens as R28 says, and there is no comment.

```tg
<b>🍽 Приёмы, пт 30 октября</b>
4:10 · хлопья
13:20 · 📷 фото сохранено
23:40 · чипсы, три пачки
```

```kb
4:10 => ml:o:<mid>:<b> | 13:20 => ml:o:<mid>:<b> | 23:40 => ml:o:<mid>:<b>
‹ чт 29 => ml:dl:<day>:<b> | сб 31 › => ml:dl:<day>:<b>
⬅️ Назад: вчера => st:y
```

#### `O2` dishes

Sent on `🍲 Блюда` in the main keyboard, whose row 3 becomes `⚙️ Настройки`, `🍲 Блюда`. A label added to a reply keyboard strands nothing, and `M12` carries it to phones.

```tg
<b>🍲 Блюда</b>
Сначала недавние. Страница 1 из 3.
```

```kb
Рис · 3 раза => dh:o:<fid>:<b>
⭐ Омлет с сыром · 12 раз => dh:o:<fid>:<b>
Суп с курицей · 2 раза => dh:o:<fid>:<b>
Овсянка => dh:o:<fid>:<b>
◀️ => dh:l:<pg> | 🔍 Найти => dh:f | ▶️ => dh:l:<pg>
```

- **Trigger:** menu. Top screen, no back. One row per dish, 4 per page, name budget as `BUTTON_NAME_MAX`. A count with no live item shows the name only. One page: only `🔍 Найти`.
- **Decision (US-O2 "текст на этом экране ищет"):** R30 and R35 beat it. An open list must not capture «суп» as a search, or the record is lost. So search is an explicit button.
- **Reads:** dishes not hidden, not merged, by latest live item; count of live items. **Writes:** nothing.
- **Stale:** a page past the end shows the last page; a gone dish redraws the list with `<i>Этого блюда уже нет в списке.</i>`.
- **Hard day:** `O2h`.

**`O2a`** Empty.

```tg
<b>🍲 Блюда</b>
Пока пусто. Блюда появятся сами, когда еда будет записываться.
```

**`O2b`** Search, ask `dish_find`. Closes on any tap or after 10 minutes. Matched in Python.

```tg
<b>🔍 Найти блюдо</b>
Напиши название.
```

```kb
⬅️ Все блюда => dh:l:0
```

**`O2c`** Found.

```tg
<b>🍲 Блюда</b>
Нашлось по «рис»:
```

```kb
Рис · 3 раза => dh:o:<fid>:<b>
Рис с овощами => dh:o:<fid>:<b>
⬅️ Все блюда => dh:l:0
```

**`O2d`** Nothing found. The slot stays.

```tg
<b>🔍 Найти блюдо</b>
По «рис» ничего не нашлось. Напиши иначе.
```

```kb
⬅️ Все блюда => dh:l:0
```

**`O2h`** `минимум сил`: counts are hidden, because a number beside food is a thing to look at.

```tg
<b>🍲 Блюда</b>
Сначала недавние. Страница 1 из 3.
```

```kb
Рис => dh:o:<fid>:<b>
⭐ Омлет с сыром => dh:o:<fid>:<b>
Суп с курицей => dh:o:<fid>:<b>
Овсянка => dh:o:<fid>:<b>
◀️ => dh:l:<pg> | 🔍 Найти => dh:f | ▶️ => dh:l:<pg>
```

#### `O3` dish-card

```tg
<b>🍲 Омлет с сыром</b>
Было 12 раз, последний раз ср 28 октября.
```

```kb
🍽 Записать => dh:log:<fid> | ✓ ⭐ В избранном => dh:fv:<fid>:0
🕘 Когда было => dh:h:<fid>:0 | 🔢 Цифры => dh:n:<fid>:1
Ещё › => dh:mo:<fid>:<b>
⬅️ Все блюда => dh:l:0
```

- **Trigger:** a name on `O2`, `🍲 Всё о блюде` on `N2`, or on a favourite.
- **Portion:** `Обычная порция: 200 г.` only when the person typed it or numbers are open (rule 1 vs US-O3).
- **Decision:** `🍽 Записать` is an addition to the stories, because otherwise the list of one's own food is a dead end. It logs the usual portion, free, and shows `O10` (section 9, item 9).
- **`🔢 Цифры`** opens the numbers and adds `✏️ Править цифры` (`O3a`), so an edit is never offered to someone who keeps numbers hidden (G5-R01).
- **Reads:** dish, live items, ⭐. **Writes:** nothing on open.
- **Back:** `⬅️ Все блюда`, `⬅️ К блюду в приёме`, `⬅️ Избранное` by token.
- **Stale:** merged, the target with `<i>Это блюдо объединено с «омлет».</i>`. Hidden, the card with `↩️ Вернуть` in reach.
- **⭐:** a dish that is not a favourite shows `⭐ В избранное` (`O3c`), one that is shows `✓ ⭐ В избранном`.
- **`Ещё ›`** opens `O14`: name, merge and hiding are rare, and hiding should not sit beside `🍽 Записать`.
- **Hard day:** `O3h`.

**`O3a`** Numbers open.

```tg
<b>🍲 Омлет с сыром</b>
Обычная порция: 200 г.
Было 12 раз, последний раз ср 28 октября.
<blockquote expandable>На 100 г: 🔥 ≈ 180 ккал · Б 12 · Ж 14 · У 1</blockquote>
Это оценка, цифры примерные.
```

```kb
🍽 Записать => dh:log:<fid> | ✓ ⭐ В избранном => dh:fv:<fid>:0
🕘 Когда было => dh:h:<fid>:0 | ✓ 🔢 Цифры => dh:n:<fid>:0
✏️ Править цифры => dh:nm:<fid> | Ещё › => dh:mo:<fid>:<b>
⬅️ Все блюда => dh:l:0
```

**`O3b`** Numbers for a portion with no known weight, as with an old favourite.

```tg
<b>🍲 Омлет с сыром</b>
Было 12 раз или больше: не все ранние записи здесь считаются.
<blockquote expandable>Цифры на порцию, без веса: ≈ 360 ккал.</blockquote>
```

```kb
🍽 Записать => dh:log:<fid> | ✓ ⭐ В избранном => dh:fv:<fid>:0
🕘 Когда было => dh:h:<fid>:0 | ✓ 🔢 Цифры => dh:n:<fid>:0
✏️ Править цифры => dh:nm:<fid> | Ещё › => dh:mo:<fid>:<b>
⬅️ Избранное => fav:p:0
```

**`O3c`** No numbers, no meals, so no `🕘 Когда было` either.

```tg
<b>🍲 Хлеб</b>
Пока без приёмов.
<blockquote expandable>Цифр пока нет.</blockquote>
```

```kb
🍽 Записать => dh:log:<fid> | ⭐ В избранное => dh:fv:<fid>:1
✓ 🔢 Цифры => dh:n:<fid>:0 | ✏️ Править цифры => dh:nm:<fid>
Ещё › => dh:mo:<fid>:<b>
⬅️ Все блюда => dh:l:0
```

**`O3h`** `минимум сил`: nothing here asks to tidy anything, and the count is hidden as on `O2h` (section 9, item 50).

```tg
<b>🍲 Омлет с сыром</b>
Последний раз ср 28 октября.
```

```kb
🍽 Записать => dh:log:<fid> | ✓ ⭐ В избранном => dh:fv:<fid>:0
⬅️ Все блюда => dh:l:0
```

#### `O4` dish-rename

```tg
<b>✏️ Название: омлет с сыром</b>
Напиши новое название.
```

```kb
⬅️ К блюду => dh:o:<fid>:<b>
```

- **Trigger:** `✏️ Название` on `O14`, ask `dish_name`.
- **Result:** the card with `<i>✓ Теперь: омлет.</i>` and `↩️ Вернуть`. The old name becomes an alias, so typing it logs this dish. Past items keep their name.
- **Reads:** dishes, compared in Python. **Writes:** name, `aliases`, event. **Back:** `⬅️ К блюду`.
- **Stale:** as `O3`. **Hard day:** not reachable at `минимум сил`.

**`O4a`** Taken name. Never an IntegrityError.

```tg
<b>✏️ Такое блюдо уже есть</b>
«Омлет» уже есть в блюдах. Объединить их? Прошлые приёмы останутся как есть.
```

```kb
🔗 Объединить => dh:j:<fid>:<fid>
⬅️ К блюду => dh:o:<fid>:<b>
```

#### `O5` dish-numbers

```tg
<b>✏️ Цифры: омлет с сыром</b>
На 100 г, четыре числа: ккал, белки, жиры, углеводы. Например: 150 10 8 2
```

```kb
⬅️ К блюду => dh:o:<fid>:<b>
```

- **Trigger:** `✏️ Править цифры`, ask `dish_numbers`. Spaces or commas. Non-numbers close the slot and go down the router. No plausibility verdict.
- **Result:** the card with `<i>✓ Для будущих приёмов. Прошлые остаются как были.</i>` and `↩️ Вернуть`.
- **Reads:** dish. **Writes:** per-100 g numbers, `numbers_by_user`, `numbers_quality='user'`, `numbers_basis='per_100'`, event. Past items untouched.
- **Back:** `⬅️ К блюду`. **Stale:** as `O3`. **Hard day:** not reachable at `минимум сил`.

#### `O6` dish-history

```tg
<b>🕘 Омлет с сыром</b>
ср 28 октября, 8:40
вт 27 октября, 9:10 · 2 порции
сб 24 октября, без времени
Ранние записи могут здесь не показываться: в них блюда не разделены.
```

```kb
28 окт => ml:o:<mid>:<b> | 27 окт => ml:o:<mid>:<b> | 24 окт => ml:o:<mid>:<b>
◀️ => dh:h:<fid>:<pg> | 1/3 => dh:nop | ▶️ => dh:h:<fid>:<pg>
⬅️ К блюду => dh:o:<fid>:<b>
```

- **Trigger:** `🕘 Когда было`. Up to 9 lines and chips, duplicates of a date read `28 окт · 2`.
- **Reads:** live items by `personal_food_id`, merged ones included. **Writes:** nothing.
- **Back:** `⬅️ К блюду`. **Stale:** deleted meals are simply gone.
- **Hard day:** dates only, no intervals and no "ещё раз за неделю".

#### `O7` dish-merge

```tg
<b>🔗 Омлет с сыром: с чем объединить?</b>
Прошлые приёмы и их цифры останутся как есть. Страница 1 из 3.
```

```kb
Омлет => dh:j:<fid>:<fid> | Омлет с ветчиной => dh:j:<fid>:<fid>
Сыр => dh:j:<fid>:<fid> | Яичница => dh:j:<fid>:<fid>
◀️ => dh:jl:<fid>:<pg> | 🔍 Найти => dh:jf:<fid> | ▶️ => dh:jl:<fid>:<pg>
⬅️ К блюду => dh:o:<fid>:<b>
```

- **Trigger:** `🔗 Объединить` on `O14`, or `O4a`. Names sharing a word come first, then recent. `🔍 Найти` opens ask `merge_find` with the same text as `O2b`.
- **Writes:** source dish `merged_into_id`; items' `personal_food_id` relinked, `original_food_id`, name and numbers untouched; ⭐ repointed, or soft-removed if the target has one; event `food_merge`.
- **Reads:** dishes. **Back:** `⬅️ К блюду`. **Stale:** a dish already merged or hidden shows its card with `<i>Это блюдо уже объединено.</i>`.
- **Hard day:** never suggested, reached only by hand.

**`O7a`** Result, on the target. Undo works at any later time, by `original_food_id`.

```tg
<i>✓ «Омлет с сыром» теперь в блюде «омлет». Прошлые приёмы и их цифры на месте.</i>

<b>🍲 Омлет</b>
Вместе было 20 раз, последний раз ср 28 октября.
```

```kb
🍽 Записать => dh:log:<fid> | ✓ ⭐ В избранном => dh:fv:<fid>:0
🕘 Когда было => dh:h:<fid>:0 | 🔢 Цифры => dh:n:<fid>:1
Ещё › => dh:mo:<fid>:<b> | ↩️ Вернуть => dh:u:<eid>
⬅️ Все блюда => dh:l:0
```

#### `O8` dish-hidden

```tg
<b>🗑 Убрано из списков: омлет с сыром</b>
Прошлые приёмы на месте. Блюдо вернётся, если снова его записать.
Вернуть можно и позже, пока эта кнопка на экране.
```

```kb
↩️ Вернуть => dh:u:<eid>
⬅️ Все блюда => dh:l:0
```

- **Trigger:** `🗑 Из списков` on `O14`, at once.
- **Writes:** `hidden_at`, ⭐ soft-removed, event `food_hide`. Filtered in lists, picker, barcode and ANN. Eating it again unhides: typing the name records the meal and the dish returns.
- **Reads:** dish. **Back:** `⬅️ Все блюда`. **Stale:** undo on a restored dish opens its card. **Hard day:** not reachable at `минимум сил`.

#### `O9` favourites-list

A tap logs; undo follows under the thumb, so no question. This meets US-O4's "один тап" (it is two today, so the story's "как сейчас" is off, and the target stands). The pencil opens the favourite's own screen (`O11`), which leads on to the dish.

```tg
<b>⭐ Избранное</b>
Нажатие на название записывает сразу. Кнопка ✏️: порция и другие настройки.
```

```kb
Омлет с сыром => fav:l:<id>:n | ✏️ => fav:o:<id>:0
Овсянка => fav:l:<id>:n | ✏️ => fav:o:<id>:0
Суп с курицей => fav:l:<id>:n | ✏️ => fav:o:<id>:0
◀️ => fav:p:<pg> | 1/3 => fav:x | ▶️ => fav:p:<pg>
```

- **Trigger:** `⭐ Избранное`. Top screen, no back. The old `⬅️ Назад`, `⬅️ К избранному` and the 4-in-a-row chips go, and tests pinning them change on purpose. With numbers on: `Омлет с сыром · ≈ 360 ккал`.
- **Reads:** favourites, dish. **Writes:** nothing on open.
- **Stale:** a removed favourite redraws with the shipped `_STALE`. A list from yesterday still logs, at now, and says the line: logging is what it asked for.
- **Hard day:** menu, name, done. The cheapest record besides sending, free. Same screen.

**`O9a`** Empty. The shipped copy names a button that no longer exists.

```tg
<b>⭐ Избранное</b>
Пока пусто. ⭐ ставится кнопкой «⭐ В избранное» у блюда.
```

#### `O10` favourite-logged

```tg
Записано: омлет с сыром.
```

```kb
↩️ Отменить => fav:u:<mid> | 🕐 Время => ml:t:<mid>:r
⬅️ Избранное => fav:p:0
```

- **Trigger:** a favourite row or `🍽 Записать` on a dish. Toast `Записано`. The same favourite on the same message within 3 s (ASSUMED): toast `Уже записано`.
- **Writes:** source `favorite`, meal at now, one item copied from the portion (`numbers_source='dish'`, or the portion's own numbers on `per_portion`), event. No model.
- **Reads:** favourite, dish. **Back:** `⬅️ Избранное`; from a dish, `⬅️ К блюду`.
- **Stale:** S. **Hard day:** same.

**`O10a`** Undone. Soft delete.

```tg
↩️ Запись отменена: омлет с сыром.
```

```kb
⬅️ Избранное => fav:p:0
```

#### `O11` favourites-item

```tg
<b>⭐ Омлет с сыром</b>
Обычная порция: 200 г.
```

```kb
🍽 Записать => fav:l:<id>:n
⚖️ Порция => fav:g:<id> | 🔢 Цифры => fav:o:<id>:0:1
🍲 Всё о блюде => dh:o:<fid>:<b> | 🗑 Из избранного => fav:r:<id>:0
⬅️ Избранное => fav:p:0
```

- **Trigger:** `✏️` on `O9`. Name and numbers go through the dish card, so one correction lives in one place (US-O4).
- **Portion:** shown only when typed or with numbers open; otherwise the line is absent. A whole-meal ⭐ or an old favourite reads `Порция: как в той записи.`
- **Reads:** favourite, dish. **Writes:** nothing on open.
- **Back:** `⬅️ Избранное`. **Stale:** the list with `_STALE`. **Hard day:** same.

**`O11a`** Portion, ask `portion`. Grams leave the numbers as they are when the portion has no weight. Back goes where it came from.

```tg
<b>⚖️ Порция: омлет с сыром</b>
Напиши граммы числом, например 200.
```

```kb
⬅️ К избранному блюду => fav:o:<id>:0
```

**`O11b`** Removed. The dish stays.

```tg
<b>🗑 Убрано из избранного: омлет с сыром</b>
Само блюдо на месте.
Вернуть можно и позже, пока эта кнопка на экране.
```

```kb
↩️ Вернуть => fav:b:<id>:0
⬅️ Избранное => fav:p:0
```

#### `O12` whole-meal-favourite

`⭐ Весь приём` on `N7`: creates a dish named by the joined names, with the sum as a portion's numbers (`per_portion`, no grams), and redraws `N7` with `✓ ⭐ Весь приём`, a line in the text, and toast `Весь приём в избранном`. A second tap removes it, toast `Убрано из избранного`. `⭐ В избранное` on a dish uses that item's portion (US-O5). No screen of its own, shown here as the state of `N7`.

```tg
<b>🍽 Ещё: приём пн 26 октября, 13:20</b>
рис, курица и овощи
Весь приём в избранном.
```

```kb
✓ ⭐ Весь приём => ml:fm:<mid>:0:<b> | 🔗 Объединить => ml:jl:<mid>:<b>
📋 Прислано => ml:src:<mid>:<b> | 🏷 Группы => ml:gr:<mid>:<b>
⬅️ К приёму => ml:o:<mid>:<b>
```

#### `O13` day-card-tabs

Changed in `stats_screen` only. The numbers row becomes `🔢 Цифры`, `🍽 Приёмы`, and `🔋 Силы` moves beside `🎯` (VERIFIED: `_tabs` `beside` holds the energy button today). States with no `🎯` give `🔋` its own row. Tests pinning day-card labels change on purpose. The picture dict and the caption both gain `≈` and `без времени`.

```tg
<b>📊 Сегодня, сб 31 октября</b>
8:40 · 13:20 · ≈ 19:00
```

```kb
🎯 Выбрать другое => st:o | 🔋 Силы: обычно => st:e
🔢 Цифры => st:n | 🍽 Приёмы => ml:dl:<day>:t
✓ Сегодня => st:t | Вчера => st:y | 7 дней => st:w
```

- **Reads, writes:** as shipped. **Back:** top screen. **Stale:** as shipped.
- **Hard day:** at `минимум сил` the row stays, because it is how the day is reached.
- **Empty today:** no `🍽 Приёмы`.

**`O13a`** Week card. **Decision (US-O1 weekday buttons vs at most 3 per row):** one `🍽 Приёмы по дням` opening the newest day with a meal, then `‹ ›`. The cost is up to 6 taps to Monday. Sunday's message gets no button.

```tg
<b>📊 7 дней, 25–31 октября</b>
```

```kb
🔢 Цифры => st:n | 🍽 Приёмы по дням => ml:dl:<day>:w
Сегодня => st:t | Вчера => st:y | ✓ 7 дней => st:w
```

#### `O14` dish-more

```tg
<b>🍲 Ещё: омлет с сыром</b>
🗑 Из списков: блюда не будет в «Блюдах» и при выборе блюда, прошлые приёмы останутся.
```

```kb
✏️ Название => dh:rn:<fid> | 🔗 Объединить => dh:jl:<fid>:0
🗑 Из списков => dh:x:<fid>
⬅️ К блюду => dh:o:<fid>:<b>
```

- **Trigger:** `Ещё ›` on `O3`.
- **Reads:** dish. **Writes:** nothing on open. **Back:** `⬅️ К блюду`. **Stale:** as `O3`.
- **Hard day:** not reachable at `минимум сил`.

## 7. Tap counts (US-O6)

| Goal | Path | Taps | Story |
|---|---|---|---|
| Record a meal | send | **0** | |
| A meal from the bot's reply | `✏️ Исправить` | **1** | 1 ✓ |
| Any meal of the day from the day card | `🍽 Приёмы`, clock | **2** | ≤2 ✓ |
| Time from the line | `🕐 Время`, chip | **2** in all | 1–2 ✓ |
| Time typed | reply `13:20` | **0** | ✓ |
| Halve a one-dish meal | `✏️ Исправить`, `½` | **2** | 1–2 ✓ |
| Halve one dish of many | `✏️ Исправить`, dish, `½` | **3** | 1 + 2 ✓ |
| Replace a dish by a known one | `✏️ Исправить`, dish, `🔄 Заменить`, name | **3–4** | **Over by one:** a replace needs a target and a value. A typed reply is **0** |
| Delete a wrong photo | `✏️ Исправить`, `🗑 Удалить` | **2** | 1–2 ✓ |
| ⭐ a whole meal, join, sources, groups | `✏️ Исправить`, `Ещё ›`, the action | **3** | no story limit; one more than before fresh eyes |
| Блюда, ⭐ from the menu | button | **1** | 1 ✓ |
| Repeat a ⭐ | `⭐ Избранное`, name | **2**, today 3 | "один тап" from the list ✓ |
| Repeat by name | type it | **0**, free when exact | |
| A meal on another day of the week | `7 дней`, `🍽 Приёмы`, `‹`…, clock | **3+** | no story limit |

## 8. Left out on purpose

- **A home button and a dishes hub.** The reply keyboard is home.
- **A review step on a correction.** Corrections apply at once, with undo.
- **`🔁 Правок: 1` on the sources screen.** It reads as a score. The 🔍 mark there too, because 🔍 means search.
- **`минимум сил` dropping `Время` from the line,** and any line that changes shape by mode.
- **`✂`.** Not drawn; `➖` is. Also not used: 🗒 ✍ 📝 💬 🔎.
- **Seven weekday buttons on the week card.** Breaks at most 3 per row.
- **Model grams on a dish line.** Behind `🔢 Цифры`.
- **A list of hidden dishes, and `↩️ Убранное` for a day.** Undo and eating the dish again cover this now. `↩️ Убранное` is a later, low-priority way back, with no counter.
- **Dedupe of a meal sent twice.** Two lines and a join row: a wrong guess would lose a real second meal.
- **Part of day from labels.** `Завтрак`, `Обед`, `Ужин` chips go, and `meal_type` stays NULL for new rows.
- **Reactions as an ack.** A line is needed anyway to carry the buttons.
- **Automatic split of an album.** Paid, so only on a tap with one question.
- **Video notes as food, deleting media after reading, a photo-only mode, the attention measure.** R35 keeps these for later.
- **A `Текст` search on the Блюда screen,** see `O2`.

## 9. For the owner

Each default is taken, and the build goes on with it.

**To decide:**

1. **Per-dish numbers (R33, R34).** "Totals = sum of items" and "N waits for nothing" meet a model that returns one set of numbers per meal. A real fix needs a schema change and a priced eval. Default: plate fallback, no eval run yet.
2. **Voice door.** R32 says `/jobs/upload`, which stalls during a GPU claim. Default: `/v1`. The wording is hers to change.
3. **Export.** US-N5 meets a feed that cannot say "deleted", and the consumer is outside this repo. Default: `updated_since` + `deleted_at`.
4. **US-O1 weekday buttons.** Default: a single button.
5. **US-N6 chips.** Default: the story's. Alternative: `−1 ч`, `−3 ч`, `Ночью`, `Вчера вечером`.
6. **R35 "`Отделить` restores the originals" vs typed fixes.** Default: typed fixes stay.
7. **R35 "asks nothing" vs the `M6a` chooser.** Default: the chooser.
8. **`🔄 Заменить` vs the story's `🔁`.** Default: 🔄, as `🔄 Другой шаг` already means swap.
9. **`🍽 Записать` on the dish card.** An addition no story names. Default: included.
10. **Draft as a real row.** Alternative: the setting opens the saved card, with no unsaved state at all. Default: a real opt-in, as R31 says.
11. **Soft delete.** «Удалено» keeps the row. Real erasure comes with R35's "delete media". Voice bytes stay until then.

**Settled by a newer rule, recorded here:**

12. Rule 1 vs US-N2, US-O3 quantities: portions and the person's words (section 2.6).
13. US-O2 text search vs R30, R35: `🔍 Найти`.
14. Stage doc and US-B1 groups in the draft vs R31: groups move to the card.
15. US-D2 button and flow vs R35: the label stays, stateless.
16. R31 and principle 5 "at the confirmation step" are outdated by R30.
17. The philosophy line allowing "logging streaks" vs G5-R04: the newer wins, and no streak is built.

**Wording no design meets exactly:**

18. US-O6 replace at 3–4 taps (section 7).
19. US-O4 "как сейчас" is two taps today, one after this.
20. US-N2 meal label: part of day comes from the clock.
21. "No second push" vs the rollout line: it is silent and once.

**Fresh eyes, round 1: changes that bend a written story.** Each default is taken, the wording is the owner's to change.

22. **`Ещё ›` on the meal card and the dish card (`N7`, `O14`).** The card read as a wall to every outside reader (11 to 13 buttons), so an earlier decision against a second level is reversed. `✏️ Исправить` stays one tap from the line, rare actions cost one tap more (section 7).
23. **The story's «не прочитано» in the day is now `📷 фото сохранено`.** Every hard-day reader took «не прочитано» for an undone task or for "I have not read this", and 2.7 forbids that.
24. **`⬅️ К записи` is now `⬅️ К сообщению` here.** The stories use the old label for the draft's own screen, which stays as shipped. `⬅️ Блюда` is `⬅️ Все блюда`, one letter from `⬅️ Блюдо`.
25. **`🗑 Убрать` is gone.** One word meant a dish out of a meal, a dish out of the lists, a ⭐ off, and on `M7d` deleting the meal. The stories and `M15` name the action, the label now names what it leaves (section 2.1).
26. **`M5d`: a chip applies the grams.** `ml:ga` is new, because the old copy named a button the screen did not have.

**Fresh eyes, round 1: findings rejected, and why.**

27. **`🎙 Текст` reads as "type something"** (two readers). R35 names the `Текст` button. `M4c` explains it once.
28. **`M1a` shows no time or day.** R35 fixes the one line. The day appears wherever the meal is named later (section 2.9).
29. **`M1c`, `M1h4`: is the new meal separate?** A reader wanted «Записано отдельно». R35 fixes «Записано: …», and the join button with its day answers it. The join row stays too, though one reader felt it as an unanswered question: R35 requires a reversible `🔗 Объединить`, it joins nothing by itself, and ignoring it is fine. Hiding it by mode would make the line change shape (`M1a`).
30. **`M7`: is this in the diary, and who retries with `🔁 Ещё раз`?** The first half is reversed by item 44. R35 fixes the copy and both button words. The toast `Читаю` answers the second one on tap, and the meal is in the day as `📷 фото сохранено`.
31. **`M1d`, `M1h2`: «≈ 3000 ккал» stings.** Shown only to someone who turned numbers on, and 2.6 forbids a cap or a comment.
32. **`M5` undo:** `➖ Отделить` stays, because R35 names it and a join's undo is a split, not a removal. `M8a` now says the result is its own meal and carries `🗑 Удалить`.
33. **`O9`: a tap on a name logs by accident.** That is US-O4's one tap, the undo sits under the thumb, and the hint line stays.
34. **`N1` rice with no amount reads as forgotten.** Writing `1 порция` invents an amount, and rule 7 forbids that (section 2.6).
35. **`O2`: a dish with no count.** A count with no live item is unknown, so nothing is shown instead of 0.
36. **`N5` offers only earlier times and no plus chips.** US-N6 asks for these. The typed path covers the rest, and item 5 holds the alternative.
37. **`M6a`, `M5d` still ask.** The one question left is on an ambiguous correction (item 7). Both now say that no answer changes nothing.
38. **`O5` is homework.** Reached only by choosing it twice, behind `🔢 Цифры`.
39. **`M14` «⚓ Опора», `O13` «🎯 Выбрать другое», «🔋 Силы: обычно», Б/Ж/У.** Shipped labels that other screens and tests share, not this spec's to rename here.
40. **«Приём» reads as a doctor's appointment at first.** The word is the shipped name of the thing (`🍽 Добавить приём пищи`, `M9a`), and it settles after one look.
41. **`📋 Черновик: вкл`, state or switch?** The same shape as the shipped `🗓 Итог недели: вкл`, and the notice above says what changed.
42. **`PMID` means nothing to a reader; topics are long.** R23 asks for the source, and a topic opens only on request. Privacy lines moved to the top of their topics, which is what the readers feared missing.
43. **Sample button contrast and picture placeholders in the mocks.** The mock's look, not the screen's.

**Fresh eyes, round 2: changes that bend a written story.** Each default is taken, the wording is the owner's to change.

44. **`M7`, `M7a`, `M4d`, `M7d`, `N1d`: a second line, `В дневнике запись есть.`** Reverses the first half of item 30, with new evidence: all four readers could not say whether a saved photo is in the diary, and rule 2.9 now writes `в дневник не записано` on the opposite case. R35's sentence is kept word for word, the line goes under it.
45. **`M1`, `M2`, `M4`: `Фото записано, читаю…`.** US-M1 words it `Записано, читаю фото…`, and the bare `Записано,` left readers asking what was written. The result line stays R35's `Записано: …`.
46. **`⬅️ К сообщению` is now `⬅️ К ответу бота`** (updates item 24, all four asked which message). **`⬅️ Приём`, `⬅️ Блюдо` are `⬅️ К приёму`, `⬅️ К блюду`,** because they read as actions and as `⭐ Приём`; **`⬅️ Приёмы` is `⬅️ Приёмы дня`,** so it no longer sits one letter from the card's back label. **`⬅️ Сегодня` is `⬅️ Карточка: сегодня`,** because beside the day arrows it read as one more day.
47. **The `Вчера` chip reads `Вчера, 30 окт`, and it means today minus one.** The written rule said so, but the mocks moved a meal to the day before the meal, and three readers saw the clash. US-N6's chip keeps its word and gains the date. The samples follow the rule.
48. **`⭐ Приём` is `⭐ Весь приём`, with a line in the text when it is on.** Only a tick showed the state, and the word was the same as the back label.
49. **`🍲 Блюдо` is `🍲 Всё о блюде`.** Under a dish in a meal it read as the same dish.
50. **`O3h` drops `Было 12 раз` (owner's call, brief's known issue).** `O2h` hides counts for a stated reason, and section 10.3 already tests that. The date stays, as on `O6`. To restore it, change the one line.
51. **`🍽 Приёмы` on the week card is `🍽 Приёмы по дням`.** All four asked which day it opens.

**Fresh eyes, round 2: findings rejected, and why.** Items 27–43 stand unless named above; a repeat without new evidence stays rejected.

52. **`M5` `➖ Отделить` as undo** (item 32 again, four readers). They guessed correctly that it does not delete, and `M8a` carries `🗑 Удалить`. A second line on every reply costs more reading than the guess.
53. **`M7` `🔁 Ещё раз`: who retries.** A sentence saying it retries by itself would be false for a parse error or an empty balance (section 5.2), and one sentence serves all causes. The toast `Читаю` answers on tap.
54. **`✓ 🔢 Цифры` could mean on or off.** The shipped tri-state of `numbers.py` (section 2.6), shared with the day card.
55. **`🗑 Удалить` beside `🗑 Из приёма` (`N1h`, `N2h`).** `🗑 Удалить приём` is over the 13-character budget at three per row. The two are on different screens, and `N6` gives the undo at once.
56. **`M1c` join button names no food.** The label is at its budget with the day, which section 2.9 ranks first, and the line above names the new dish.
57. **`O7` merges on one tap, `N5a` `↩️ Вернуть` is unclear about how far back.** Undo follows under the thumb; it undoes the last change, as section 2.5 says.
58. **The pager's `1/2` looks tappable; `M9b` «Новое меню внизу» is not visible.** A no-op cell, the shipped pattern. The keyboard arrives with the real message, and a mock cannot show it.
59. **`M8b` repeats the chips in the text.** The chips carry no day, and 2.9 requires it.
60. **`M1g` `≈ 14:02` is too exact for an approximation.** It is the forward's real clock, and `≈` says it may not be the meal's. Rounding would invent a value.
61. **A separate `🔒 Приватность` topic.** The readers found the privacy line hard to spot, so it now has a bold label; a topic of its own is a new row for a page already at its cap.
62. **`O9` accidental log, `M1h4`, `M1d`, `M1h2`, «Приём», `🎙 Текст`, `PMID`, `M14`, `O13` shipped labels, rice with no amount** repeated round 1's items 27, 29, 31, 33, 34, 39, 40, 42 without new evidence.
63. **`N6`, `N1`, `O1h` dates that differ between mocks, and `N6a`, `O13`, `O13a` picture placeholders.** Samples, severity 0 by the brief.

**Fresh eyes, round 3: changes that bend earlier decisions.**

64. **`M1`, `M2`, `M4`: `Фото в дневнике, читаю…`** (updates item 45). All four readers were unsure whether a photo in reading was already in the diary, because `записано` sat on both the pending line and the result. US-M1's wording stays bent, for the same reason.
65. **`В дневнике запись с этим фото.`** (updates item 44). All four asked a record of what. Same two lines of R35 above it, word for word.
66. **`M1b` `На этом всё.`** `Этого достаточно.` read as "that is enough food" to two readers: a shame risk on the one line every person sees once.
67. **`исходн…` is gone from the bot's copy** (the brief's known issue: rule 2.9 forbade it and `N2`, `M13` used it). `½` of what was asked by all four, so `N2`, `N2h` say `Считается от первой записи`. `а не их` read as a riddle, so `M13`, `M13h` say what stays.
68. **`M13` says what `➖ Отделить` does.** All four had to guess. Three lines is a lot, and `M13h` has only the first; this screen is opened on purpose.
69. **A button in a sentence carries its whole label** (rule in 2.9, swept: `M11`, `M13`, `M15b`, `O9`). `O9` now says `Нажатие на название`, which also narrows the accidental log of item 33 at no cost.
70. **`⬅️ Назад: сегодня`, not `⬅️ Карточка: сегодня`** (updates item 46). Three readers did not know the word in the bot's own sense, and two expected the day they had walked to. `Назад` keeps item 46's reason: it does not read as one more day. `📊 Карточка дня` is a shipped label and stays.
71. **Which day, which lists.** `N5c` names the day; `N5b`, `N5d` name it one sentence earlier, so `в этом дне` stays there. `O14` says which lists. `M5f` no longer says "no meal in the last day", which stung as a remark on not logging, and it gives the way forward: a reply to the record.
72. **`N6`, `O8`, `O11b`: `Вернуть можно и позже, пока эта кнопка на экране.`** All four asked how long. The sentence is what is true: the undo has no time limit, but after `⬅️` the button is gone, and a deleted meal shows in no list. **For the owner:** that is a real gap for a meal deleted by mistake and noticed later. `↩️ Убранное` (section 8) is still the later way back; it is left out of this round.
73. **Samples.** The omelet is ⭐ in `O2` and in `O9`, so `O3`, `O3a`, `O3h` tick it, and only `O3c` shows the empty star. `O2`'s first row is now not the largest count, because `12, 3, 2` read as "most eaten" against the title's `Сначала недавние`.
74. **`старая`, `разбивка`.** `Старая запись` felt lesser to three readers (rule in 2.9). `N1b`, `M13a` say what differs, `O3b`, `O6`, `M15a` say `ранние`. `M15a` drops `Блюдо хранится один раз…`, which is the database talking.
75. **`M4c` names the outside service once, `M15d` drops `платный`.** Three readers met the disclosure only if they opened the topic; sound leaving the project should not depend on that. `платный` made one reader ask whether they pay.
75a. **`M12` goes only to a person with meals from before this release.** `Теперь…` means nothing to a newcomer, who had no old flow; the keyboard reaches them with `/start`.

**Fresh eyes, round 3: findings rejected, and why.**

76. **`🔁 Ещё раз` on `M7`, `M7a`, `M4d`, `N1d`: who retries, and will it double?** New evidence: all four, on a label, where item 53 rejected a sentence. Not changed, because R35 fixes both button words (item 30). **For the owner:** `🔁 Прочитать заново` fits the budget and matches `M11`, `M13`; a one-word decision.
77. **`M1c`, `M1h4`: is it counted twice, can it be ignored?** All four guessed here, and one worried about a double count. A line on every join row breaks R35's one line that asks nothing, and a reassurance about tidying is itself about tidying. `M15b` carries the answer. Items 29, 62.
78. **`N5` `Вчера, 30 окт` counted from today.** All four stumbled and then read it right, because the date saves it. `На вчера, 30 окт` is 16 characters at three per row, budget 13.
79. **`M5d`, `M6a`: the bold question looks like a task.** Two readers. The title must say what is asked, and 2.9 puts the line that it can be left unanswered right under it. Item 37.
80. **`O3c`, `O4a`: `✓ 🔢 Цифры` over `Цифр пока нет`; `N2b` `Цифры не посчитаны`.** The tick is the state of the setting (item 54). A sentence saying nothing is wrong on `N2b` would raise what it denies, and rule 7 asks for this wording.
81. **`O1a` `➕ Приём в этот день` as a nudge.** It is the only way to add a meal to a past day and the only action on an empty day, and no text asks for it.
82. **`O1` chips carry no food, `O1h` `фото сохранено`.** Lines of what, chips of when, by rule; all four found the right meal (item 23).
83. **`N5c` and `N5d` mixed up, `O6` pager against 12, `O13a` which day comes first.** One reader each, or a sample.
84. **Repeats with no new evidence:** `➖ Отделить` has no undo (32, 52), `M1d`, `M1h2` 3000 ккал (31: numbers are opt-in, 2.6), `O9` accidental log (33, see 69), rice with no amount (34), `O2` no count (35), `M14`, `O13` shipped labels (39), «Приём» (40), `Черновик`, `M10` notice above the title (41, 2.5), `PMID` (42), pager (58), `M9b` (58), `M8b` (59), `M1g` (60), `🗑 Удалить` beside `🗑 Из приёма` (55), `🎙 Текст`, ticks (27, 54), `Ещё ›` hides join (22), mock look and glare (43), `N6a` and picture placeholders (63).

**Stories.** Slices N and O are unchanged. Where a decision bends them, this file says why. Slice M was added before slice N.

## 10. Build order and tests

Each slice ends at a pushed feature branch with `./scripts/test.sh` green, a local Postgres check of its migration, and a release note. Copy goes through `findings`, plus a by-eye pass for bare masculine verbs, which the test misses. Tests use real rows for filters (H11), turn on `PRAGMA foreign_keys` for FK behaviour (H4), and use a naive-datetime helper for SQLite read-back (H3). No test calls a paid API.

### 10.1 Slice N, first

Order: migration → `meal_items` writes at save → card, dish, grams, picker, time, removed → readers → topic. Screens: `N1`–`N7`, plus the bridge.

- Sum: an item change rewrites totals and every JSON field in one transaction, and day card and numbers agree.
- Legacy never recomputed: an edit elsewhere leaves a no-items meal's totals; the first `➕` keeps them to the kopeck.
- Plate: no quantity row, removal leaves totals, the notice says so, nothing split by share. One food gives `items`.
- Idempotence: the same `ml:q`, `ml:ts` twice gives one event and toast `Уже так`.
- Quantity: ½ after ×2 is half of base. Decimal × float does not raise.
- Undo guard: change, later change, old undo: no change and the notice, with a planted later change as the known positive.
- Past stays: an item keeps its name and numbers after a dish `upsert`; `numbers_by_user` survives `upsert`, with a control that an ordinary upsert does change.
- Filter: a deleted meal is absent in day, week, step, push, seed, export and retag, one test each, including the raw select.
- Time: landing on another day, future time refused, `Вчера, <date>` counted from today and not from the meal, its label naming the same date as the write, from the local date across a DST-free zone and across midnight, `day_only` out of `_moments`, `early_clock`, `_long_gap`, approx out of `early_clock`, `_recent_clock`.
- Stale: every verb on a deleted meal, a foreign id (nothing changes), malformed data.
- Rule 1: no model grams on `N1`, `N2` with numbers closed; with numbers open they appear.
- No clamp: a 3000 kcal number prints whole, with no added word.
- Layout: labels as lists, 4+1, 3 per row, 64 bytes at worst-case ids, every mark drawn. Every pager with more than one page shows its position.
- Copy: no bare `🗑 Убрать`; a line naming a meal carries its day; an estimated total carries `≈`; a saved-but-unread line says `В дневнике запись с этим …` and names its source; no bot line says `исходн…`; a button in a sentence carries its whole label; no back label `⬅️ Приём`, `⬅️ Сегодня`.
- Migration: structural test; single head.

### 10.2 Slice M, second

Order: migration → router with sources and lines → background and sweep → time words → reply, corrective words → failures → album → voice → join and separate → draft, sources, groups, edited → rollout, topics. Screens: `M1`–`M15`. Release note: `WHISPER_URL` optional.

- Born first: model and download patched to raise; meal and source exist, the reply is `M7`.
- Routing matrix through the real `create_bot_application()`, as the intake probe did: **no message type returns silence** apart from the stop list. Must not wrongly fire: «вчерашний суп» moves nothing; «ещё суп» after 4 h is a new meal; `72,5` and `13.20` stay weigh-ins; a routine name stays a tick; `Обед` from an old keyboard is no meal; `150` in a grams slot is grams and outside it a weight.
- A wait never eats food: an open numeric slot plus «суп» gives a meal and a closed slot; a restart in between keeps the slot (DB).
- Reply: to line, card and the person's own message; deleted target gives `M5a`; the original line is edited.
- Join round trip: join, join, separate the first; times, sources and totals of all restored, typed fixes kept.
- Late result: `rev` moved during reading: nothing overwritten, reading kept with `applied=false`.
- Sweep: a stuck `pending` becomes `failed` and its line edits. Replay after restart uses `file_id`. Retry cap holds, none while the balance is out, with a counter assert.
- Handler returns before the read ends: a slow fake does not hold a second update.
- Meter records inside the task, with a known-positive assert.
- Voice: local success; unreachable, refused, timeout each go to fallback; no speech (422) takes no paid call and stays `no_speech`; empty `WHISPER_URL` goes straight to fallback; extension matches bytes; `🎙 Текст` toggle; logs hold no transcript.
- Album: order of arrival, straggler re-reads, up to 10.
- Replayed update: unique source key gives one meal.
- Edited message: a real `edited_message` update through the handler.
- Draft: filter hides it everywhere; restart keeps it; `✅ Записать` shows it; never in totals before.
- Second helping: a new item, both sources, the word «Добавлено», no count in any string.
- Reader upkeep: `food_lists.seed`, `food_groups_retag.pending`, `/reprocess` do what section 3 says; `Не посчитано приёмов: 1`, and a failed-only day never reads 0 ккал.

### 10.3 Slice O, third

Order: migration with favourite backfill → day list and tabs → dishes → dish card, rename, numbers, history, merge, hide → favourites. Screens: `O1`–`O14`. Label tests for the day card, the settings hub and favourites change on purpose, each named in the commit.

- Day list: `≈`, `без времени`, duplicates `13:20 · 2`, `‹ ›` across a month, no `›` beyond today, back by origin kept while walking, photo↔text crossing.
- Merge: relink; undo weeks later with new meals on the target (`original_food_id`); item names and numbers unchanged, with a control that the dish did change; typed old name resolves to the target; ⭐ repoints or soft-removes.
- Rename: a taken name offers merge, no IntegrityError; run this once on Postgres, because SQLite passes without the unique index (H7). Cyrillic case in Python.
- Hide: gone from lists, picker, barcode and ANN; eating it again unhides.
- Numbers: `numbers_by_user` survives; future items use new numbers; past ones do not.
- Counts: live items plus equal legacy descriptions; `Было 12 раз или больше`; hidden at `минимум сил` on `O2h` and `O3h`; no `🕘 Когда было` with no meals.
- Favourites: backfill keeps every row and its numbers (`per_portion`); a tap logs at now with one item; undo is soft; double tap within 3 s; empty copy names a real button.
- Layout as in N.

## 11. What was checked

- VERIFIED by script on this file: every `tg`, `kb` and inline Russian string through the rules of `tests/test_user_text.py`, every callback at worst-case int4 ids against 64 bytes, every label against the budget, every emoji against `render/emoji/build_tiles.py`. The script's known-positive controls passed in the same run. Bare masculine verbs were listed and read by eye.
- The `st:` and `hw:` callbacks in `O13` and `M15` show the shape. The shipped values in `stats_screen` and `explain_screen` win. `ℹ️` is a shipped mark on an existing screen (`explain_screen`), absent from `build_tiles.py` when this was written; this spec adds no new use of it beyond naming that path.
- Fresh eyes round 1: 16 answers from four invented personas on rendered mocks, read as untrusted data. They are simulated readers, not people, so the findings are hypotheses. Not tested on a real person.
- Fresh eyes round 2: 16 answers from the same four invented personas, read as untrusted data. All control questions came back right. **No synthesised report existed when this round was applied (`fe/round2/report.md` was missing), so the triage above was made directly from the 16 answers and the brief; its severities are this file's, not a reviewer's.** Simulated readers, so hypotheses still.
- Fresh eyes round 3: 16 answers from the same four invented personas, read as untrusted data. All control questions came back right. **No synthesised report existed when this round was applied (`fe/round3/report.md` was missing), so the triage in items 64–84 was made directly from the 16 answers and the brief; its severities are this file's, not a reviewer's.** Simulated readers, so hypotheses still.
- Not checked: real label widths on a phone, silent-edit behaviour on a locked phone, real albums, the whisper service, PTB's `edited_message`, any uploaded emoji set, any paid call. PMIDs were not read in PubMed.
