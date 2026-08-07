"""Dashboards, the reconciliation report, and exports."""

from __future__ import annotations

import io
from datetime import date, timedelta
from typing import Any

from flask import Blueprint, abort, render_template, request, send_file

from app.analytics import reconciliation as recon
from app.analytics import reports as rp
from app.security import manager_required

bp = Blueprint("reports", __name__)

MAX_EXPORT_DAYS = 400


def _range_from_args() -> tuple[date, date]:
    end = date.today()
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30
    days = max(1, min(days, MAX_EXPORT_DAYS))
    return end - timedelta(days=days), end


@bp.route("/")
@manager_required
def dashboard() -> Any:
    return render_template(
        "reports/dashboard.html",
        headline=rp.headline(),
        departments=rp.headcount_by_department(),
        dept_hours=rp.department_hours(),
        months=rp.attendance_by_month(),
        projects=rp.project_utilisation(),
        leave=rp.leave_by_type(),
        top=rp.top_hours(),
        overtime=rp.overtime_days(),
    )


@bp.route("/reconciliation")
@manager_required
def reconciliation() -> Any:
    return render_template("reports/reconciliation.html", report=recon.run_all())


@bp.route("/export/timesheet.<fmt>")
@manager_required
def export_timesheet(fmt: str) -> Any:
    if fmt not in {"csv", "xlsx"}:
        abort(404)

    start, end = _range_from_args()
    rows = rp.timesheet_export(start, end)
    name = f"timesheet_{start.isoformat()}_{end.isoformat()}"
    return _send(rows, fmt, name, sheet="Timesheet")


@bp.route("/export/reconciliation.<fmt>")
@manager_required
def export_reconciliation(fmt: str) -> Any:
    if fmt not in {"csv", "xlsx"}:
        abort(404)

    rows = recon.to_rows(recon.run_all())
    return _send(rows, fmt, f"reconciliation_{date.today().isoformat()}", sheet="Findings")


def _send(rows: list[dict[str, Any]], fmt: str, stem: str, *, sheet: str) -> Any:
    """Serialise to CSV or Excel and return as a download.

    pandas is doing real work here - handling quoting, encoding and the Excel
    writer - which is the opposite of using it to group data the database
    could group itself.
    """
    import pandas as pd

    frame = pd.DataFrame(rows)
    buffer = io.BytesIO()

    if fmt == "csv":
        buffer.write(frame.to_csv(index=False).encode("utf-8"))
        mimetype = "text/csv"
    else:
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            frame.to_excel(writer, index=False, sheet_name=sheet)
            # Widen columns so the file is readable without manual resizing.
            worksheet = writer.sheets[sheet]
            for i, column in enumerate(frame.columns, start=1):
                width = max(
                    len(str(column)), *(frame[column].astype(str).str.len().tolist() or [0])
                )
                worksheet.column_dimensions[
                    worksheet.cell(row=1, column=i).column_letter
                ].width = min(width + 2, 50)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    buffer.seek(0)
    return send_file(buffer, mimetype=mimetype, as_attachment=True, download_name=f"{stem}.{fmt}")
