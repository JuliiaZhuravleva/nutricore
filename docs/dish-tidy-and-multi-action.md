# Dish tidy-up and several actions in one message — design (draft, 2026-11-13)

> **Status:** proposal, waiting for the owner's answers (§5). Nothing built.
> Two features that share one core: **dish operations callable without a tap**
> (merge, star, rename, hide) with **one undo for a group of them**.
>
> - **A. 🍲 Порядок в блюдах**: a weekly pass over the person's dishes that merges the
>   near-identical ones by itself, proposes likely duplicates and frequent dishes for ⭐,
>   and takes the whole batch, or any part of it, in **one tap**.
> - **B. Several actions in one message**: «на завтрак овсянка, в обед суп, вечером
>   сырники; а сырки глазированные — это одно блюдо, и добавь гречку в избранное» →
>   three meals, one merge, one ⭐.
>
> Borrowed from sweet-home (private repo): the command-line grammar (its ADR-0018), the
> "edits at once, undo after" rule, and the weekly tidy batch. Its known weak spot (to
> refuse one item you must step through all of them) is what A's checkbox screen fixes.

## 0. What exists (verified 2026-11-13)

- `app/services/dishes.py`: manual merge `_merge` (moves live meal items and ⭐ to the
  target, sets `merged_into_id`, the old name keeps finding the dish, a `food_merge`
  `RecordEvent`, and `_unmerge` undoes only what this merge moved), star `_v_star`,
  rename, hide, all bound to a tap context `_Tap`.
- `personal_foods` + `personal_food_embeddings` (pgvector, ADR-0003): every dish
  already has an embedding, so similarity costs no model call.
- `app/services/week_push.py` + the beat entry `week-summaries`: a per-user-clock weekly
  send, marked before sending. The pattern to copy.
- `app/services/diary_widget.py`: a widget sent with `sendWidget`, a text twin, and a
  kill switch. Its taps do **not** reach the bot yet: `widget_query` handling is new
  work (skill `telegram-widgets` §4).
- Text routing: `capture.route_text` makes every plain text one meal (after the reply,
  weight, tick, time and correction checks), read by `openai_service.analyze_food_entry`
  (JSON, with the nearest saved dishes in the prompt).
- Real data (prod, read-only, checked 2026-11-13): **hardly any pair is "obviously the
  same"**. Most look-alikes differ in brand, size or fat (two flavours of one snack, 15 %
  vs 26 %), and the real duplicates differ in words (word order, a brand name vs its
  generic). So the automatic tier must be narrow, and the proposals do the work.

## 1. Core: `dish_ops` (slice 1)

Move the write halves out of `dishes.py` into `app/services/dish_ops.py`, free of
`_Tap`/Telegram: `merge(db, user, source, target, group=None)`, `unmerge(event)`,
`star(db, user, dish, group=None)`, `unstar`, `rename`, `hide`. `dishes.py` calls them
(behaviour-preserving; its tests stay green unchanged).

`RecordEvent` gets a nullable **`group_id`** (uuid string, indexed). One batch or one
message's changes share it; **↩️ Вернуть всё** undoes the group's events newest-first,
each through its own undo. An undo is refused for an item changed since (as `_unmerge`
already does) and named in the answer: «Не вернулось: «X», его меняли после».

## 2. A. 🍲 Порядок в блюдах

### 2.1 When

Beat task `dish-tidy` every 15 min, like `week-summaries`; it checks who is due.
**Default: Sunday 12:00 on the person's clock, silent**, not with the 22:00 summary
(one message about one thing). Settings: «раз в неделю / раз в две недели / выключено».
On demand: «🍲 Порядок» beside «🔍 Найти» on the first page of the 🍲 Блюда list. **Nothing found → nothing sent.**

### 2.2 Finding candidates (no model call)

Per user, over live dishes (not merged, not hidden):

| Tier | Rule | What happens |
|---|---|---|
| **Same** | equal after normalising: casefold, ё→е, quotes/punctuation/brackets dropped, amounts dropped («40 г», «39g», «82 g», «1 шт»), a leading «это» dropped, word order ignored; **and** per-100 g kcal within 10 % (or one side has none) | merged **by itself** before the send, listed with undo |
| **Likely** | embedding cosine distance under a threshold (calibrate on the owner's data: ASSUMED ≈ 0.25), **or** the stems of the shorter name all found in the longer (`_share_a_word` logic); **and** kcal per 100 g within 30 %, and different `%`/size numbers never pair | **proposed**, ticked by default |
| **⭐ Frequent** | after the merges: eaten ≥ 3 times in the last 28 days, not starred | **proposed**, ticked by default |

- Pairs become **clusters** (A~B, B~C → one group, one target).
- **Target** = most meal items, then numbers a person typed, then the shorter clean name.
  The other names stay as names that find the dish (existing behaviour).
- **Declines are remembered**: an unticked pair or ⭐ is not offered again for 90 days,
  or until one of the dishes is renamed or its numbers change.
- Caps: 10 proposals per batch (the most-eaten first); the rest wait for next week.
- A later option (not v1): one cheap model call to judge the Likely pairs only.

### 2.3 The screen: one widget, one tap

```
🍲 Порядок в блюдах · 13 ноября
Объединено само (1)
  ✓ «рис  белый» → «рис белый»                       ↩
Похоже на одно блюдо
  ☑ «томатный соус» → «соус томатный»
  ☑ «йогурт Бренд 2%» → «йогурт 2%»
В избранное?
  ☑ гречка · 5 раз за месяц
  ☐ яблоко · 3 раза за месяц
[ Применить отмеченное (3) ]
Предложения действуют до 20 ноября
```

- The dictionary (names, order) is baked into the HTML per send; the state holds
  `{r, mask, done}` (≤ 1 KB). Ticking and unticking change only the widget's own state.
  The apply tap sends **one** request `a:<rev>:<mask hex>` (≤ 64 B). The bot writes,
  answers with the new state, then edits `chat_data`; ✓ shows only after the answer
  (skill pattern 3). Unticked = declined (remembered).
- After apply: the ended look. ✓ applied, — declined, the counts kept. Under the widget
  (inline keyboard): `✅ ОК` first (owner's rule, `close_row`), `↩️ Вернуть всё`.
- Lifetime 7 days; the next batch retires the previous one (`x: 1`).
- **Text twin** (widgets off, Desktop/Telegram X, refused send, too big). The same batch
  as a **checkbox keyboard**: one row per item `☑ томатный соус → соус…`, a tap
  flips ☑/☐ by editing the markup only (no write), then `[Применить отмеченное]`,
  `[💤 Не сейчас]`. One extra tap per refused item, never a walk through all items.
- Storage: `tidy_batches(id, user_id, created_at, expires_at, status, chat_id,
  message_id, group_id)` and `tidy_items(id, batch_id, kind auto|merge|star,
  food_ids JSON, target_id, score, state proposed|applied|declined|undone|stale)`.
  Callback data carries the batch id only. At apply time every item is checked again on
  the live rows; a changed one becomes `stale` and is named, the rest apply.

## 3. B. Several actions in one message

### 3.1 When it is used

A deterministic `wants_plan(text)` ahead of NEW_MEAL in `route_text` (after the reply,
weight, tick, time and correction paths, which stay as they are). It fires on:
- a dish verb: объедини / одно блюдо / одно и то же / в избранное / из избранного /
  переименуй / убери из блюд;
- **two or more meal markers**: завтрак, обед, ужин, перекус, полдник, утром, днём,
  вечером, ночью, «в 13:00»; or several lines/list items with food;
- «за день / сегодня ел(а) / вчера ел(а)» with more than one meal.

Otherwise nothing changes: one message is one meal, the same read as today. Voice: the
same router runs on the transcript.

### 3.2 The planner: command lines, not JSON (sweet-home ADR-0018)

One model call (task `plan`, `max_tokens` ≈ 400, temperature 0) writes one command per
line. One registry in `app/services/plan_grammar.py` builds both the prompt's command
docs and the parser, so they cannot drift apart. The parser ignores prose, bullets and
fences; `none` means nothing fits.

```
meal when="завтрак" "овсянка, яблоко"
meal when="13:30" "суп, хлеб"
merge #12 #31 into #7
star #4
unstar #9
rename #15 "орехи жареные"
hide #22
ask "…"            # a question back to the person instead of guessing
```

- The prompt gets the person's live dishes, numbered (`#n "name" (also: aliases)`),
  the ones the words hit first (stems + ANN, as `saved_dishes.candidates`), capped
  at 150; and the date and time on their clock.
- **Strict resolution**: `#n` must exist and belong to the person. A dish verb whose
  words fit several dishes equally → **buttons to choose** (choose all / pick), never a
  guess. A refused line goes back to the model **once** with the nearest dishes.
- **Caps**: 8 meals and 10 dish changes per message. Over the cap the extra lines are
  refused with a reason («Больше 8 приёмов за раз не записываю, остальное пришли
  отдельно»), never cut silently.

### 3.3 Running it: at once, undo after

The bot's existing rule (meals are written at once, no confirmation; sweet-home's
`edits-at-once` too):
1. **Meals first**, so a failure further on loses no food. Each `meal` line becomes a
   meal of its own through the existing NEW_MEAL path with **its own words** and its
   time (`meal_words.time_words`). Each gets its usual line message with its usual
   buttons («читаю…» → the answer), so ✏️/🕐/➖ work on it as on any meal.
2. **Dish changes** through `dish_ops` with one `group_id`. **One** reply lists them:
   «🔗 «сырок …» теперь в «сырок …» · ⭐ гречка», with `✅ ОК` and `↩️ Вернуть всё`.
   A failed line: «Не вышло: «X». Остальное сделано.»
3. Questions (`ask`, several fitting dishes) come last, as buttons.

- **Idempotent**: the inbound message id is the key (an edited or re-sent message does
  not plan twice); taps take the row first, so a double tap is a no-op.
- **Cost**: one planner call (a cheap model, ≈ 2–3 k input tokens with the dish list) plus
  the meal reads that would happen anyway. Logged as `ai_call_logs.kind="plan"`. Tests
  use fakes only.

## 4. Slices

| # | Slice | Model calls | Depends on |
|---|---|---|---|
| 1 | `dish_ops` + `RecordEvent.group_id` + «Вернуть всё» | none | — |
| 2 | Tidy: finder, tables, weekly task, setting, **text checkbox screen** | none | 1 |
| 3 | Tidy widget + `widget_query` handling (reused later) | none | 2 |
| 4 | Multi-action: grammar, parser, router, dish commands | 1 per such message | 1 |
| 5 | Day split: several `meal` lines → several meals | 1 + reads | 4 |

Each slice: own branch, `./scripts/test.sh` + the Postgres cases (merge and pgvector
queries), review, release via `nutricore-release`. Slice 2 alone already makes the
weekly batch usable. Slice 3 adds the one-tap widget on top.

## 5. The owner's answers (2026-11-13)

1. **Automatic merges**: only near-identical names. Likely pairs are proposed, ticked;
   a different size is proposed unticked; a different fat % never pairs.
2. **When**: Sunday 12:00 on the person's own clock (sent in the first sweep from
   12:00 to 20:59), silently.
3. **Dish changes from words** (slices 4–5): at once, with «↩️ Вернуть всё».
4. **Order**: the tidy-up first (slices 1→2→3), then several actions (4→5).
