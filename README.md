# HR Management System

Flask + PostgreSQL app for tracking employees, attendance, tasks and leave.

I originally wrote a version of this during an internship at MFI Document
Solutions in Kampala. It did the job, but it was a prototype — one big
`app.py`, SQLite, and a few things in it that make me wince now. This is me
rebuilding it properly.

**Currently:** schema, auth and infrastructure are done. Attendance, leave and
the reporting side are in progress. Roadmap is at the bottom.

---

## Running it

```bash
cp .env.example .env
docker compose up --build
```

Then <http://localhost:8000>. Migrations run on startup.

You'll need an account to log in:

```bash
docker compose exec web flask create-admin
```

If you'd rather not use Docker, you need Postgres 14+ running somewhere:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # edit DATABASE_URL
flask db upgrade
flask create-admin
flask run --port 8000
```

---

## What changed from the original

The prototype worked, so this isn't a rewrite for its own sake. But a few
things needed fixing before I'd want anyone looking at it:

**Passwords were stored in plaintext.** Login was a single query —
`SELECT * FROM employee WHERE username = ? AND password = ?`. Now they're
hashed with `werkzeug.security`.

**`users.db` was committed to the repo**, including those plaintext
credentials. Databases are gitignored here, and there's a pre-commit hook so I
can't do it again by accident.

**Role checks were copy-pasted into each route** — which meant one route,
`/users`, didn't have one at all and would happily dump the whole employee
table to anyone who found it. There's a decorator for that now.

**SQLite had no real constraints.** Nothing stopped two attendance records for
the same person on the same day, or a clock-out before a clock-in. Those are
database constraints now, not just form validation.

Plus the boring stuff that makes a project maintainable: migrations instead of
`CREATE TABLE IF NOT EXISTS`, an ORM instead of raw cursors, tests, and CI.

---

## Layout

```
app/
├── config.py         Config per environment, everything from env vars
├── extensions.py     Extension singletons — keeps imports acyclic
├── security.py       Role decorator and access checks
├── cli.py            flask seed / flask create-admin
├── models/           SQLAlchemy 2.0
├── blueprints/       Routes
└── templates/
migrations/           Alembic
tests/
```

### The schema

Seven tables. Departments and projects are reference data. Employees have a
self-referential `manager_id`, which gives me the reporting chain for free.
Attendance, tasks, leave and reviews all hang off employees.

I pushed the integrity rules into the database rather than leaving them in
Python, because seed scripts and psql sessions bypass application code:

- one attendance row per employee per day
- `clock_out > clock_in`, `end_date >= start_date`, `0 < hours <= 24`
- nobody can be their own manager or write their own review
- a leave decision has to record both who decided and when, or neither

The indexes are picked for the queries the reports run — `(employee_id,
work_date)` for one person's history, `(work_date, status)` for daily
company-wide counts. Not just an index on every foreign key.

### Permissions

Three roles: employee, manager, HR admin. The rule lives in one method,
`Employee.can_view()` — HR sees everyone, managers see their direct reports,
everyone sees themselves. Routes get `@roles_required(...)`.

The bit I care most about is that `TestAccessRules` covers the case I'd
otherwise get wrong: a manager can see *their* reports, but not another
manager's.

---

## Development

```bash
pytest
ruff check . && ruff format .
mypy app
flask db migrate -m "..."    # after touching a model
flask db upgrade
```

CI runs lint, mypy, a Docker build, and the tests against Postgres 16 rather
than SQLite, so enum types and constraints behave the way they will in
production.

It also runs `flask db migrate` and fails if it detects any changes — that
catches the easy mistake of editing a model and forgetting to generate the
migration to go with it.

---

## Leave

The one part with real business logic, and why there's a `services/` layer
at all. Approving leave isn't a status update — it checks who's allowed to
decide, then writes the approved days onto the timesheet so the two tables
agree.

Rules worth knowing:

- Weekends and public holidays don't cost you a day, and don't get written
  to attendance.
- Pending requests are held against your balance, not just approved ones —
  otherwise two requests that together exceed your entitlement both sail
  through because neither had been decided yet.
- Only annual leave draws the balance down. Sick and bereavement don't.
- Nobody approves their own request, including HR.
- Cancelling approved leave removes the leave days it wrote, but leaves
  alone any day where real attendance has since been recorded.

## Data quality

The part I most wanted to build. It's the same problem as migration
reconciliation at work: two systems that are supposed to agree usually
don't, and what's useful is a repeatable report of exactly where and by how
much — not a vague sense that the numbers are off.

Ten checks, each with a count, a plain-language explanation of why it
matters, and sample rows so you can go and look at the actual records:

| Check | Finds |
|---|---|
| `OPEN_SHIFT` | Clocked in, never clocked out |
| `WORKED_ON_LEAVE` | Timesheet says present, leave says approved |
| `LEAVE_NOT_ON_TIMESHEET` | Approved leave that never reached the timesheet |
| `OVER_LOGGED` | More hours booked to projects than were worked |
| `TASK_NO_ATTENDANCE` | Work booked on a day with no attendance |
| `TASK_OUTSIDE_PROJECT` | Time booked to a project that wasn't running |
| `OUTSIDE_EMPLOYMENT` | Attendance before joining or after leaving |
| `IMPLAUSIBLE_SHIFT` | Shifts over 16 hours |
| `NO_MANAGER` | Active staff with nobody to approve their leave |
| `FUTURE_DATED` | Attendance for days that haven't happened |

Against the seeded data it finds ~440 discrepancies in about half a second
across 84k attendance and 130k task rows. The seed plants some of them
deliberately — roughly 6% of approved leave never reaches the timesheet, and
1.3% of worked days have no clock-out — because a checker that has never
fired is indistinguishable from one that doesn't work.

**The checks only read.** Nothing in there fixes anything, deliberately: a
report that quietly edits data is a report you can't trust twice.

Everything aggregates in SQL. It'd read more naturally in pandas, but that
means moving 80,000 rows to produce twelve. pandas earns its place at the
export step, handling CSV quoting and the Excel writer.

## Still to do

- [x] Schema, migrations, auth, roles, Docker, CI
- [x] Seed script with realistic volume — 250 employees, 18 months,
      ~215k rows in under 4 seconds
- [x] Employee directory with search, filtering and paging
- [x] Leave requests and the approval flow
- [x] Timesheet: clocking, monthly view, task logging
- [x] Dashboards and the data quality report, CSV/Excel export
- [ ] Deploy a demo with read-only logins
- [ ] Charts on the dashboard — the numbers are all there, they're just
      tables right now
- [ ] Org chart from the reporting line

## Licence

MIT.
