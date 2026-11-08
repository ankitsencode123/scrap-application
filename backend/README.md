# ScrapMart API (FastAPI + PostgreSQL)

Layout (the original single `main.py` split by its numbered sections, logic unchanged):

```
app/main.py            section 10  app, CORS, lifespan, routers
app/core/config.py     section 1   Settings
app/core/security.py   section 6   bcrypt, JWT, get_current_user, require_role
app/core/storage.py    section 7   S3/local storage, image processing
app/db/base.py         section 2   engine, SessionLocal, Base, get_db
app/db/enums.py        section 3   enums & constants
app/db/models.py       section 4   ORM models (+ Message for chat)
app/schemas.py         section 5   Pydantic models
app/services/common.py section 8   feed query, haversine, tracker, serialisation
app/api/v1/*.py        section 9   routers: auth users uploads listings bids pickup reviews messages meta
tests/                 end-to-end flow test (uses a throw-away Postgres via `pgserver`, or DATABASE_URL)
```

## Run

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then: export $(grep -v '^#' .env | xargs)
docker compose up -d db            # or use any PostgreSQL
uvicorn app.main:app --reload      # docs: http://localhost:8000/docs
```

Frontend: copy `.env.local.example` to `.env.local` in the repo root, then `npm run dev`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

See the docstring in `app/core/config.py` / the project notes for every environment variable.
