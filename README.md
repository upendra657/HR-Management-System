# HR Management System

Flask + PostgreSQL app for tracking employees, attendance, tasks and leave.

I wrote a version of this during an internship at MFI Document Solutions in
Kampala. It did the job, but it was a prototype — one big `app.py`, SQLite, and
a few things in it that make me wince now. This is me rebuilding it properly.

278 tests, running against Postgres in CI.

---

## Running it

```bash
cp .env.example .env
docker compose up --build
docker compose exec web flask create-admin
```

Then <http://localhost:8000>. Migrations run on startup. Without Docker you
need Postgres 14+ — see [COMMANDS.md](COMMANDS.md).

---

## What changed from the original

The prototype worked, so this isn't a rewrite for its own sake. But:

**Passwords were stored in plaintext.** Login was one query —
`SELECT * FROM employee WHERE username = ? AND password = ?`. Now they're
hashed.

**`users.db` was committed**, including those credentials. Databases are
gitignored here.

**Role checks were copy-pasted into each route** — so one route, `/users`,
didn't have one and would dump the whole employee table to anyone who found it.
There's a decorator for that now.

**SQLite had no real constraints.** Nothing stopped two attendance records for
the same person on the same day, or a clock-out before a clock-in. Those are
database constraints now, not form validation.

Plus migrations, an ORM, tests and CI.

---

## The parts worth looking at

### Constraints live in the database

Seed scripts and psql sessions bypass application code, so the rules that must
always hold are in the schema: one attendance row per employee per day,
`clock_out > clock_in`, `0 < hours <= 24`, nobody manages themselves or writes
their own review, and a leave decision records both who decided and when, or
neither.

Indexes are picked for the queries the reports actually run — `(employee_id,
work_date)` for one person's history, `(work_date, status)` for company-wide
daily counts. Not an index on every foreign key.

### Access control in one place

Three roles. The rule is `Employee.can_view()` — HR sees everyone, managers see
their direct reports, everyone sees themselves — mirrored by `visible_to()`,
which pushes the same rule into the WHERE clause so the row count doesn't leak
either. The test I care about is the one proving a manager can see *their*
reports but not another manager's.

### Leave is the real business logic

Approving leave isn't a status update — it checks who's allowed to decide, then
writes the approved days onto the timesheet so the two tables agree. Weekends
and holidays don't cost a day. Pending requests count against your balance, not
just approved ones, otherwise two requests that together exceed your entitlement
both sail through. Nobody approves their own, including HR.

### Data quality

The part I most wanted to build, because it's the same problem as migration
reconciliation at work: two systems that should agree usually don't, and what
helps is a repeatable report of exactly where and by how much.

Ten checks — clocked in but never out, timesheet says present while leave says
approved, approved leave that never reached the timesheet, more hours booked to
projects than were worked, attendance before joining, and so on. Each returns a
count, a plain-language explanation, and sample rows to go look at.

Against the seeded data it finds ~440 discrepancies in about half a second
across 84k attendance and 130k task rows. The seed plants some deliberately —
roughly 6% of approved leave never reaches the timesheet — because a checker
that has never fired is indistinguishable from one that doesn't work.

**The checks only read.** A report that quietly edits data is a report you can't
trust twice.

Everything aggregates in SQL. It'd read more naturally in pandas, but that means
moving 80,000 rows to produce twelve. pandas earns its place at the export step.

### The landing page

Everyone lands on `/dashboard`, so it has to work for all three roles. Clock
state and the matching button, this month's hours, leave balance, and a bar per
working day for the last fortnight. Managers also get their approval queue and
who's off.

HR's queue is every pending request in the company, so the page fetches five and
counts the rest — a landing page that slows down as the company grows is a bad
landing page. There's a test that the query count doesn't move when pending
requests go from three to twelve.

Days with no attendance are plotted as zero rather than skipped, because a gap
in the bars reads as a broken chart. Days clocked in but never out go amber
instead — there the zero is a recording gap, which is the distinction the
reconciliation report is built on.

### Dark mode

The theme is set by an inline script in `<head>`, not the deferred `theme.js` —
a deferred script runs after first paint, so dark mode would flash white on
every load. A test asserts it comes before the stylesheet.

Chart data goes into a `<script type="application/json">` tag rather than being
fetched, since it's already computed for the tables on the same request. That's
an XSS vector if the serialiser doesn't escape angle brackets; Flask's `tojson`
emits `<`, and there's a test with a project named `</script>` proving it
stays inert.

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
than SQLite, so enums and constraints behave the way they will in production.
It also runs `flask db migrate` and fails if it detects changes — that catches
editing a model and forgetting the migration.

---

## Deploying

`render.yaml` provisions the web service and a Postgres instance. In Render:
**New → Blueprint**, point it at this repo, apply.

Three things that cost me time:

**Render hands out `postgres://` URLs.** SQLAlchemy 2.0 dropped that alias and
refuses to start. `_normalise_db_url` rewrites it to `postgresql+psycopg://`.

**The build needs the `demo` extra.** `flask setup-demo` runs on first boot and
imports the seed generator, which needs Faker. Installing plain `.` builds fine
and then fails at start — the worst order to find out.

**Migrations run in the start command,** not `preDeployCommand`, which is paid.
Render runs it on every boot, so `setup-demo` is idempotent: it skips seeding if
employees already exist rather than wiping the database on each redeploy.

### The demo is read-only

`DEMO_MODE=true` refuses anything that writes. Sign-in still works, because
seeing the role-based views is most of the point. Without it a public demo gets
emptied by the first person who finds the delete buttons.

Three logins, password `demo12345`:

| Login | Sees |
|---|---|
| `demo_hr` | Everyone, all reports, the data quality report |
| `demo_manager` | Only their own direct reports |
| `demo_employee` | Only themselves; reports return 403 |

Signing in as each is the quickest way to see the access rules working rather
than taking my word for it.

Free tier caveats: the service sleeps after 15 minutes idle and takes ~30
seconds to wake, and the free Postgres expires after 30 days.

---

## Still to do

- [x] Schema, migrations, auth, roles, Docker, CI
- [x] Seed script with realistic volume — 250 employees, 18 months, ~215k rows
- [x] Employee directory, leave workflow, timesheet
- [x] Dashboards, data quality report, CSV/Excel export
- [x] Dark mode and charts
- [ ] Deploy a demo with read-only logins
- [ ] Org chart from the reporting line
- [ ] Audit trail — for an HR system, salary and role changes going unrecorded
      is a real gap rather than an oversight

## Licence

MIT.
