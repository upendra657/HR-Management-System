"""Generates synthetic HR data so the reports have something to report on.

Deterministic - same seed gives the same database every time, which matters
because I want to be able to talk about specific numbers and have them still
be there tomorrow.

    flask seed                          250 employees, 18 months
    flask seed --employees 40 --months 6 --reset

Rough shape at the defaults: ~250 employees, ~95k attendance rows, ~130k
tasks. Inserted through SQLAlchemy Core rather than the ORM - building 200k
model instances is slow and pointless when nothing needs the identity map.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from faker import Faker
from sqlalchemy import delete, func, insert, select

from app.extensions import db
from app.models import (
    AttendanceLog,
    AttendanceStatus,
    Department,
    Employee,
    EmployeeStatus,
    EmploymentType,
    LeaveRequest,
    LeaveStatus,
    LeaveType,
    PerformanceReview,
    Project,
    ReviewStatus,
    Role,
    Task,
)
from app.services.dates import public_holidays, working_days

SEED = 20240617
BATCH = 5000

DEPARTMENTS = [
    ("Engineering", "ENG", "Software delivery and platform"),
    ("Operations", "OPS", "Service delivery and field teams"),
    ("Finance", "FIN", "Accounting, payroll and procurement"),
    ("Human Resources", "HR", "People operations"),
    ("Sales", "SLS", "New business and account management"),
    ("Support", "SUP", "Customer support desk"),
]

# Weighted so Engineering and Operations dominate, like a real services company.
DEPT_WEIGHTS = [0.30, 0.26, 0.09, 0.06, 0.15, 0.14]

TITLES = {
    "ENG": [
        "Software Engineer",
        "Senior Software Engineer",
        "QA Engineer",
        "DevOps Engineer",
        "Data Engineer",
    ],
    "OPS": ["Operations Analyst", "Field Technician", "Logistics Coordinator", "Site Supervisor"],
    "FIN": ["Accountant", "Financial Analyst", "Payroll Officer"],
    "HR": ["HR Officer", "Recruiter", "HR Business Partner"],
    "SLS": ["Account Executive", "Sales Development Rep", "Account Manager"],
    "SUP": ["Support Specialist", "Technical Support Engineer", "Support Team Lead"],
}

MANAGER_TITLES = {
    "ENG": "Engineering Manager",
    "OPS": "Operations Manager",
    "FIN": "Finance Manager",
    "HR": "HR Manager",
    "SLS": "Sales Manager",
    "SUP": "Support Manager",
}

PROJECTS = [
    ("Document Archive Migration", "DAM-01", "Ministry of Lands", "Kampala"),
    ("EDRMS Rollout Phase 2", "EDR-02", "Uganda Revenue Authority", "Kampala"),
    ("Records Digitisation", "RDG-01", "Stanbic Bank", "Kampala"),
    ("Scanning Bureau Operations", "SBO-01", "Internal", "Nairobi"),
    ("Payroll System Integration", "PSI-01", "Internal", "Kampala"),
    ("Client Portal Rebuild", "CPR-01", "Internal", "Remote"),
    ("Compliance Audit Support", "CAS-01", "Diamond Trust Bank", "Kampala"),
    ("Warehouse Inventory Sync", "WIS-01", "Mukwano Group", "Jinja"),
    ("Field Survey Programme", "FSP-01", "National Water", "Entebbe"),
    ("Helpdesk Migration", "HDM-01", "Internal", "Remote"),
    ("Data Quality Remediation", "DQR-01", "Uganda Revenue Authority", "Kampala"),
    ("Onboarding Automation", "OBA-01", "Internal", "Kampala"),
]

TASK_TEMPLATES = [
    "Scanned and indexed {n} document batches",
    "QA review of indexed records for {client}",
    "Resolved {n} support tickets",
    "Client site visit - {site}",
    "Data validation and reconciliation checks",
    "Updated migration mapping spreadsheet",
    "Weekly progress report and stand-up",
    "Fixed defects raised in UAT",
    "Configured user accounts and permissions",
    "Prepared invoices and supporting documentation",
    "Training session for client staff",
    "Backlog grooming and sprint planning",
    "Investigated data discrepancies in source extract",
    "Deployed release to staging",
]


class Rng:
    """Wraps the two sources of randomness so both are seeded together."""

    def __init__(self, seed: int) -> None:
        self.r = random.Random(seed)
        self.fake = Faker("en_GB")
        Faker.seed(seed)

    def weighted(self, choices: list, weights: list[float]):
        return self.r.choices(choices, weights=weights, k=1)[0]


def _chunked_insert(model, rows: list[dict]) -> int:
    """Core bulk insert in batches, so memory stays flat on the big tables."""
    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        db.session.execute(insert(model), chunk)
        total += len(chunk)
    db.session.commit()
    return total


def _wipe() -> None:
    """Children first - the FKs are RESTRICT in places and would refuse."""
    for model in (PerformanceReview, LeaveRequest, Task, AttendanceLog):
        db.session.execute(delete(model))
    db.session.commit()
    # Managers reference employees, so break the self-reference before deleting.
    db.session.execute(db.update(Employee).values(manager_id=None))
    db.session.commit()
    db.session.execute(delete(Employee))
    db.session.execute(delete(Project))
    db.session.execute(delete(Department))
    db.session.commit()


# --------------------------------------------------------------------------
def run_seed(
    employees: int = 250,
    months: int = 18,
    reset: bool = False,
    seed: int = SEED,
    quiet: bool = False,
) -> dict[str, int]:
    rng = Rng(seed)
    r = rng.r
    fake = rng.fake

    def say(msg: str) -> None:
        if not quiet:
            print(msg)

    existing = db.session.scalar(select(func.count()).select_from(Employee)) or 0
    if existing and not reset:
        raise SystemExit(
            f"Database already has {existing} employees. Re-run with --reset to replace them."
        )
    if reset:
        say("Clearing existing data...")
        _wipe()

    today = date.today()
    window_start = today - timedelta(days=int(months * 30.44))
    holidays = public_holidays(window_start, today)

    # ---- departments ----------------------------------------------------
    db.session.execute(
        insert(Department),
        [{"name": n, "code": c, "description": d} for n, c, d in DEPARTMENTS],
    )
    db.session.commit()
    depts = {d.code: d for d in db.session.scalars(select(Department)).all()}
    say(f"  departments      {len(depts)}")

    # ---- projects -------------------------------------------------------
    proj_rows = []
    for name, code, client, site in PROJECTS:
        start = window_start - timedelta(days=r.randint(0, 400))
        # Two thirds still running; the rest closed at some point in the window.
        active = r.random() < 0.66
        end = None if active else start + timedelta(days=r.randint(120, 600))
        if end and end > today:
            end, active = None, True
        proj_rows.append(
            {
                "name": name,
                "code": code,
                "client": client,
                "site": site,
                "start_date": start,
                "end_date": end,
                "is_active": active,
            }
        )
    db.session.execute(insert(Project), proj_rows)
    db.session.commit()
    projects = db.session.scalars(select(Project)).all()
    active_projects = [p for p in projects if p.is_active]
    say(f"  projects         {len(projects)} ({len(active_projects)} active)")

    # ---- employees ------------------------------------------------------
    # Built in three passes: HR admins, one manager per department, then the
    # rest - because managers must exist before anyone can report to them.
    emp_rows: list[dict] = []
    used_usernames: set[str] = set()
    counter = 0

    def make(dept_code: str, role: Role, title: str, join_earliest_years: int) -> dict:
        nonlocal counter
        counter += 1
        name = fake.name()
        base = "".join(ch for ch in name.lower().replace(" ", ".") if ch.isalnum() or ch == ".")
        username = base
        n = 1
        while username in used_usernames:
            n += 1
            username = f"{base}{n}"
        used_usernames.add(username)

        join = today - timedelta(days=r.randint(120, join_earliest_years * 365))
        terminated = role == Role.EMPLOYEE and r.random() < 0.07
        exit_date = None
        status = EmployeeStatus.ACTIVE
        if terminated:
            # Leave at least 60 days of history before they left.
            earliest_exit = join + timedelta(days=60)
            if earliest_exit < today:
                exit_date = earliest_exit + timedelta(
                    days=r.randint(0, max(1, (today - earliest_exit).days))
                )
                status = EmployeeStatus.TERMINATED

        etype = rng.weighted(
            [
                EmploymentType.FULL_TIME,
                EmploymentType.PART_TIME,
                EmploymentType.CONTRACT,
                EmploymentType.INTERN,
            ],
            [0.78, 0.07, 0.11, 0.04],
        )
        seniority = 1.0
        if role == Role.MANAGER:
            seniority = 1.9
        elif role == Role.HR_ADMIN:
            seniority = 1.7
        elif "Senior" in title or "Lead" in title:
            seniority = 1.35
        if etype == EmploymentType.INTERN:
            seniority = 0.45

        return {
            "employee_code": f"EMP-{counter:04d}",
            "full_name": name,
            "username": username,
            "email": f"{username}@mfi-example.com",
            "password_hash": PW_HASH,
            "role": role,
            "employment_type": etype,
            "status": status,
            "job_title": title,
            "join_date": join,
            "exit_date": exit_date,
            "base_salary": Decimal(str(round(r.uniform(3.2e6, 6.5e6) * seniority, -3))),
            "country": "Uganda",
            "phone": f"+2567{r.randint(10000000, 99999999)}",
            "department_id": depts[dept_code].id,
            "manager_id": None,
        }

    # Hash one password once - generate_password_hash is deliberately slow, and
    # doing it 250 times adds ~20 seconds for no benefit in fake data.
    probe = Employee()
    probe.set_password("demo12345")
    PW_HASH = probe.password_hash

    hr_admin = make("HR", Role.HR_ADMIN, "Head of People", 8)
    emp_rows.append(hr_admin)

    manager_idx: dict[str, int] = {}
    for _, code, _ in DEPARTMENTS:
        manager_idx[code] = len(emp_rows)
        emp_rows.append(make(code, Role.MANAGER, MANAGER_TITLES[code], 7))

    codes = [c for _, c, _ in DEPARTMENTS]
    while len(emp_rows) < employees:
        code = rng.weighted(codes, DEPT_WEIGHTS)
        emp_rows.append(make(code, Role.EMPLOYEE, r.choice(TITLES[code]), 5))

    db.session.execute(insert(Employee), emp_rows)
    db.session.commit()

    people = db.session.scalars(select(Employee).order_by(Employee.id)).all()
    by_code = {p.employee_code: p for p in people}

    # Wire up reporting: managers report to the HR admin, everyone else to
    # their department's manager.
    head = by_code[hr_admin["employee_code"]]
    dept_manager: dict[int, Employee] = {}
    for code, idx in manager_idx.items():
        mgr = by_code[emp_rows[idx]["employee_code"]]
        mgr.manager_id = head.id
        dept_manager[depts[code].id] = mgr
    for p in people:
        if p.role == Role.EMPLOYEE:
            line_manager = dept_manager.get(p.department_id)
            if line_manager and line_manager.id != p.id:
                p.manager_id = line_manager.id
    db.session.commit()

    say(
        f"  employees        {len(people)} "
        f"({sum(1 for p in people if p.status == EmployeeStatus.TERMINATED)} terminated)"
    )

    # ---- leave requests -------------------------------------------------
    # Generated before attendance, because approved leave has to show up as
    # leave days in the attendance table. Real HR data has gaps between the
    # two - that is what the reconciliation report is for - so a small share
    # are deliberately left unreflected rather than making them agree perfectly.
    leave_rows = []
    on_leave: dict[int, set[date]] = {}

    for p in people:
        start = max(window_start, p.join_date)
        end = min(today, p.exit_date or today)
        if (end - start).days < 40:
            continue
        # Roughly 21 days of annual entitlement, so 4-10 requests over 18 months.
        for _ in range(r.randint(4, 10)):
            s = start + timedelta(days=r.randint(0, max(1, (end - start).days - 5)))
            length = r.choices(
                [1, 2, 3, 5, 10, 14], weights=[0.28, 0.2, 0.18, 0.2, 0.1, 0.04], k=1
            )[0]
            e = s + timedelta(days=length - 1)
            if e > end:
                continue
            status = rng.weighted(
                [
                    LeaveStatus.APPROVED,
                    LeaveStatus.PENDING,
                    LeaveStatus.REJECTED,
                    LeaveStatus.CANCELLED,
                ],
                [0.72, 0.11, 0.09, 0.08],
            )
            decided_by, decided_at = None, None
            if status in (LeaveStatus.APPROVED, LeaveStatus.REJECTED):
                # The constraint requires both fields or neither.
                approver = dept_manager.get(p.department_id) or head
                if approver.id == p.id:
                    approver = head
                decided_by = approver.id
                decided_at = datetime.combine(
                    s - timedelta(days=r.randint(1, 10)), time(r.randint(8, 17), 0)
                ).replace(tzinfo=timezone.utc)

            if status == LeaveStatus.APPROVED:
                for d in working_days(s, e):
                    # ~6% of approved leave never makes it onto the timesheet.
                    if r.random() > 0.06:
                        on_leave.setdefault(p.id, set()).add(d)

            leave_rows.append(
                {
                    "employee_id": p.id,
                    "leave_type": rng.weighted(
                        [
                            LeaveType.ANNUAL,
                            LeaveType.SICK,
                            LeaveType.UNPAID,
                            LeaveType.PARENTAL,
                            LeaveType.BEREAVEMENT,
                        ],
                        [0.55, 0.28, 0.08, 0.06, 0.03],
                    ),
                    "status": status,
                    "start_date": s,
                    "end_date": e,
                    "days": Decimal(str(float(length))),
                    "reason": fake.sentence(nb_words=6),
                    "decided_by_id": decided_by,
                    "decided_at": decided_at,
                    "decision_note": None,
                }
            )
    say(f"  leave requests   {len(leave_rows)}")
    _chunked_insert(LeaveRequest, leave_rows)

    # ---- attendance + tasks ---------------------------------------------
    att_rows: list[dict] = []
    task_rows: list[dict] = []

    for p in people:
        start = max(window_start, p.join_date)
        end = min(today, p.exit_date or today)
        if start > end:
            continue

        # Each person has their own habits - an early riser stays an early riser.
        base_in = r.choice([7, 8, 8, 8, 9, 9])
        base_in_min = r.choice([0, 15, 30, 45])
        typical_day = r.uniform(7.6, 9.2)
        remote_bias = r.random()
        my_leave = on_leave.get(p.id, set())

        # People are staffed on one or two projects, not all twelve. Without
        # this every project ends up with near-identical hours, which is the
        # giveaway that the data is random rather than modelled.
        pool = [pr for pr in projects if pr.start_date <= end]
        primary = r.sample(pool, k=min(len(pool), r.choice([1, 1, 2, 2, 3])))

        for day in working_days(start, end):
            if day in holidays:
                att_rows.append(
                    {
                        "employee_id": p.id,
                        "work_date": day,
                        "clock_in": None,
                        "clock_out": None,
                        "status": AttendanceStatus.HOLIDAY,
                        "notes": "Public holiday",
                    }
                )
                continue

            if day in my_leave:
                # Approved leave, so the timesheet has to agree with it.
                att_rows.append(
                    {
                        "employee_id": p.id,
                        "work_date": day,
                        "clock_in": None,
                        "clock_out": None,
                        "status": AttendanceStatus.LEAVE,
                        "notes": None,
                    }
                )
                continue

            roll = r.random()
            if roll < 0.018:
                status = AttendanceStatus.ABSENT
            elif roll < 0.018 + 0.28 * remote_bias:
                status = AttendanceStatus.REMOTE
            else:
                status = AttendanceStatus.PRESENT

            if not status.is_worked:
                att_rows.append(
                    {
                        "employee_id": p.id,
                        "work_date": day,
                        "clock_in": None,
                        "clock_out": None,
                        "status": status,
                        "notes": None,
                    }
                )
                continue

            in_min = max(0, base_in * 60 + base_in_min + int(r.gauss(0, 22)))
            worked_min = int(max(240, r.gauss(typical_day * 60, 55)))
            out_min = min(23 * 60 + 30, in_min + worked_min)

            # About 1 in 80 days somebody forgets to clock out. This is not
            # noise for its own sake - it is the case the reports have to cope
            # with, and the reason hours_worked returns 0 rather than crashing.
            forgot = r.random() < 0.0125

            att_rows.append(
                {
                    "employee_id": p.id,
                    "work_date": day,
                    "clock_in": time(in_min // 60, in_min % 60),
                    "clock_out": None if forgot else time(out_min // 60, out_min % 60),
                    "status": status,
                    "notes": "No clock-out recorded" if forgot else None,
                }
            )

            # Mostly their own projects; occasionally they get pulled onto
            # something else.
            candidates = [
                pr
                for pr in primary
                if pr.start_date <= day and (pr.end_date is None or pr.end_date >= day)
            ]
            if not candidates or r.random() < 0.015:
                candidates = [
                    pr
                    for pr in projects
                    if pr.start_date <= day and (pr.end_date is None or pr.end_date >= day)
                ] or candidates
            if not candidates:
                continue
            hours_available = round((out_min - in_min) / 60, 2)
            n_tasks = r.choices([1, 2, 3], weights=[0.45, 0.4, 0.15], k=1)[0]
            remaining = hours_available
            for i in range(n_tasks):
                if remaining < 0.5:
                    break
                share = remaining if i == n_tasks - 1 else round(remaining * r.uniform(0.3, 0.7), 2)
                share = max(0.5, min(share, remaining, 12.0))
                proj = r.choice(candidates)
                task_rows.append(
                    {
                        "employee_id": p.id,
                        "project_id": proj.id,
                        "task_date": day,
                        "hours": Decimal(str(round(share, 2))),
                        "description": r.choice(TASK_TEMPLATES).format(
                            n=r.randint(3, 60),
                            client=proj.client or "client",
                            site=proj.site or "site",
                        ),
                        "remarks": None if r.random() < 0.82 else fake.sentence(nb_words=8),
                    }
                )
                remaining = round(remaining - share, 2)

    say(f"  attendance       {len(att_rows)} rows (inserting...)")
    _chunked_insert(AttendanceLog, att_rows)
    say(f"  tasks            {len(task_rows)} rows (inserting...)")
    _chunked_insert(Task, task_rows)

    # ---- performance reviews --------------------------------------------
    review_rows = []
    seen: set[tuple[int, date, date]] = set()
    for p in people:
        if p.role == Role.HR_ADMIN:
            continue
        reviewer = (p.manager_id and db.session.get(Employee, p.manager_id)) or head
        if reviewer.id == p.id:
            continue
        for years_ago in (1, 0):
            period_start = date(today.year - years_ago - 1, 7, 1)
            period_end = date(today.year - years_ago, 6, 30)
            if period_end < p.join_date or period_start > today:
                continue
            key = (p.id, period_start, period_end)
            if key in seen:
                continue
            seen.add(key)
            done = period_end < today
            review_rows.append(
                {
                    "employee_id": p.id,
                    "reviewer_id": reviewer.id,
                    "period_start": period_start,
                    "period_end": period_end,
                    "status": ReviewStatus.ACKNOWLEDGED if done else ReviewStatus.DRAFT,
                    "rating": r.choices([2, 3, 4, 5], weights=[0.07, 0.33, 0.44, 0.16], k=1)[0]
                    if done
                    else None,
                    "strengths": fake.sentence(nb_words=14) if done else None,
                    "improvements": fake.sentence(nb_words=12) if done else None,
                    "employee_comment": fake.sentence(nb_words=10)
                    if done and r.random() < 0.5
                    else None,
                }
            )
    say(f"  reviews          {len(review_rows)}")
    _chunked_insert(PerformanceReview, review_rows)

    counts = {
        "departments": len(depts),
        "projects": len(projects),
        "employees": len(people),
        "attendance_logs": len(att_rows),
        "tasks": len(task_rows),
        "leave_requests": len(leave_rows),
        "performance_reviews": len(review_rows),
    }
    say("\nDone. Sign in as any username with password 'demo12345'.")
    say(f"HR admin: {head.username}")
    return counts
