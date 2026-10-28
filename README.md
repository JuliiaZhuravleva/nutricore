# Nutricore

Nutricore is a **personal** Telegram bot for tracking nutrition — capture meals with minimal friction (text or photo → calories + macros via OpenAI) and chat about food. It is a single-user tool (public here as a portfolio piece), **not** a multi-user product.

**Ecosystem boundary:** nutricore is the *capture + chat surface* of a hub-and-spoke setup — a separate project, `my-health`, owns all medical data and reasoning. Food questions use nutricore's own OpenAI; health/medical questions relay to the hub via `/consult`. No medical logic or medical data lives in the bot.

![Python](https://img.shields.io/badge/Python-3.12%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-Latest-green)
![Docker](https://img.shields.io/badge/Docker-Latest-blue)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Latest-blue)
![Redis](https://img.shields.io/badge/Redis-Latest-red)

## Features

**Shipped & working:**

- **Meal logging** — text or photo → foods + calories/macros, confirm to save. A photo's caption is part of the input: a brand, a sauce the camera cannot see, or "I only ate a third of this" changes the recorded numbers. The photo path is hardened (base64 to OpenAI, atomic draft, retries) and self-heals against model deprecation (the owner gets an alert and picks a new model for the failed task in chat; the choice persists).
- **Fix it under the reply** — the analysis comes back with buttons: a photo's portion as «Маленькая / Средняя / Большая» with their grams, «✏️ Поправить вес» (type the grams), «✏️ Поправить продукт» (type what it really is — the same photo is re-read with that note, no resend), «✅ Сохранить». An estimate shows as one («≈ 400 ккал (320–480)»), food names come in Russian, and a button of an older reply only says it is out of date.
- **What it cost** — «💸 Стоимость запросов» in ⚙️ Настройки (off by default) adds a line under each analysis: «💸 Запрос: $0.0004 · 1 вызов ИИ». It is an estimate from the token counts and OpenAI's price list (OpenRouter's own reported cost when the call went there); a model without a known price is named, never guessed. «✖️ Отмена» under the reply, tapped or typed, drops the meal and returns to the menu; «Нет» starts the entry over.
- **Favorites** — "⭐ И в избранное" under the reply saves the dish (named by the photo's caption when there is one); "⭐ Избранное" in the menu lists them, and a tap on a dish plus "when" logs it again with the stored numbers — no new analysis, with an "↩️ Отменить" under the record. Removing a favorite can be undone the same way.
- **Packaged-food lookup** — a barcode or product name read from the photo → the product's *actual* КБЖУ from [Open Food Facts](https://world.openfoodfacts.org/), then a label-OCR and a web-search fallback, and finally the vision estimate — each with a transparent source/confidence badge. A macro the source does not carry shows as "—", never as a confident 0.
- **Models per task, any company** — each task (photo, text, barcode, label) can run on its own model, from OpenAI directly or from Anthropic, Google, Qwen and others through [OpenRouter](https://openrouter.ai/) (always with provider data collection denied). A weekly check asks each company for shutdown dates of the models in use and messages the owner once per news; `/models` shows the state and checks now. See [ADR-0004](docs/decisions/ADR-0004-model-providers-and-lifecycle-watch.md).
- **Access control** — open / whitelist / closed modes with a silent gate.
- **Secured REST API** (`X-API-Token`, fail-closed) + Telegram webhook secret.
- **`/consult` relay** to the my-health hub — medical questions never touch the bot's own AI.

**Planned** (see [ROADMAP.md](ROADMAP.md)): goals & remaining-budget replies, statistics, weight tracking, reminders/digests, AI coaching. The domain models/CRUD/REST exist; the "intelligence" layer is staged, not built yet.

## Project Structure

```
nutricore/
├── app/                   # Main application code
│   ├── api/v1/            # API endpoints
│   ├── core/              # Core configuration
│   ├── crud/              # Database operations
│   ├── db/                # Database setup and session
│   ├── models/            # SQLAlchemy models
│   ├── schemas/           # Pydantic schemas
│   ├── services/          # Business logic
│   └── utils/             # Utility functions
├── celery_app/            # Celery async tasks
├── alembic/               # Database migrations
├── tests/                 # Unit and integration tests
└── nginx/                 # Nginx configuration
```

## Prerequisites

- Python 3.12 or higher
- Docker and Docker Compose
- PostgreSQL
- Redis
- OpenAI API key
- Telegram Bot token

## Getting Started

### Setting Up Environment

1. Clone this repository
   ```bash
   git clone https://github.com/yourusername/nutricore.git
   cd nutricore
   ```

2. Create and configure environment variables
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

3. Start the application using Docker Compose
   ```bash
   docker-compose up -d
   ```

### Configuration

Edit the `.env` file with your specific settings (see `.env.example`):

- Database credentials
- Telegram Bot token
- OpenAI API key
- Models — `OPENAI_MODEL` (default), optional per-task `MODEL_VISION/TEXT/BARCODE/LABEL_OCR`, optional `OPENROUTER_API_KEY`; the weekly model check — `MODEL_WATCH_ENABLED`, `MODEL_WATCH_WARN_DAYS`
- Admin settings
- Custom emoji (optional) — `CUSTOM_EMOJI=set1,set2`: custom-emoji sticker set names; the bot draws its marks with them (needs the bot owner's Telegram Premium). Unset: plain emoji
- Access control — `BOT_ACCESS_MODE` (open/whitelist/closed) + `ALLOWED_TELEGRAM_IDS`
- REST API auth — `API_TOKEN` (the API is disabled until this is set)
- `my-health` consult relay — `MYHEALTH_CONSULT_URL` + `CONSULT_TOKEN`
- Environment-specific configurations

## Development

### Local Development Setup

1. Create a virtual environment
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

2. Install dependencies
   ```bash
   poetry install
   ```
   (See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contributor workflow.)

3. Run the FastAPI application
   ```bash
   uvicorn app.main:app --reload
   ```

### Running Tests

```bash
pytest
```

### Database Migrations

Create a new migration:
```bash
alembic revision --autogenerate -m "description"
```

Apply migrations:
```bash
alembic upgrade head
```

## Deployment

The application is designed to be deployed using Docker Compose:

```bash
docker-compose -f docker-compose.yml up -d
```

For production environments, additional configuration is recommended:
- Set up proper SSL certificates
- Configure Nginx for reverse proxy 
- Set `DEBUG=false` in your environment

## Documentation

- **[docs/README.md](docs/README.md)** — documentation index (start here)
- **[ROADMAP.md](ROADMAP.md)** — direction, current status, and what's deliberately *not* built
- **[docs/product-philosophy.md](docs/product-philosophy.md)** — the principles behind product decisions
- **[CONTRIBUTING.md](CONTRIBUTING.md)** · **[SECURITY.md](SECURITY.md)**

## License

MIT

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request
