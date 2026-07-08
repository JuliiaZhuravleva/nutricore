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

- **Meal logging** — text or photo → foods + calories/macros, confirm to save. The photo path is hardened (base64 to OpenAI, atomic draft, retries) and self-heals against OpenAI model deprecation (the owner picks a new model in chat; the choice persists).
- **Packaged-food lookup** — a barcode or product name read from the photo → the product's *actual* КБЖУ from [Open Food Facts](https://world.openfoodfacts.org/), falling back to the vision estimate, with a transparent source/confidence badge.
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
- Admin settings
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
