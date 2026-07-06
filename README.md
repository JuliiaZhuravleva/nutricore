# Nutricore

Nutricore is an advanced Telegram bot for tracking nutrition and health metrics, using AI-powered food analysis and comprehensive health monitoring capabilities.

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-Latest-green)
![Docker](https://img.shields.io/badge/Docker-Latest-blue)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Latest-blue)
![Redis](https://img.shields.io/badge/Redis-Latest-red)

## Features

- **Food intake tracking** via text descriptions and photos
- **AI-powered nutrition analysis** using OpenAI's models
- **Body metrics tracking** with Mi Smart Scale integration
- **Activity monitoring** with Samsung Health integration
- **Comprehensive analytics** and reporting
- **Personalized diet recommendations** and health insights

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

- Python 3.9 or higher
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
   pip install -e .
   ```

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

## License

MIT

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request
