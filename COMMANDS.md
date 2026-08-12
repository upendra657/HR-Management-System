# Commands

Quick reference. Everything assumes you're in the project root.

```bash
cd ~/Desktop/Projects/HR-Management-System
```

---

## First time setup

### With Docker (easiest)

```bash
cp .env.example .env
docker compose up --build              # app + Postgres, migrations run on start
```

App at <http://localhost:8000>. Then in another terminal:

```bash
docker compose exec web flask seed --employees 250 --months 18
docker compose exec web flask create-admin
```

### Without Docker

Needs Postgres 14+ running somewhere.

```bash
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                # dev pulls in [demo], so seeding works

cp .env.example .env                   # edit DATABASE_URL to point at your Postgres
createdb hrms                          # if it doesn't exist yet

export FLASK_APP=wsgi.py
flask db upgrade
flask seed --employees 250 --months 18
flask create-admin
flask run --port 8000
```

---

## Daily use

```bash
source .venv/bin/activate
export FLASK_APP=wsgi.py

flask run --port 8000                  # dev server, auto-reloads
flask shell                            # REPL with all models already imported
```

### Docker equivalents

```bash
docker compose up                      # start
docker compose up --build              # rebuild after changing dependencies
docker compose down                    # stop
docker compose down -v                 # stop AND delete the database volume
docker compose logs -f web             # tail app logs
docker compose exec web bash           # shell inside the container
docker compose exec db psql -U hrms    # psql inside the database container
```

---

## Seeding

```bash
flask seed                             # 250 employees, 18 months, ~215k rows
flask seed --employees 40 --months 6   # smaller, faster
flask seed --reset                     # wipe and regenerate
flask setup-demo                       # seed + create the three demo logins
flask setup-demo --force               # re-seed even if data exists
```

Seeded accounts all use the password `demo12345`.

The seed is deterministic — the same seed value produces the same database,
so you can talk about specific numbers and they'll still be there tomorrow.

---

## Tests

```bash
pytest                                 # all 278
pytest -q                              # quieter
pytest -v                              # every test name — good for reading
                                       # what the system guarantees
pytest tests/test_leave.py             # one file
pytest -k "reconciliation"             # matching a keyword
pytest -x                              # stop at the first failure
pytest --lf                            # re-run only what failed last time
pytest --durations=10                  # find the slow ones
```

The full suite takes ~60s. Run one file while iterating.

### Against Postgres instead of SQLite

Tests default to SQLite in memory. CI runs them against Postgres, and you
should too before pushing anything schema-related:

```bash
createdb hrms_test
TEST_DATABASE_URL=postgresql+psycopg://hrms:hrms@localhost:5432/hrms_test pytest
```

---

## Code quality

```bash
ruff check .                           # lint
ruff check . --fix                     # lint and autofix
ruff format .                          # format
ruff format --check .                  # check formatting without changing
mypy app                               # type check
```

Run all four before committing — CI runs exactly these.

---

## Migrations

```bash
flask db migrate -m "Add audit log"    # generate after changing a model
flask db upgrade                       # apply
flask db downgrade                     # undo the last one
flask db current                       # which migration is applied
flask db history                       # all of them
```

**After changing any model, always generate a migration.** CI fails the build
if the models and migrations have drifted — that check exists because it's
easy to forget.

To check for drift yourself:

```bash
flask db migrate -m "drift-check"      # should say "No changes in schema detected"
```

If it generates a file, you forgot a migration. If it says no changes, delete
nothing and carry on.

---

## Poking at the data

```bash
flask shell
```

```python
# Models are already imported.
Employee.query.count()
db.session.scalar(select(func.count()).select_from(AttendanceLog))

# Run the data quality report
from app.analytics import reconciliation as recon

report = recon.run_all()
print(report.headline)
for f in report.failing:
    print(f.code, f.count, f.explanation[:60])

# Aggregates
from app.analytics import reports as rp

rp.headline()
rp.project_utilisation()
```

### Straight SQL

```bash
psql $DATABASE_URL                     # or: docker compose exec db psql -U hrms
```

```sql
-- Prove the constraints are real: this should be rejected
INSERT INTO attendance_logs (employee_id, work_date, status)
VALUES (1, '2026-01-05', 'present');
INSERT INTO attendance_logs (employee_id, work_date, status)
VALUES (1, '2026-01-05', 'present');   -- uq_attendance_employee_date fires

-- Clock out before clock in: ck_attendance_time_order fires
UPDATE attendance_logs SET clock_in = '17:00', clock_out = '09:00' WHERE id = 1;

-- Manage yourself: ck_employees_self_manage fires
UPDATE employees SET manager_id = id WHERE id = 5;

-- Check an index is actually used
EXPLAIN ANALYZE
SELECT * FROM attendance_logs
WHERE employee_id = 42 AND work_date BETWEEN '2026-01-01' AND '2026-03-31';
-- want "Index Scan using ix_attendance_employee_date", not "Seq Scan"
```

Worth doing at least once — seeing the database refuse makes the argument
yours rather than something you read.

---

## Git

```bash
git status
git add -A
git commit -m "..."
git push

git log --oneline                      # history
git log --stat -1                      # what the last commit touched
git diff                               # unstaged changes
git diff --staged                      # staged changes
```

### First push to a new repo

```bash
git remote add origin https://github.com/upendra657/<repo-name>.git
git push -u origin main
```

`HR-Management-System` is already taken by the old prototype. Either pick a
new name, or overwrite it — keeping the old code on a branch first:

```bash
git remote add origin https://github.com/upendra657/HR-Management-System.git
git fetch origin
git branch legacy origin/main          # keep the prototype
git push origin legacy
git push -u origin main --force        # replace
```

---

## Deploying to Render

1. Push to GitHub
2. Render → **New → Blueprint** → pick the repo → apply

`render.yaml` handles the rest: web service, Postgres, generated
`SECRET_KEY`, migrations and seeding on boot.

```bash
# Watch it from the Render dashboard, or with their CLI:
render logs -s hrms --tail
```

Free tier: sleeps after 15 minutes idle (~30s to wake), and the free Postgres
expires after 30 days.

---

## Troubleshooting

**`No such command 'db'`** — `export FLASK_APP=wsgi.py`

**`could not connect to server`** — Postgres isn't running, or `DATABASE_URL`
in `.env` is wrong. With Docker: `docker compose up db`.

**`Can't load plugin: sqlalchemy.dialects:postgres`** — the URL starts with
`postgres://`. `_normalise_db_url` in `config.py` handles this automatically,
so if you see it, something is bypassing the config.

**`Database already has N employees`** — add `--reset` to `flask seed`.

**Tests pass locally, fail in CI** — CI uses Postgres, local defaults to
SQLite. Re-run with `TEST_DATABASE_URL` set (see above). Enum and constraint
behaviour differ between the two.

**`flask seed` is slow** — it shouldn't be; 250 employees takes ~4 seconds.
If it's minutes, you're probably on a remote database rather than localhost.

**Port 8000 already in use** — `lsof -ti:8000 | xargs kill`
