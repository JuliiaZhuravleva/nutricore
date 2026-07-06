# Product lookup for packaged food (optional accuracy upgrade)

> **Status: DRAFT spec (2026-07-06).** Optional enhancement to the photo meal-logging
> flow. Not scheduled into the staged plan yet — see ROADMAP "Enhancements".

## Problem

Today photo logging sends the image to OpenAI vision, which **estimates** foods and
macros from what it sees. For a **packaged** product that estimate is a guess, when the
exact КБЖУ is knowable — the packaging carries a barcode, a brand/name, and often the
nutrition table itself. We want an optional path that, when a real product is
identified, returns **that product's actual КБЖУ** instead of a vision guess.

## Scope

- **Optional / opt-in.** The default vision estimate stays. This path only kicks in for
  packaged products (barcode or clear packaging), behind a flag or an auto-trigger when a
  barcode/label is detected. Cooked food with no packaging → unchanged vision flow.
- **Personal tool.** Same audience and boundary as the rest of the bot (see
  `docs/stages/README.md`). No product-catalog UI, no multi-user concerns.

## Approaches, by confidence (highest first)

The core insight: **for the numbers, prefer a structured product database over an LLM's
web-search summary** — a nutrition app lives or dies on the КБЖУ being right, and free-text
web results are easy for a model to paraphrase wrong. So:

1. **Barcode → Open Food Facts.** ⭐ Most reliable.
   - Detect/decode the barcode (EAN/UPC) from the image — a barcode library
     (`pyzbar` / `zxing`) or ask the vision model to read the digits.
   - Look it up: `GET https://world.openfoodfacts.org/api/v2/product/{barcode}.json`
     (**no API key, no signup**, 3M+ products) → ingredients, per-100g/per-serving
     nutrition, Nutri-Score, name, image.
   - Deterministic, free, exact. Fails only if no barcode or product not in the DB.

2. **Packaging text → identify → structured nutrition.**
   - Vision reads brand + product name + weight, then either an Open Food Facts **name
     search** or the **OpenAI web_search tool** identifies the specific product.
   - Still prefer pulling the *numbers* from the structured DB (Open Food Facts) over the
     web-search prose.

3. **Read the label directly.** Many packages print the nutrition table — vision can just
   OCR it. Often enough on its own; no external call.

4. **Fallback: current vision estimate** (cooked food / no packaging). Already shipped.

Always **label the source/confidence** in the reply so the user knows how much to trust
the numbers, e.g. `по штрих-коду (точно)` · `нашли в базе (проверь)` · `оценка по фото`.

## Correction on "search by the picture"

OpenAI does **not** offer reverse-image search (find this exact image on the web) as a
tool. What exists is:
- **Vision** — the model *looks at* the image and can read text/barcodes on it.
- **web_search** — a built-in **text** search tool, available in the **Responses API**
  (not the Chat Completions API the bot uses today), returns answers with source
  citations and supports domain filtering.

So "search by image" in practice = vision extracts a text signal (name/barcode) → text
search. True reverse-image search would be a third-party service (Google Vision / SerpAPI)
and is out of scope.

## Design sketch

```
photo → vision (foods + macros, current)          ── always
      ├─ barcode detected? → Open Food Facts /product/{barcode}.json → product КБЖУ   [conf: high]
      ├─ else brand+name readable? → OFF name search / OpenAI web_search → product     [conf: medium]
      │                                       → pull numbers from OFF where possible
      └─ else → vision estimate                                                        [conf: low]
reply: nutrition + a source/confidence badge; save to meals with the source recorded
```

- **Cache** resolved products (barcode / normalized name → КБЖУ) in a small table so
  repeat scans don't re-hit the API.
- **Record the source** on the `meals` row (barcode / web / vision) alongside `ai_analysis`,
  so history shows how each entry's numbers were derived (pairs with TD-009).

## Implementation notes

- **web_search requires the Responses API.** The bot's OpenAI calls are `chat.completions`
  today; using web_search means migrating (at least that call) to the Responses API — plus
  higher cost/latency. Gate it behind the opt-in flag; barcode→OFF needs no OpenAI change.
- **Barcode decode** is a small dependency (`pyzbar` needs the system `zbar` lib; `zxing`
  is pure-ish). Or lean on vision to read the EAN digits and skip the lib.
- **Open Food Facts**: free, no key; be a good citizen — set a `User-Agent`, cache, and
  handle "product not found" gracefully. Data is openly licensed.
- **Confidence must surface.** Never present a web-search number with the same certainty
  as a barcode-DB number.

## Data & privacy

- Product КБЖУ is public data (fine to cache). The user's photo/description is personal
  content — retention/deletion handled by the general inbound-persistence work (TD-009),
  not re-solved here.

## Open questions

- Trigger: explicit `/scan`-style command, an inline "🔍 найти продукт" button on the
  photo confirmation, or auto when a barcode is detected?
- If OFF returns per-100g but the user ate a portion — reuse the existing portion-estimate
  step to scale.
- Which product DB(s) beyond Open Food Facts (USDA FoodData Central for US, regional DBs)?
- Do we ever trust a pure web_search number, or only use it to *find* the product and then
  require a structured source for the numbers?

## Relation to other work

- **TD-009** (persist inbound + reprocess) — the storage/history substrate this rides on.
- **Stage 2/4** (`docs/stages/`) — meal capture accuracy + coaching; this is an accuracy
  upgrade to capture.
- **`ai_call_logs`** — extend logging to record which lookup path served each entry.

## Sources

- OpenAI — Web search tool (Responses API): https://developers.openai.com/api/docs/guides/tools-web-search
- OpenAI — New tools for building agents: https://openai.com/index/new-tools-for-building-agents/
- Open Food Facts API — tutorial: https://openfoodfacts.github.io/openfoodfacts-server/api/tutorial-off-api/
- Open Food Facts — Data, API & SDKs: https://world.openfoodfacts.org/data
