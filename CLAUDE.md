# CLAUDE.md - AI Assistant Guide for Nutricore

This document provides comprehensive guidance for AI assistants working on the Nutricore codebase. It covers architecture, conventions, workflows, and best practices specific to this project.

## Project Overview

Nutricore is an advanced Telegram bot for tracking nutrition and health metrics, using AI-powered food analysis and comprehensive health monitoring capabilities. The application combines:
- **FastAPI** backend for REST API endpoints
- **Telegram Bot** for user interaction
- **OpenAI** for food analysis and health insights
- **PostgreSQL** for persistent data storage
- **Redis** + **Celery** for asynchronous task processing

## Repository Structure

```
nutricore/
├── app/                        # Main application code
│   ├── api/v1/                 # API endpoints (versioned)
│   │   ├── users.py            # User management endpoints
│   │   ├── meals.py            # Meal tracking endpoints
│   │   ├── body_metrics.py     # Body metrics endpoints
│   │   ├── activities.py       # Activity tracking endpoints
│   │   └── analysis_reports.py # Analytics endpoints
│   ├── core/                   # Core configuration
│   │   ├── config.py           # Settings and environment configuration
│   │   ├── security.py         # Security utilities (JWT, auth)
│   │   └── deps.py             # FastAPI dependencies
│   ├── crud/                   # Database CRUD operations
│   │   ├── crud_user.py        # User CRUD operations
│   │   ├── crud_meal.py        # Meal CRUD operations
│   │   └── ...                 # Other CRUD modules
│   ├── db/                     # Database setup
│   │   ├── session.py          # Database session management
│   │   ├── base.py             # Base imports for models
│   │   └── base_class.py       # Base model class
│   ├── models/                 # SQLAlchemy ORM models
│   │   ├── user.py             # User model
│   │   ├── meal.py             # Meal model
│   │   ├── body_metric.py      # Body metrics model
│   │   ├── activity.py         # Activity model
│   │   └── ...                 # Other models
│   ├── schemas/                # Pydantic schemas for validation
│   │   ├── user.py             # User schemas (Create, Update, Response)
│   │   ├── meal.py             # Meal schemas
│   │   └── ...                 # Other schemas
│   ├── services/               # Business logic layer
│   │   ├── openai_service.py   # OpenAI integration
│   │   ├── telegram.py         # Telegram bot handlers
│   │   └── analysis.py         # Analysis and reporting
│   ├── utils/                  # Utility functions
│   │   └── helpers.py          # Helper functions
│   └── main.py                 # FastAPI application entry point
├── celery_app/                 # Celery configuration and tasks
│   ├── celery_app.py           # Celery app setup + beat_schedule
│   └── tasks/                  # Task package
│       └── periodic.py         # Scheduled maintenance tasks (e.g. log purge)
├── alembic/                    # Database migrations
│   ├── versions/               # Migration scripts
│   └── env.py                  # Alembic environment config
├── tests/                      # Test suite
│   ├── conftest.py             # Pytest configuration and fixtures
│   ├── test_crud_user.py       # User CRUD tests
│   ├── test_crud_meal.py       # Meal CRUD tests
│   └── ...                     # Other test files
├── nginx/                      # Nginx configuration (for production)
├── bot.py                      # Telegram bot entry point (polling mode)
├── docker-compose.yml          # Docker orchestration
├── Dockerfile                  # Application container definition
├── pyproject.toml              # Poetry dependencies and project metadata
├── alembic.ini                 # Alembic configuration
├── .env.example                # Environment variable template
└── README.md                   # Project documentation

```

## Technology Stack

### Core Technologies
- **Python 3.12+**: Primary programming language
- **FastAPI 0.115+**: Web framework for REST API
- **SQLAlchemy 2.0+**: ORM for database operations
- **Pydantic 2.10+**: Data validation and settings management
- **Alembic 1.14+**: Database migration tool

### Data Storage
- **PostgreSQL 15**: Primary database
- **Redis 7**: Cache and message broker

### Async Processing
- **Celery 5.4+**: Distributed task queue
- **Celery Beat**: Periodic task scheduler

### External Services
- **python-telegram-bot 21.9+**: Telegram bot framework
- **OpenAI 1.12+**: AI-powered food analysis and insights
- **httpx 0.28+**: Async HTTP client

### Development Tools
- **pytest 8.3+**: Testing framework
- **black 24.10+**: Code formatter
- **isort 5.13+**: Import organizer
- **flake8 7.1+**: Linting tool
- **Poetry**: Dependency management

## Architecture Patterns

### Layered Architecture

The application follows a strict layered architecture to maintain separation of concerns:

1. **Presentation Layer** (`app/api/v1/`)
   - FastAPI route handlers
   - Request/response handling
   - Input validation via Pydantic schemas
   - Minimal business logic

2. **Service Layer** (`app/services/`)
   - Business logic implementation
   - External service integration (OpenAI, Telegram)
   - Complex operations and orchestration
   - Data transformation

3. **Data Access Layer** (`app/crud/`)
   - Database operations (CRUD)
   - Query optimization
   - Data retrieval and persistence
   - No business logic

4. **Model Layer** (`app/models/`)
   - SQLAlchemy ORM models
   - Database schema definition
   - Relationships and constraints

5. **Schema Layer** (`app/schemas/`)
   - Pydantic models for validation
   - API request/response contracts
   - Data transfer objects (DTOs)

### Design Patterns

#### CRUD Pattern
Each model has a corresponding CRUD class with standard operations:

```python
class CRUDUser:
    def get(self, db: Session, user_id: int) -> Optional[User]
    def get_by_telegram_id(self, db: Session, telegram_id: int) -> Optional[User]
    def create(self, db: Session, *, obj_in: UserCreate) -> User
    def update(self, db: Session, db_obj: User, obj_in: UserUpdate) -> User
    def remove(self, db: Session, user_id: int) -> User

crud_user = CRUDUser()  # Singleton instance
```

#### Schema Pattern
Three-tier schema structure for each model:

1. **Base Schema**: Common fields
2. **Create Schema**: Fields required for creation
3. **Update Schema**: Optional fields for updates
4. **InDBBase Schema**: Database representation with id, timestamps
5. **Response Schema**: API response model

```python
class UserBase(BaseModel):
    telegram_id: int
    username: Optional[str] = None

class UserCreate(UserBase):
    pass

class UserUpdate(BaseModel):
    username: Optional[str] = None
    diet_preferences: Optional[Dict[str, Any]] = None

class UserInDBBase(UserBase):
    id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

class User(UserInDBBase):
    pass
```

#### Dependency Injection
FastAPI's dependency injection system is used throughout:

```python
from app.db.session import get_db

@router.get("/users/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    return crud_user.get(db, user_id)
```

## Code Conventions

### Python Style
- **Formatter**: Black (line length: 88 characters)
- **Import order**: isort (imports organized by standard, third-party, local)
- **Linting**: flake8 for code quality
- **Type hints**: Use type hints for function signatures

### Naming Conventions
- **Files**: Snake case (e.g., `crud_user.py`, `openai_service.py`)
- **Classes**: PascalCase (e.g., `User`, `CRUDUser`, `OpenAIService`)
- **Functions/Methods**: Snake case (e.g., `get_user`, `create_meal`)
- **Variables**: Snake case (e.g., `user_id`, `db_session`)
- **Constants**: UPPER_SNAKE_CASE (e.g., `OPENAI_API_KEY`, `MAX_TOKENS`)
- **Private methods**: Prefix with underscore (e.g., `_internal_helper`)

### Database Conventions
- **Table names**: Plural, snake case (e.g., `users`, `body_metrics`)
- **Column names**: Snake case (e.g., `telegram_id`, `created_at`)
- **Timestamps**: Always include `created_at` and `updated_at`
- **Foreign keys**: Format: `{table_singular}_id` (e.g., `user_id`)

### API Conventions
- **Versioning**: All endpoints under `/api/v1/`
- **Resource naming**: Plural nouns (e.g., `/api/v1/users`, `/api/v1/meals`)
- **HTTP methods**: Standard REST verbs (GET, POST, PUT, DELETE)
- **Response format**: JSON with Pydantic schemas
- **Error handling**: HTTPException with appropriate status codes

### File Organization
- **One class per file** in CRUD and service modules
- **Related endpoints** grouped in single API router file
- **Imports ordered**: Standard library → Third-party → Local
- **Docstrings**: Use for classes and complex functions

## Development Workflows

### Setting Up Development Environment

1. **Clone and navigate to repository**
   ```bash
   git clone <repository-url>
   cd nutricore
   ```

2. **Create virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies with Poetry**
   ```bash
   pip install poetry
   poetry install
   ```

4. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

5. **Start services with Docker Compose**
   ```bash
   docker-compose up -d db redis
   ```

6. **Run database migrations**
   ```bash
   alembic upgrade head
   ```

7. **Start development server**
   ```bash
   uvicorn app.main:app --reload
   ```

8. **Start Telegram bot (separate terminal)**
   ```bash
   python bot.py
   ```

### Database Migrations

When modifying models:

1. **Make changes to models** in `app/models/`

2. **Generate migration**
   ```bash
   alembic revision --autogenerate -m "descriptive message"
   ```

3. **Review generated migration** in `alembic/versions/`

4. **Apply migration**
   ```bash
   alembic upgrade head
   ```

5. **Rollback if needed**
   ```bash
   alembic downgrade -1  # Go back one version
   ```

### Testing Workflow

1. **Run all tests**
   ```bash
   pytest
   ```

2. **Run specific test file**
   ```bash
   pytest tests/test_crud_user.py
   ```

3. **Run with coverage**
   ```bash
   pytest --cov=app --cov-report=html
   ```

4. **Test structure**: Each CRUD module should have corresponding tests
   - Use `db_session` fixture from `conftest.py`
   - Test all CRUD operations (Create, Read, Update, Delete)
   - Test edge cases and error conditions

### Code Quality Checks

Run before committing:

```bash
# Format code
black app/ tests/

# Sort imports
isort app/ tests/

# Lint code
flake8 app/ tests/

# Run tests
pytest
```

### Docker Development

Full environment with Docker Compose:

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f telegram_bot

# Rebuild after changes
docker-compose up -d --build

# Stop all services
docker-compose down
```

Services in docker-compose:
- `app`: FastAPI application (port 8000)
- `db`: PostgreSQL database (port 5432)
- `redis`: Redis cache (port 6379)
- `celery_worker`: Celery worker for async tasks
- `celery_beat`: Celery beat scheduler
- `telegram_bot`: Telegram bot polling

## Working with Key Components

### Adding a New Model

1. **Create model** in `app/models/new_model.py`:
   ```python
   from sqlalchemy import Column, Integer, String, ForeignKey
   from sqlalchemy.orm import relationship
   from app.db.base_class import Base, BaseClass

   class NewModel(Base, BaseClass):
       __tablename__ = "new_models"

       id = Column(Integer, primary_key=True, index=True)
       name = Column(String, nullable=False)
       user_id = Column(Integer, ForeignKey("users.id"))

       user = relationship("User", back_populates="new_models")
   ```

2. **Create schemas** in `app/schemas/new_model.py`:
   ```python
   from pydantic import BaseModel
   from datetime import datetime

   class NewModelBase(BaseModel):
       name: str

   class NewModelCreate(NewModelBase):
       pass

   class NewModelUpdate(BaseModel):
       name: Optional[str] = None

   class NewModel(NewModelBase):
       id: int
       user_id: int
       created_at: datetime
       updated_at: datetime
       model_config = ConfigDict(from_attributes=True)
   ```

3. **Create CRUD operations** in `app/crud/crud_new_model.py`:
   ```python
   from sqlalchemy.orm import Session
   from sqlalchemy import select
   from app.models.new_model import NewModel
   from app.schemas.new_model import NewModelCreate, NewModelUpdate

   class CRUDNewModel:
       def get(self, db: Session, id: int) -> Optional[NewModel]:
           stmt = select(NewModel).where(NewModel.id == id)
           return db.execute(stmt).scalar_one_or_none()

       def create(self, db: Session, obj_in: NewModelCreate, user_id: int) -> NewModel:
           db_obj = NewModel(**obj_in.model_dump(), user_id=user_id)
           db.add(db_obj)
           db.commit()
           db.refresh(db_obj)
           return db_obj

       # ... other CRUD methods

   crud_new_model = CRUDNewModel()
   ```

4. **Create API router** in `app/api/v1/new_models.py`:
   ```python
   from fastapi import APIRouter, Depends, HTTPException
   from sqlalchemy.orm import Session
   from app.db.session import get_db
   from app.schemas.new_model import NewModel, NewModelCreate
   from app.crud.crud_new_model import crud_new_model

   router = APIRouter()

   @router.post("/", response_model=NewModel)
   def create_item(item_in: NewModelCreate, user_id: int, db: Session = Depends(get_db)):
       return crud_new_model.create(db, item_in, user_id)
   ```

5. **Register router** in `app/main.py`:
   ```python
   from app.api.v1.new_models import router as new_models_router
   app.include_router(new_models_router, prefix="/api/v1/new-models", tags=["new_models"])
   ```

6. **Generate and apply migration**:
   ```bash
   alembic revision --autogenerate -m "add new_model table"
   alembic upgrade head
   ```

### Working with OpenAI Service

The OpenAI service is located in `app/services/openai_service.py`:

```python
from app.services.openai_service import OpenAIService

# Initialize service
openai_service = OpenAIService()

# Analyze food text
result = await openai_service.analyze_food_entry("chicken breast 200g")

# Analyze food image
result = await openai_service.analyze_food_image(image_url)

# Generate health insights
insights = await openai_service.generate_health_insights(user_data)
```

### Working with Telegram Bot

Bot handlers are in `app/services/telegram.py`. To add new handlers:

```python
from telegram import Update
from telegram.ext import CommandHandler, MessageHandler, filters

async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /newcommand."""
    await update.message.reply_text("Response")

# Register in create_bot_application()
application.add_handler(CommandHandler("newcommand", new_command))
```

### Working with Celery Tasks

Tasks live in the `celery_app/tasks/` package (e.g. `celery_app/tasks/periodic.py`).
Add a module there and list it in `celery_app.py`'s `include=[...]`. Scheduled tasks
also get an entry in `app.conf.beat_schedule`.

```python
# celery_app/tasks/periodic.py
from celery_app.celery_app import app

@app.task
def process_nutrition_data(user_id: int):
    """Process user nutrition data asynchronously."""
    # Task implementation
    return result

# Call task
from celery_app.tasks.periodic import process_nutrition_data
process_nutrition_data.delay(user_id=123)
```

## Environment Configuration

### Required Environment Variables

Critical variables (see `.env.example` for full list):

```bash
# Database
POSTGRES_SERVER=db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password
POSTGRES_DB=nutricore
POSTGRES_PORT=5432

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# Security
SECRET_KEY=your_secret_key_here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_WEBHOOK_URL=https://your_domain.com/webhook  # For webhook mode
TELEGRAM_ADMIN_IDS=[123456789]

# OpenAI
OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-4o-mini
OPENAI_TEMPERATURE=0.7
OPENAI_MAX_TOKENS=2000

# Application
DEBUG=true
ENVIRONMENT=development
DEFAULT_TIMEZONE=Asia/Tbilisi
```

### Configuration Management

Settings are managed through `app/core/config.py` using Pydantic Settings:
- Automatically loads from `.env` file
- Type validation and conversion
- Property methods for computed values (e.g., `SQLALCHEMY_DATABASE_URI`)
- Access via `from app.core.config import settings`

## Testing Guidelines

### Test Structure

Tests are located in `tests/` directory with this structure:
- `conftest.py`: Shared fixtures (db_session, test client)
- `test_crud_*.py`: CRUD operation tests
- `test_schemas.py`: Schema validation tests
- `test_api_*.py`: API endpoint tests (when added)

### Writing Tests

Follow these patterns:

```python
import pytest
from app.crud.crud_user import crud_user
from app.schemas.user import UserCreate

def test_create_user(db_session):
    """Test user creation."""
    user_in = UserCreate(
        telegram_id=123456789,
        username="test_user",
        diet_preferences={"vegan": True}
    )
    user = crud_user.create(db_session, obj_in=user_in)

    assert user.telegram_id == 123456789
    assert user.username == "test_user"
    assert user.id is not None
```

### Test Fixtures

Available fixtures from `conftest.py`:
- `db_session`: Database session for tests
- Use fixtures for common test data setup

### Running Tests

```bash
# All tests
pytest

# Specific file
pytest tests/test_crud_user.py

# Specific test
pytest tests/test_crud_user.py::test_create_user

# With coverage
pytest --cov=app --cov-report=html

# Verbose output
pytest -v

# Stop on first failure
pytest -x
```

## Common Tasks for AI Assistants

### When Adding Features

1. **Understand the requirement**: Clarify the feature scope and acceptance criteria
2. **Review existing code**: Check similar implementations in the codebase
3. **Plan the implementation**: Identify which layers need changes (model, schema, CRUD, API, service)
4. **Implement in order**: Model → Schema → CRUD → Service → API
5. **Write tests**: Add tests for new functionality
6. **Update migrations**: Create and apply database migrations if needed
7. **Test manually**: Use the API or bot to verify functionality
8. **Document**: Add docstrings and update relevant documentation

### When Fixing Bugs

1. **Reproduce the issue**: Understand the bug and how to trigger it
2. **Write a failing test**: Create a test that demonstrates the bug
3. **Identify root cause**: Trace through the code to find the issue
4. **Implement fix**: Make minimal changes to fix the issue
5. **Verify tests pass**: Ensure the new test and all existing tests pass
6. **Check for similar issues**: Look for the same pattern elsewhere

### When Refactoring

1. **Ensure tests exist**: Have comprehensive tests before refactoring
2. **Make small changes**: Refactor incrementally, running tests after each change
3. **Maintain backwards compatibility**: Don't break existing APIs unless necessary
4. **Update documentation**: Keep docs in sync with code changes
5. **Review patterns**: Ensure refactored code follows project conventions

### When Reviewing Code

Look for:
- **Architecture compliance**: Proper separation of concerns across layers
- **Pattern consistency**: Follows established CRUD, schema, and API patterns
- **Type safety**: Proper type hints and Pydantic validation
- **Error handling**: Appropriate exception handling and HTTP status codes
- **Security**: No SQL injection, proper authentication, secure secrets handling
- **Testing**: Adequate test coverage for new code
- **Documentation**: Clear docstrings and comments where needed
- **Code style**: Black formatting, isort imports, flake8 compliance

## Troubleshooting

### Common Issues

**Database connection errors**:
- Verify PostgreSQL is running: `docker-compose ps`
- Check connection string in `.env`
- Ensure migrations are applied: `alembic upgrade head`

**Import errors**:
- Check virtual environment is activated
- Verify dependencies installed: `poetry install`
- Check for circular imports

**Alembic migration conflicts**:
- Check for multiple heads: `alembic heads`
- Merge heads if needed: `alembic merge heads`
- Ensure database state matches: `alembic current`

**Celery tasks not running**:
- Verify Redis is running: `docker-compose ps redis`
- Check worker is started: `docker-compose logs celery_worker`
- Ensure task is registered in `celery_app.celery_app.py`

**Telegram bot not responding**:
- Check bot token is correct in `.env`
- Verify bot is running: `docker-compose logs telegram_bot`
- Check webhook vs polling mode configuration

### Debugging Tips

- **Enable debug mode**: Set `DEBUG=true` in `.env`
- **Check logs**: Use `docker-compose logs -f [service_name]`
- **Database inspection**: Connect with `psql` or a GUI tool
- **API testing**: Use FastAPI's automatic docs at `/docs`
- **Interactive testing**: Use `python -i` with imports to test components

## Security Considerations

### Important Security Practices

1. **Never commit secrets**: Use `.env` file (gitignored) for sensitive data
2. **Validate input**: Use Pydantic schemas for all API inputs
3. **Sanitize database queries**: Use SQLAlchemy ORM, not raw SQL
4. **Authentication**: Implement proper JWT token validation
5. **Authorization**: Check user permissions before operations
6. **HTTPS only**: Use SSL certificates in production
7. **Rate limiting**: Implement rate limits on API endpoints (future)
8. **SQL injection prevention**: Always use parameterized queries
9. **Dependency updates**: Regularly update dependencies for security patches

### Sensitive Files (Never Commit)

- `.env` - Environment variables
- `*.pem`, `*.key` - Certificates and keys
- `private_key.json` - Service account keys
- Any files with credentials or tokens

## Performance Considerations

### Database Optimization

- Use `select()` instead of `.query()` (SQLAlchemy 2.0 style)
- Add indexes on frequently queried columns
- Use `joinedload()` for eager loading relationships
- Implement pagination for large result sets
- Monitor slow queries and optimize

### API Optimization

- Use async endpoints where beneficial
- Implement caching with Redis for expensive operations
- Batch database operations when possible
- Return only necessary data in API responses
- Consider compression for large responses

### Celery Usage

Use Celery for:
- Long-running operations (data analysis)
- External API calls (OpenAI requests)
- Scheduled tasks (daily reports, cleanup)
- Background processing (image analysis)

Don't use Celery for:
- Simple, fast operations
- Operations requiring immediate response
- Operations with real-time user feedback

## Future Development

Refer to `ROADMAP.md` for planned features and enhancements. Key areas:

- **Phase 1**: Enhanced OpenAI integration, better UX, increased test coverage
- **Phase 2**: Smart scale integration, activity tracking, advanced analytics
- **Phase 3**: AI-powered diet plans, health pattern recognition, NLP improvements
- **Phase 4**: Multiplatform support, integration ecosystem, community features

When implementing new features, consider:
- Alignment with roadmap priorities
- Impact on existing functionality
- Scalability and performance
- User experience improvements
- Security and privacy implications

## Resources

### Documentation
- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [SQLAlchemy 2.0 Docs](https://docs.sqlalchemy.org/en/20/)
- [Pydantic Docs](https://docs.pydantic.dev/)
- [Alembic Tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [python-telegram-bot Docs](https://docs.python-telegram-bot.org/)
- [OpenAI API Docs](https://platform.openai.com/docs/)
- [Celery Docs](https://docs.celeryq.dev/)

### Project Documentation
- `README.md` - Project overview and setup
- `ROADMAP.md` - Development roadmap
- `Business_description.md` - Business context
- `.env.example` - Environment variable reference

## Questions and Support

When working on this codebase as an AI assistant:
- **Clarify requirements** before implementing features
- **Ask about priorities** when multiple approaches are possible
- **Suggest improvements** when you notice issues
- **Document decisions** in code comments and commit messages
- **Follow existing patterns** unless explicitly asked to refactor
- **Test thoroughly** before considering work complete

## Version History

- **v0.1.0** (Current): Initial open-source release with core functionality
  - FastAPI backend with REST API
  - Telegram bot integration
  - OpenAI-powered food analysis
  - Database models and migrations
  - Basic test coverage
  - Docker deployment setup
