"""
logic.py
--------
Business rules for the Resource Allocation Management application.

Everything that is *policy* rather than *storage* lives here:

  * Allocation maths (100% rule, 5% increments, baseline remainder).
  * Assignment save path (modes, concurrent-edit protection, transactions,
    audit history).
  * Project lifecycle (allowed status transitions, auto-close, end warnings).
  * Financial calculations (cost, budget as-of, projections, variance).
  * Annual reset / archive.

The UI layer never writes allocation rows directly — it always goes through
``assign_project`` / ``remove_assignment`` so the invariants below hold.
"""

import datetime as _dt

import database as db
from working_days import (
    working_hours,
    last_day_of_month,
    months_between,
    month_index,
    month_label,
    month_weeks,
    add_months,
)

# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class ValidationError(Exception):
    """Raised when a save violates a business rule (rolls back the txn)."""


class ConcurrentEditError(Exception):
    """Raised when the baseline changed in the DB since the panel was opened."""


# --------------------------------------------------------------------------- #
# Lifecycle constants
# --------------------------------------------------------------------------- #
MAIN_FLOW = [
    "ESTIMATE", "GATE_0", "GATE_1", "APPROVED",
    "ALLOCATED", "READY_TO_USE", "CLOSED",
]
TERMINAL_ANY = ["CANCELLED", "DENIED"]
ALL_STATUSES = MAIN_FLOW + TERMINAL_ANY + ["NOT_ALLOCATED"]

# Status that makes a project appear in grid / project view / availability /
# assignment dropdowns.
USABLE_STATUS = "READY_TO_USE"

STATUS_COLORS = {
    "ESTIMATE": "#9E9E9E",
    "GATE_0": "#7E57C2",
    "GATE_1": "#5C6BC0",
    "APPROVED": "#42A5F5",
    "ALLOCATED": "#26A69A",
    "READY_TO_USE": "#66BB6A",
    "CLOSED": "#8D6E63",
    "CANCELLED": "#EF5350",
    "DENIED": "#E53935",
    "NOT_ALLOCATED": "#FFA726",
}


def allowed_transitions(current):
    """A project may move directly to any other status (no forced step order)."""
    return [s for s in ALL_STATUSES if s != current]


# --------------------------------------------------------------------------- #
# Percentage helpers
# --------------------------------------------------------------------------- #
def snap5(value):
    """Round to the nearest multiple of 5."""
    return int(round(float(value) / 5.0) * 5)


def is_multiple_of_5(value):
    return abs(value - round(value / 5.0) * 5) < 1e-9


# --------------------------------------------------------------------------- #
# Reference-data accessors
# --------------------------------------------------------------------------- #
_HOLIDAY_CACHE = None


def get_holiday_dates():
    """Holiday dates, memoized per process.

    Called once per resource×month inside working-hour maths, so with 50+
    resources this would otherwise issue hundreds of identical queries per
    page render. Call ``clear_holiday_cache()`` after editing holidays.
    """
    global _HOLIDAY_CACHE
    if _HOLIDAY_CACHE is None:
        _HOLIDAY_CACHE = [r["holiday_date"]
                          for r in db.query("SELECT holiday_date FROM holidays")]
    return _HOLIDAY_CACHE


def clear_holiday_cache():
    global _HOLIDAY_CACHE
    _HOLIDAY_CACHE = None


def get_roles():
    return db.query("SELECT * FROM roles ORDER BY name")


def get_clients():
    return db.query("SELECT * FROM clients ORDER BY name")


def get_managers():
    return db.query("SELECT * FROM managers ORDER BY name")


def get_resources(active_only=False):
    sql = "SELECT * FROM resources"
    if active_only:
        sql += " WHERE status = 'ACTIVE'"
    sql += " ORDER BY name"
    return db.query(sql)


def get_resource(resource_id):
    return db.query_one("SELECT * FROM resources WHERE id = ?", (resource_id,))


def get_projects(usable_only=False, include_closed=True):
    sql = "SELECT * FROM projects"
    clauses = []
    if usable_only:
        clauses.append(f"status = '{USABLE_STATUS}'")
    if not include_closed:
        clauses.append("status NOT IN ('CLOSED','CANCELLED','DENIED')")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY is_baseline DESC, name"
    return db.query(sql)


def get_project(project_id):
    return db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))


def project_label(project):
    """Display label with the project code prefixed, e.g. '[ACME-PORT] Acme Portal'."""
    code = (project["code"] or "").strip() if "code" in project.keys() else ""
    return f"[{code}] {project['name']}" if code else project["name"]


def get_baseline_projects(usable_only=True):
    sql = "SELECT * FROM projects WHERE is_baseline = 1"
    if usable_only:
        sql += f" AND status = '{USABLE_STATUS}'"
    return db.query(sql + " ORDER BY name")


def role_name(role_id):
    r = db.query_one("SELECT name FROM roles WHERE id = ?", (role_id,))
    return r["name"] if r else "—"


def client_name(client_id):
    r = db.query_one("SELECT name FROM clients WHERE id = ?", (client_id,))
    return r["name"] if r else "—"


def manager_name(manager_id):
    r = db.query_one("SELECT name FROM managers WHERE id = ?", (manager_id,))
    return r["name"] if r else "—"


# --------------------------------------------------------------------------- #
# As-of rate / budget
# --------------------------------------------------------------------------- #
def billing_rate_for_month(resource_id, year, month):
    """Latest billing rate effective on or before the last day of the month."""
    last = last_day_of_month(year, month).isoformat()
    row = db.query_one(
        """SELECT rate FROM resource_billing_rates
           WHERE resource_id = ? AND effective_from_date <= ?
           ORDER BY effective_from_date DESC, id DESC LIMIT 1""",
        (resource_id, last),
    )
    return float(row["rate"]) if row else 0.0


def budget_for_month(project_id, year, month):
    """Latest OVERALL budget effective on or before the last day of the month.

    Only considers overall amendments (budget_year IS NULL); per-year budgets
    are read via ``annual_budget``."""
    last = last_day_of_month(year, month).isoformat()
    row = db.query_one(
        """SELECT budget_amount FROM project_budgets
           WHERE project_id = ? AND budget_year IS NULL AND effective_from_date <= ?
           ORDER BY effective_from_date DESC, id DESC LIMIT 1""",
        (project_id, last),
    )
    return float(row["budget_amount"]) if row else 0.0


def budget_as_of(project_id, as_of_date):
    """Latest OVERALL budget effective on or before an arbitrary as-of date."""
    row = db.query_one(
        """SELECT budget_amount FROM project_budgets
           WHERE project_id = ? AND budget_year IS NULL AND effective_from_date <= ?
           ORDER BY effective_from_date DESC, id DESC LIMIT 1""",
        (project_id, as_of_date),
    )
    return float(row["budget_amount"]) if row else 0.0


def annual_budget(project_id, year):
    """Budget for a specific calendar year.

    Uses the latest per-year amendment (budget_year == year) if one exists;
    otherwise falls back to the project's overall budget as of that year-end.
    """
    last = last_day_of_month(year, 12).isoformat()
    row = db.query_one(
        """SELECT budget_amount FROM project_budgets
           WHERE project_id = ? AND budget_year = ? AND effective_from_date <= ?
           ORDER BY effective_from_date DESC, id DESC LIMIT 1""",
        (project_id, year, last),
    )
    if row:
        return float(row["budget_amount"]), True   # explicit per-year budget
    return budget_for_month(project_id, year, 12), False  # fallback to overall


def resource_working_hours(resource_id, year, month):
    res = get_resource(resource_id)
    if not res:
        return 0.0
    return working_hours(
        month, year, res["hours_per_day"], res["days_per_week"],
        get_holiday_dates(),
    )


def monthly_cost(resource_id, project_id, year, month):
    """working_hours * (percentage/100) * billing_rate for one alloc cell."""
    alloc = db.query_one(
        """SELECT percentage FROM allocations
           WHERE resource_id=? AND project_id=? AND year=? AND month=? AND is_active=1""",
        (resource_id, project_id, year, month),
    )
    if not alloc:
        return 0.0
    pct = float(alloc["percentage"])
    hrs = resource_working_hours(resource_id, year, month)
    rate = billing_rate_for_month(resource_id, year, month)
    return hrs * (pct / 100.0) * rate


# --------------------------------------------------------------------------- #
# Allocation reads
# --------------------------------------------------------------------------- #
def get_month_allocations(resource_id, year, month, conn=None):
    """All active allocation rows for a resource in a month, with project meta."""
    sql = """
        SELECT a.*, p.name AS project_name, p.is_baseline, p.status AS project_status
        FROM allocations a JOIN projects p ON p.id = a.project_id
        WHERE a.resource_id=? AND a.year=? AND a.month=? AND a.is_active=1
        ORDER BY p.is_baseline DESC, p.name
    """
    if conn is not None:
        return conn.execute(sql, (resource_id, year, month)).fetchall()
    return db.query(sql, (resource_id, year, month))


def resource_month_total(resource_id, year, month, conn=None):
    rows = get_month_allocations(resource_id, year, month, conn)
    return sum(float(r["percentage"]) for r in rows)


def baseline_pool(resource_id, year, month, conn=None):
    """Sum of percentages currently sitting on baseline projects (the pool
    available to pull non-baseline work from)."""
    rows = get_month_allocations(resource_id, year, month, conn)
    return sum(float(r["percentage"]) for r in rows if r["is_baseline"])


def baseline_rows(resource_id, year, month, conn=None):
    rows = get_month_allocations(resource_id, year, month, conn)
    return [r for r in rows if r["is_baseline"]]


def get_allocation_value(resource_id, project_id, year, month, conn=None):
    sql = """SELECT percentage FROM allocations
             WHERE resource_id=? AND project_id=? AND year=? AND month=? AND is_active=1"""
    if conn is not None:
        row = conn.execute(sql, (resource_id, project_id, year, month)).fetchone()
    else:
        row = db.query_one(sql, (resource_id, project_id, year, month))
    return float(row["percentage"]) if row else 0.0


def available_for_new(resource_id, year, month, target_project_id=None, conn=None):
    """How much % a non-baseline project may take this month.

    Equal to the current baseline pool plus whatever the target project
    already holds (since re-assigning frees its current share first).
    """
    pool = baseline_pool(resource_id, year, month, conn)
    existing = 0.0
    if target_project_id is not None:
        existing = get_allocation_value(resource_id, target_project_id, year, month, conn)
    return pool + existing


# --------------------------------------------------------------------------- #
# History helper
# --------------------------------------------------------------------------- #
def _record_history(conn, allocation_id, resource_id, project_id, year, month,
                    old_pct, new_pct, change_type, user, reason):
    conn.execute(
        """INSERT INTO allocation_history
           (allocation_id, resource_id, project_id, year, month,
            old_percentage, new_percentage, change_type, changed_at, changed_by, reason)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (allocation_id, resource_id, project_id, year, month,
         old_pct, new_pct, change_type, db.now_iso(), user, reason),
    )


def _upsert_allocation(conn, resource_id, project_id, year, month, pct, user):
    """Insert or update an allocation row; deactivate if pct == 0. Returns id."""
    existing = conn.execute(
        """SELECT id, percentage FROM allocations
           WHERE resource_id=? AND project_id=? AND year=? AND month=?""",
        (resource_id, project_id, year, month),
    ).fetchone()
    now = db.now_iso()
    if existing:
        active = 1 if pct > 0 else 0
        conn.execute(
            """UPDATE allocations SET percentage=?, is_active=?, last_modified_at=?,
               last_modified_by=? WHERE id=?""",
            (pct, active, now, user, existing["id"]),
        )
        return existing["id"], float(existing["percentage"])
    else:
        if pct <= 0:
            return None, 0.0
        cur = conn.execute(
            """INSERT INTO allocations
               (resource_id, project_id, year, month, percentage,
                assigned_date, assigned_by, last_modified_at, last_modified_by, is_active)
               VALUES (?,?,?,?,?,?,?,?,?,1)""",
            (resource_id, project_id, year, month, pct,
             now, user, now, user),
        )
        return cur.lastrowid, 0.0


# --------------------------------------------------------------------------- #
# Assignment validation (no DB writes) — used for live preview
# --------------------------------------------------------------------------- #
def validate_assignment(resource_id, project_id, month_pct, baseline_choice,
                        project, conn=None):
    """Validate a planned assignment for a set of months.

    Parameters
    ----------
    month_pct      : dict {(year, month): new_pct}
    baseline_choice: dict {(year, month): baseline_project_id or "__split__"}
    project        : the target project row (must not be baseline).

    Returns
    -------
    dict with keys:
        ok            : bool
        errors        : list[str]   (hard blocks)
        warnings      : list[str]   (soft)
        preview       : dict {(y,m): {...per-month preview...}}
    """
    errors, warnings = [], []
    preview = {}

    if project["is_baseline"]:
        errors.append("Baseline project % is calculated automatically and "
                      "cannot be entered manually.")
        return {"ok": False, "errors": errors, "warnings": warnings, "preview": preview}

    pstart = month_index(project["start_year"], project["start_month"])
    pend = month_index(project["end_year"], project["end_month"])

    for (y, m), new_pct in sorted(month_pct.items()):
        cell = {"pct": new_pct}
        midx = month_index(y, m)

        # Rule 7: allocation window inside project window.
        if midx < pstart or midx > pend:
            errors.append(f"{month_label(y, m)}: outside project window "
                          f"({month_label(project['start_year'], project['start_month'])}"
                          f"–{month_label(project['end_year'], project['end_month'])}).")
            preview[(y, m)] = cell
            continue

        # 5% increment.
        if not is_multiple_of_5(new_pct):
            errors.append(f"{month_label(y, m)}: {new_pct}% is not a multiple of 5.")

        b_rows = baseline_rows(resource_id, y, m, conn)
        if not b_rows:
            errors.append(f"{month_label(y, m)}: resource has no baseline allocation; "
                          "assign a baseline first.")
            preview[(y, m)] = cell
            continue

        old_pct = get_allocation_value(resource_id, project_id, y, m, conn)
        delta = new_pct - old_pct  # positive = take more from baseline
        pool = baseline_pool(resource_id, y, m, conn)

        if delta > pool + 1e-9:
            errors.append(f"{month_label(y, m)}: needs {delta:.0f}% but only "
                          f"{pool:.0f}% baseline available.")

        choice = baseline_choice.get((y, m), "__split__")
        new_baselines = _plan_baseline(b_rows, delta, choice)
        if new_baselines is None:
            errors.append(f"{month_label(y, m)}: chosen baseline cannot absorb "
                          "the change without going negative.")
        else:
            if any(v < -1e-9 for v in new_baselines.values()):
                errors.append(f"{month_label(y, m)}: a baseline would go negative.")
            remaining = sum(max(0.0, v) for v in new_baselines.values())
            if remaining <= 1e-9:
                warnings.append(f"{month_label(y, m)}: resource will have 0% on all "
                                "baselines.")
            cell["baseline_after"] = new_baselines
            cell["baseline_remaining"] = remaining

        # Financials preview.
        hrs = resource_working_hours(resource_id, y, m)
        rate = billing_rate_for_month(resource_id, y, m)
        cell["hours"] = hrs * (new_pct / 100.0)
        cell["billing"] = hrs * (new_pct / 100.0) * rate
        preview[(y, m)] = cell

    return {"ok": len(errors) == 0, "errors": errors,
            "warnings": warnings, "preview": preview}


def _plan_baseline(b_rows, delta, choice):
    """Compute new baseline percentages after absorbing ``delta``.

    delta > 0 : baseline shrinks (work taken from it).
    delta < 0 : baseline grows (work returned).
    choice    : a baseline project_id (single source) or "__split__".

    Returns dict {project_id: new_pct} or None if impossible.
    """
    current = {r["project_id"]: float(r["percentage"]) for r in b_rows}
    if not current:
        return None

    if choice != "__split__" and choice in current:
        new = dict(current)
        new[choice] = current[choice] - delta
        if new[choice] < -1e-9:
            return None
        return new

    # Split proportionally across baselines.
    total = sum(current.values())
    new = dict(current)
    if delta > 0:
        if total <= 0:
            return None
        for pid, val in current.items():
            new[pid] = val - delta * (val / total)
    else:
        # Returning work: give it back proportionally (or evenly if all zero).
        if total <= 0:
            share = -delta / len(current)
            for pid in current:
                new[pid] = current[pid] + share
        else:
            for pid, val in current.items():
                new[pid] = val - delta * (val / total)
    # round to avoid float dust
    for pid in new:
        new[pid] = round(new[pid], 4)
        if new[pid] < -1e-9:
            return None
    return new


# --------------------------------------------------------------------------- #
# Assignment save (transactional, concurrency-checked)
# --------------------------------------------------------------------------- #
def assign_project(resource_id, project_id, month_pct, baseline_choice,
                   baseline_at_open, user, reason="assignment"):
    """Persist an assignment across one or more months, atomically.

    Parameters
    ----------
    month_pct        : {(year, month): new_pct}
    baseline_choice  : {(year, month): baseline_project_id or "__split__"}
    baseline_at_open : {(year, month): pool_value_when_panel_opened}
                       used for concurrent-edit protection.
    Raises ConcurrentEditError / ValidationError (transaction rolls back).
    """
    project = get_project(project_id)
    if project is None:
        raise ValidationError("Project not found.")
    if project["is_baseline"]:
        raise ValidationError("Cannot manually assign a baseline project.")

    with db.transaction() as conn:
        # --- Concurrent edit check: re-read pool fresh. ---
        for (y, m), open_pool in baseline_at_open.items():
            fresh = baseline_pool(resource_id, y, m, conn)
            if abs(fresh - open_pool) > 1e-6:
                raise ConcurrentEditError(
                    f"Baseline for {month_label(y, m)} changed since you opened "
                    f"the panel (was {open_pool:.0f}%, now {fresh:.0f}%). "
                    "Please reload and try again."
                )

        # --- Validate against fresh state. ---
        v = validate_assignment(resource_id, project_id, month_pct,
                                baseline_choice, project, conn)
        if not v["ok"]:
            raise ValidationError(" ".join(v["errors"]))

        # --- Apply per month. ---
        for (y, m), new_pct in month_pct.items():
            b_rows = baseline_rows(resource_id, y, m, conn)
            old_pct = get_allocation_value(resource_id, project_id, y, m, conn)
            delta = new_pct - old_pct
            choice = baseline_choice.get((y, m), "__split__")
            new_baselines = _plan_baseline(b_rows, delta, choice)
            if new_baselines is None:
                raise ValidationError(f"{month_label(y, m)}: baseline plan failed.")

            # Write the target allocation.
            alloc_id, prev = _upsert_allocation(conn, resource_id, project_id,
                                                y, m, new_pct, user)
            _record_history(conn, alloc_id, resource_id, project_id, y, m,
                            old_pct, new_pct,
                            "EDIT" if old_pct else "CREATE", user, reason)

            # Write the adjusted baselines.
            for bpid, bval in new_baselines.items():
                bval = round(bval, 4)
                bid, bprev = _upsert_allocation(conn, resource_id, bpid, y, m,
                                                bval, user)
                if abs(bprev - bval) > 1e-9:
                    _record_history(conn, bid, resource_id, bpid, y, m,
                                    bprev, bval, "BASELINE_ADJUST", user,
                                    "auto-adjust for " + project["name"])

            # Final invariant: total must be 100.
            total = resource_month_total(resource_id, y, m, conn)
            if abs(total - 100.0) > 0.01:
                raise ValidationError(
                    f"{month_label(y, m)}: total is {total:.1f}%, must be 100%."
                )
        res = get_resource(resource_id)
        labels = ", ".join(month_label(y, m) for (y, m) in sorted(month_pct))
        db.audit_log("ASSIGN", "allocation", project_id,
                     f"Assigned {res['name'] if res else resource_id} to "
                     f"{project['name']} ({labels})", user, conn=conn)
    return True


def remove_assignment(resource_id, project_id, months, baseline_target, user,
                       reason="removed"):
    """Remove a (non-baseline) project allocation for given months, returning
    the freed % to a baseline project.

    months          : list of (year, month)
    baseline_target : baseline project_id to return % to, or "__split__".
    """
    project = get_project(project_id)
    if project and project["is_baseline"]:
        raise ValidationError("Cannot remove a baseline directly; reassign work.")

    with db.transaction() as conn:
        for (y, m) in months:
            old_pct = get_allocation_value(resource_id, project_id, y, m, conn)
            if old_pct <= 0:
                continue
            b_rows = baseline_rows(resource_id, y, m, conn)
            if not b_rows:
                raise ValidationError(
                    f"{month_label(y, m)}: no baseline to return % to."
                )
            # Returning work => delta negative of old_pct.
            new_baselines = _plan_baseline(b_rows, -old_pct, baseline_target)
            if new_baselines is None:
                raise ValidationError(f"{month_label(y, m)}: cannot return %.")

            aid, prev = _upsert_allocation(conn, resource_id, project_id, y, m, 0, user)
            _record_history(conn, aid, resource_id, project_id, y, m,
                            old_pct, 0, "REMOVE", user, reason)
            for bpid, bval in new_baselines.items():
                bid, bprev = _upsert_allocation(conn, resource_id, bpid, y, m,
                                                round(bval, 4), user)
                _record_history(conn, bid, resource_id, bpid, y, m, bprev,
                                round(bval, 4), "BASELINE_ADJUST", user,
                                "absorb freed % from " + (project["name"] if project else "?"))
            total = resource_month_total(resource_id, y, m, conn)
            if abs(total - 100.0) > 0.01:
                raise ValidationError(
                    f"{month_label(y, m)}: total is {total:.1f}% after removal."
                )
        res = get_resource(resource_id)
        labels = ", ".join(month_label(y, m) for (y, m) in months)
        db.audit_log("REMOVE", "allocation", project_id,
                     f"Removed {res['name'] if res else resource_id} from "
                     f"{project['name'] if project else project_id} ({labels})",
                     user, conn=conn)
    return True


def set_baseline_allocation(resource_id, baseline_project_id, year, month, user,
                            reason="baseline setup"):
    """Ensure a resource sits on a baseline for a month at 100% if the month is
    otherwise empty — i.e. put a fresh resource onto a baseline. If the month
    already has allocations, this only makes the chosen project the baseline
    carrier of the remainder.
    """
    with db.transaction() as conn:
        rows = get_month_allocations(resource_id, year, month, conn)
        non_baseline_total = sum(float(r["percentage"]) for r in rows
                                 if not r["is_baseline"])
        remainder = 100.0 - non_baseline_total
        if remainder < 0:
            raise ValidationError("Non-baseline already exceeds 100%.")
        # Zero-out any other baselines and put remainder on chosen one.
        for r in rows:
            if r["is_baseline"] and r["project_id"] != baseline_project_id:
                bid, bprev = _upsert_allocation(conn, resource_id, r["project_id"],
                                                year, month, 0, user)
                _record_history(conn, bid, resource_id, r["project_id"], year,
                                month, bprev, 0, "BASELINE_ADJUST", user, reason)
        aid, prev = _upsert_allocation(conn, resource_id, baseline_project_id,
                                       year, month, remainder, user)
        _record_history(conn, aid, resource_id, baseline_project_id, year, month,
                        prev, remainder, "BASELINE_SET", user, reason)
    return True


# --------------------------------------------------------------------------- #
# Project lifecycle
# --------------------------------------------------------------------------- #
def change_project_status(project_id, new_status, user, reason=""):
    project = get_project(project_id)
    if not project:
        raise ValidationError("Project not found.")
    old = project["status"]
    if new_status == old:
        raise ValidationError("Status unchanged.")
    if new_status not in ALL_STATUSES:
        raise ValidationError(f"Unknown status {new_status}.")
    # Reason is optional; any status may be set directly.
    with db.transaction() as conn:
        conn.execute("UPDATE projects SET status=? WHERE id=?", (new_status, project_id))
        conn.execute(
            """INSERT INTO project_status_history
               (project_id, old_status, new_status, changed_at, changed_by, reason)
               VALUES (?,?,?,?,?,?)""",
            (project_id, old, new_status, db.now_iso(), user, (reason or "").strip() or None),
        )
        summary = f"Status {old} → {new_status} ({project['name']})"
        if (reason or "").strip():
            summary += f" — {reason.strip()}"
        db.audit_log("STATUS", "project", project_id, summary, user, conn=conn)
    return True


def projects_ending_soon(within_days=30, ref_date=None):
    """Projects whose end month-end falls within ``within_days`` of ref_date
    and that are still active."""
    ref = ref_date or _dt.date.today()
    out = []
    for p in get_projects(include_closed=False):
        if p["status"] in ("CLOSED", "CANCELLED", "DENIED"):
            continue
        end = last_day_of_month(p["end_year"], p["end_month"])
        delta = (end - ref).days
        if 0 <= delta <= within_days:
            out.append((p, delta))
    return out


def projects_past_end(ref_date=None):
    """Active projects whose end month has fully passed (candidates for auto-close)."""
    ref = ref_date or _dt.date.today()
    out = []
    for p in get_projects(include_closed=False):
        if p["status"] in ("CLOSED", "CANCELLED", "DENIED"):
            continue
        end = last_day_of_month(p["end_year"], p["end_month"])
        if end < ref:
            out.append(p)
    return out


def close_project(project_id, user, reason="auto-closed (past end date)"):
    """Mark a project CLOSED. Freed % naturally absorbs into baseline next
    month because closed projects are excluded from future grid editing."""
    project = get_project(project_id)
    if not project:
        raise ValidationError("Project not found.")
    with db.transaction() as conn:
        conn.execute("UPDATE projects SET status='CLOSED' WHERE id=?", (project_id,))
        conn.execute(
            """INSERT INTO project_status_history
               (project_id, old_status, new_status, changed_at, changed_by, reason)
               VALUES (?,?,?,?,?,?)""",
            (project_id, project["status"], "CLOSED", db.now_iso(), user, reason),
        )
        db.audit_log("STATUS", "project", project_id,
                     f"Closed {project['name']} — {reason}", user, conn=conn)
    return True


def extend_project(project_id, end_month, end_year, user, reason="extended"):
    project = get_project(project_id)
    if not project:
        raise ValidationError("Project not found.")
    if month_index(end_year, end_month) < month_index(project["start_year"],
                                                       project["start_month"]):
        raise ValidationError("End must be on/after start.")
    db.execute("UPDATE projects SET end_month=?, end_year=? WHERE id=?",
               (end_month, end_year, project_id))
    db.execute(
        """INSERT INTO project_status_history
           (project_id, old_status, new_status, changed_at, changed_by, reason)
           VALUES (?,?,?,?,?,?)""",
        (project_id, project["status"], project["status"], db.now_iso(), user,
         f"{reason}: end -> {month_label(end_year, end_month)}"),
    )
    db.audit_log("UPDATE", "project", project_id,
                 f"Extended {project['name']} end → {month_label(end_year, end_month)}",
                 user)
    return True


# --------------------------------------------------------------------------- #
# Financials
# --------------------------------------------------------------------------- #
def project_month_cost(project_id, year, month):
    """Total cost of a project in a month across all resources (actual)."""
    rows = db.query(
        """SELECT resource_id, percentage FROM allocations
           WHERE project_id=? AND year=? AND month=? AND is_active=1""",
        (project_id, year, month),
    )
    total = 0.0
    for r in rows:
        hrs = resource_working_hours(r["resource_id"], year, month)
        rate = billing_rate_for_month(r["resource_id"], year, month)
        total += hrs * (float(r["percentage"]) / 100.0) * rate
    return total


def project_financials(project_id, as_of_date=None):
    """Month-by-month financial table for a project.

    Returns list of dicts: year, month, label, allocated_pct_total, cost,
    budget, unstaffed(bool).
    """
    project = get_project(project_id)
    if not project:
        return []
    as_of = as_of_date or db.today_iso()
    rows = []
    for (y, m) in months_between(project["start_year"], project["start_month"],
                                 project["end_year"], project["end_month"]):
        allocs = db.query(
            """SELECT resource_id, percentage FROM allocations
               WHERE project_id=? AND year=? AND month=? AND is_active=1""",
            (project_id, y, m),
        )
        pct_total = sum(float(a["percentage"]) for a in allocs)
        cost = project_month_cost(project_id, y, m)
        budget = budget_for_month(project_id, y, m)
        rows.append({
            "year": y, "month": m, "label": month_label(y, m),
            "allocated_pct": pct_total, "cost": cost, "budget": budget,
            "unstaffed": pct_total <= 0,
            "gap": budget if pct_total <= 0 else 0.0,
            "resource_count": len(allocs),
        })
    return rows


def resource_utilization(year, month):
    """Per-resource utilization summary for a month."""
    out = []
    for res in get_resources(active_only=False):
        total = resource_month_total(res["id"], year, month)
        rows = get_month_allocations(res["id"], year, month)
        hrs = resource_working_hours(res["id"], year, month)
        rate = billing_rate_for_month(res["id"], year, month)
        out.append({
            "resource_id": res["id"], "name": res["name"],
            "role": role_name(res["role_id"]),
            "manager": manager_name(res["manager_id"]),
            "allocated_pct": total,
            "available_pct": max(0.0, 100.0 - total),
            "billed_hours": hrs * (total / 100.0),
            "rate": rate,
            "projects": [r["project_name"] for r in rows if r["percentage"] > 0],
            "n_projects": len([r for r in rows if r["percentage"] > 0]),
        })
    return out


def availability(year, month, min_pct=0.0):
    """Resources with available baseline % >= min_pct for a month."""
    out = []
    for res in get_resources(active_only=True):
        pool = baseline_pool(res["id"], year, month)
        if pool + 1e-9 < min_pct:
            continue
        rows = get_month_allocations(res["id"], year, month)
        out.append({
            "resource_id": res["id"], "name": res["name"],
            "role": role_name(res["role_id"]),
            "manager": manager_name(res["manager_id"]),
            "rate": billing_rate_for_month(res["id"], year, month),
            "available_pct": pool,
            "projects": [(r["project_name"], r["percentage"]) for r in rows
                         if not r["is_baseline"] and r["percentage"] > 0],
        })
    out.sort(key=lambda x: x["available_pct"], reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Weekly hours breakdown
# --------------------------------------------------------------------------- #
def _week_eligible(assigned_date, year, month, week):
    """Whether a project (with this allocation assigned_date) should carry hours
    in ``week`` of (year, month).

    Only assignments made *inside the same month* are treated as mid-month
    additions: the project then carries hours from the week that contains its
    assigned_date onward (earlier weeks are considered already submitted).
    Assignments made in any other month spread across all weeks normally.
    """
    if not assigned_date:
        return True
    try:
        d = _dt.date.fromisoformat(str(assigned_date)[:10])
    except ValueError:
        return True
    if (d.year, d.month) != (year, month):
        return True
    # Same month → eligible only from the week that contains/follows the date.
    return week["end"] >= d


def weekly_project_hours(resource_id, year, month, cutoff_date=None):
    """Per-week hours a resource should book to each project in a month.

    Rules:
      * A full Mon–Fri week = hours_per_day × days_per_week hours (40 by default).
        Partial weeks scale by their working-day count.
      * **Week-level locking**: every week whose end date is *before* the
        ``cutoff_date`` (default today) is LOCKED — i.e. already submitted. A
        project keeps its frozen share in the locked weeks it was active for;
        all rebalancing (new projects, extra hours) lands only on the open
        weeks on/after the cutoff. Past weeks are never rewritten.
      * Non-baseline projects spread their monthly hours across their eligible
        weeks (proportional to working days). A project added mid-month only
        loads the weeks from its add-date onward — earlier weeks keep 0 for it.
      * Baseline projects act as the balancer: each week is topped up to full
        capacity with baseline hours, so submitted weeks stay fully booked and a
        newly added project's load lands on the remaining open weeks. Monthly
        totals per project are preserved (baseline is clamped at 0 only in weeks
        deliberately over-loaded past capacity).

    Returns ``(weeks, rows)`` where each week dict additionally carries
    ``capacity`` and ``locked`` (bool), and rows is a list of dicts:
        {project_id, project, is_baseline, week_n, week_label, locked, hours}
    """
    res = get_resource(resource_id)
    if not res:
        return [], []
    cutoff = cutoff_date or _dt.date.today()
    weeks = month_weeks(year, month, get_holiday_dates())
    for w in weeks:
        w["capacity"] = (w["working_days"] * float(res["hours_per_day"])
                         * (float(res["days_per_week"]) / 5.0))
        w["locked"] = w["end"] < cutoff
    month_cap = sum(w["capacity"] for w in weeks)

    allocs = db.query(
        """SELECT a.project_id, a.percentage, a.assigned_date, p.name AS project_name,
                  p.code AS project_code, p.is_baseline
           FROM allocations a JOIN projects p ON p.id = a.project_id
           WHERE a.resource_id=? AND a.year=? AND a.month=? AND a.is_active=1
                 AND a.percentage > 0""",
        (resource_id, year, month))
    nonbaseline = [a for a in allocs if not a["is_baseline"]]
    baselines = [a for a in allocs if a["is_baseline"]]

    rows = []
    nb_week_total = {w["n"]: 0.0 for w in weeks}

    for a in nonbaseline:
        monthly = month_cap * float(a["percentage"]) / 100.0
        elig = [w for w in weeks
                if _week_eligible(a["assigned_date"], year, month, w)]
        elig_cap = sum(w["capacity"] for w in elig) or 0.0
        locked_elig = [w for w in elig if w["locked"]]
        open_elig = [w for w in elig if not w["locked"]]
        open_cap = sum(w["capacity"] for w in open_elig) or 0.0

        # Frozen submitted hours in locked weeks (their proportional share).
        wk_hours = {}
        locked_total = 0.0
        for w in locked_elig:
            h = monthly * w["capacity"] / elig_cap if elig_cap > 0 else 0.0
            wk_hours[w["n"]] = h
            locked_total += h
        # Remaining monthly hours go to the open weeks only (past is untouched).
        remaining = max(0.0, monthly - locked_total)
        for w in open_elig:
            wk_hours[w["n"]] = (remaining * w["capacity"] / open_cap
                                if open_cap > 0 else 0.0)

        for w in weeks:
            h = wk_hours.get(w["n"], 0.0)
            nb_week_total[w["n"]] += h
            rows.append({
                "project_id": a["project_id"], "project": a["project_name"],
                "code": a["project_code"] or "",
                "is_baseline": False, "week_n": w["n"],
                "week_label": w["label"], "locked": w["locked"], "hours": h,
            })

    base_total_pct = sum(float(b["percentage"]) for b in baselines)
    for w in weeks:
        filler = max(0.0, w["capacity"] - nb_week_total[w["n"]])
        for b in baselines:
            if base_total_pct > 0:
                share = float(b["percentage"]) / base_total_pct
            else:
                share = 1.0 / len(baselines) if baselines else 0.0
            rows.append({
                "project_id": b["project_id"], "project": b["project_name"],
                "code": b["project_code"] or "",
                "is_baseline": True, "week_n": w["n"],
                "week_label": w["label"], "locked": w["locked"],
                "hours": filler * share,
            })
    return weeks, rows


# --------------------------------------------------------------------------- #
# Annual reset / archive
# --------------------------------------------------------------------------- #
def has_allocations_for_year(year):
    row = db.query_one(
        "SELECT COUNT(*) AS c FROM allocations WHERE year=? AND is_active=1",
        (year,),
    )
    return row["c"] > 0


def archive_year(year, user="system"):
    """Copy all allocations for ``year`` into allocations_archive then clear them."""
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO allocations_archive
               (archived_year, resource_id, project_id, year, month, percentage,
                assigned_date, assigned_by, last_modified_at, last_modified_by,
                notes, is_active)
               SELECT ?, resource_id, project_id, year, month, percentage,
                assigned_date, assigned_by, last_modified_at, last_modified_by,
                notes, is_active
               FROM allocations WHERE year=?""",
            (year, year),
        )
        conn.execute("DELETE FROM allocations WHERE year=?", (year,))
        conn.execute(
            """INSERT INTO allocation_history
               (allocation_id, resource_id, project_id, year, month,
                old_percentage, new_percentage, change_type, changed_at,
                changed_by, reason)
               VALUES (NULL, 0, 0, ?, 1, NULL, NULL, 'ARCHIVE', ?, ?, ?)""",
            (year, db.now_iso(), user, f"archived allocations for {year}"),
        )
    return True


def maybe_annual_reset(user="system", ref_date=None):
    """If it's a new year and the previous year hasn't been archived yet,
    archive it. Idempotent: guarded by an app_settings flag."""
    ref = ref_date or _dt.date.today()
    prev_year = ref.year - 1
    flag = db.get_setting(f"archived_{prev_year}")
    if flag:
        return False
    if has_allocations_for_year(prev_year):
        archive_year(prev_year, user)
    db.set_setting(f"archived_{prev_year}", "1")
    return True
