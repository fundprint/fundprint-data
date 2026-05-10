# fundprint-data

Pipeline that acquires, resolves, validates, and publishes a public dataset tracking private-equity ownership of U.S. ABA / autism therapy clinics. Every ownership claim traces to a public source URL with a numeric confidence score; no row enters the published dataset without passing a methodology-defined validation gate. See `docs/architecture.md` for the five-layer design and `docs/schema.md` for the data contract.

## Quickstart

```bash
# 1. Clone the repo
git clone https://github.com/<org>/fundprint-data.git
cd fundprint-data

# 2. Install the package and dev dependencies (requires Python 3.12+)
pip install -e ".[dev]"

# 3. Set environment variables
cp .env.example .env
# Edit .env with your DATABASE_URL, ANTHROPIC_API_KEY, etc.

# 4. Apply migrations to a running Postgres instance
#    Local dev: `npx supabase db reset` against a Supabase local stack,
#    or pipe the file directly into psql:
psql "$DATABASE_URL" -f supabase/migrations/20250510000001_initial_schema.sql

# 5. Run the test suite
pytest
```

The methodology governing what counts as "PE-backed" lives in `fundprint-methodology`. The dashboard that reads this dataset lives in `fundprint-dashboard`. See `docs/cross-repo.md` for how the three repos coordinate.
