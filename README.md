# HR Management System

Flask + PostgreSQL app for tracking employees, attendance, tasks and leave.

I originally wrote a version of this during an internship at MFI Document
Solutions in Kampala. It did the job, but it was a prototype — one big
`app.py`, SQLite, and a few things in it that make me wince now. This is me
rebuilding it properly.

**Currently:** the directory, timesheet, leave workflow, dashboards and the
data quality report all work. 265 tests, running against Postgres in CI.
What's left is at the bottom.

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
credentials. Databases are gitignored here.

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
├── demo.py           Read-only guard for the public demo
├── cli.py            flask seed / create-admin / setup-demo
├── models/           SQLAlchemy 2.0 — 7 tables
├── services/         Business logic: employees, leave, attendance, dashboard
├── analytics/        Reconciliation, reports, shared SQL expressions
├── blueprints/       22 routes across 6 blueprints
└── templates/
migrations/           Alembic
scripts/seed.py       Synthetic data generator
tests/                265 tests
```

Views parse the request, call a service, render. Anything touching more than
one table lives in `services/`; anything aggregating across the whole company
lives in `analytics/`.

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

## The landing page

Everyone lands on `/dashboard`, so it is the page that has to work for all
three roles at once. It shows your clock state and the button that matches it,
this month's hours, your leave balance, and a bar per working day for the last
fortnight. Managers additionally get their approval queue and who on their team
is off; HR gets the same, company-wide.

Two decisions worth knowing:

**The lists are previews with separate counts.** HR's approval queue is every
pending request in the company, so the page fetches five and counts the rest.
A landing page that gets slower as the company grows is a bad landing page.
There's a test asserting the query count doesn't move when the number of
pending requests goes from three to twelve.

**Days with no attendance are plotted as zero, not skipped.** A gap in the bars
reads as a broken chart. Days where someone clocked in and never out are
coloured amber instead — the zero there is a recording gap, not a day off,
which is the same distinction the reconciliation report is built around.

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

## Deploying

`render.yaml` provisions both the web service and a Postgres instance. In
Render: **New → Blueprint**, point it at this repo, apply. `SECRET_KEY` is
generated for you and `DATABASE_URL` is wired from the database.

Two things that cost me time and are worth knowing:

**Render hands out `postgres://` URLs.** SQLAlchemy 2.0 dropped that alias
and refuses to start, and the default driver would be psycopg2 rather than
the psycopg 3 this installs. `_normalise_db_url` in `config.py` rewrites it
to `postgresql+psycopg://`, so the platform's URL works untouched.

**Migrations run in the start command,** not a `preDeployCommand` — that's a
paid feature. Render runs the start command on every boot, so `setup-demo`
is idempotent: it skips seeding if employees already exist rather than
wiping the database on each redeploy.

### The demo is read-only

`DEMO_MODE=true` refuses anything that would write. Sign-in and sign-out
still work, because seeing the role-based views is most of the point.
Without this a public demo gets emptied by the first person who finds the
delete buttons.

Three logins, password `demo12345`:

| Login | Sees |
|---|---|
| `demo_hr` | Everyone, all reports, the data quality report |
| `demo_manager` | Only their own direct reports |
| `demo_employee` | Only themselves; reports return 403 |

Signing in as each is the quickest way to see the access rules actually
working rather than taking my word for it.

Free tier caveats: the service sleeps after 15 minutes idle and takes ~30
seconds to wake, and the free Postgres instance expires after 30 days.

## Still to do

- [x] Schema, migrations, auth, roles, Docker, CI
- [x] Seed script with realistic volume — 250 employees, 18 months,
      ~215k rows in under 4 seconds
- [x] Employee directory with search, filtering and paging
- [x] Leave requests and the approval flow
- [x] Timesheet: clocking, monthly view, task logging
- [x] Dashboards and the data quality report, CSV/Excel export
- [x] Dark mode, and charts on the dashboard
- [x] Personal landing page — clock state, balance, approval queue
- [ ] Deploy a demo with read-only logins
- [ ] Org chart from the reporting line
- [ ] Audit trail — for an HR system, salary and role changes going
      unrecorded is a real gap rather than an oversight

## Dark mode

Bootstrap 5.3 does the work via `data-bs-theme`; the toggle sits in the
navbar and remembers your choice, falling back to the OS preference.

Two details worth knowing:

The theme is set by a small inline script in `<head>`, not by the deferred
`theme.js`. A deferred script runs after first paint, so dark mode would
flash white on every page load. There's a test asserting that script appears
before the stylesheet.

Both the sun and moon icons ship in the markup and CSS picks one. Swapping
them in JavaScript leaves the wrong icon visible until the script runs.

The charts read their colours from live CSS variables and re-style on a
`themechange` event, so switching theme doesn't need a reload. Chart data is
serialised into a `<script type="application/json">` tag rather than fetched
— it's already computed for the tables on the same request, and Flask's
`tojson` escapes angle brackets so a project named `</script>` can't break
out. There's a test for that too.

## Licence

MIT.
