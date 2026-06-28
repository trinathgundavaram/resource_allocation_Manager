"""
seed.py
-------
Populate the database with realistic sample data so the application is usable
on first launch. Safe to call repeatedly — it only seeds when the DB is empty.
"""

import datetime as _dt

import database as db
import logic
from working_days import months_between, month_index


def is_empty():
    row = db.query_one("SELECT COUNT(*) AS c FROM resources")
    return row["c"] == 0


def _insert(sql, params):
    return db.execute(sql, params)


def seed(force=False):
    db.init_db()
    if not force and not is_empty():
        return False

    now = db.now_iso()
    user = "seed"
    year = _dt.date.today().year  # current year (2026)

    # ----- Roles -----
    roles = {}
    for name in ["Architect", "Senior Engineer", "Engineer", "Designer",
                 "QA Engineer", "Project Manager"]:
        roles[name] = _insert(
            "INSERT INTO roles (name, created_at) VALUES (?,?)", (name, now))

    # ----- Clients -----
    clients = {}
    for name in ["Acme Corp", "Globex", "Initech", "Umbrella Inc", "Internal"]:
        clients[name] = _insert(
            "INSERT INTO clients (name, created_at) VALUES (?,?)", (name, now))

    # ----- Managers -----
    managers = {}
    for name in ["Alice Morgan", "Bob Chen", "Carol Diaz"]:
        managers[name] = _insert(
            "INSERT INTO managers (name, created_at) VALUES (?,?)", (name, now))

    # ----- Holidays (current year, US-ish sample) -----
    holidays = [
        (f"{year}-01-01", "New Year's Day"),
        (f"{year}-05-25", "Memorial Day"),
        (f"{year}-07-03", "Independence Day (obs)"),
        (f"{year}-09-07", "Labor Day"),
        (f"{year}-11-26", "Thanksgiving"),
        (f"{year}-12-25", "Christmas Day"),
    ]
    for d, nm in holidays:
        _insert("INSERT INTO holidays (holiday_date, name, created_at) VALUES (?,?,?)",
                (d, nm, now))

    # ----- Resources + billing rates -----
    resources_def = [
        ("Nina Patel", "Architect", 8, 5, 185, "Alice Morgan"),
        ("Omar Farah", "Senior Engineer", 8, 5, 150, "Alice Morgan"),
        ("Priya Sharma", "Senior Engineer", 8, 5, 150, "Bob Chen"),
        ("Quinn Lee", "Engineer", 8, 5, 110, "Bob Chen"),
        ("Rafael Gomez", "Engineer", 8, 5, 110, "Bob Chen"),
        ("Sara Kim", "Designer", 8, 5, 120, "Carol Diaz"),
        ("Tom Becker", "QA Engineer", 8, 5, 95, "Carol Diaz"),
        ("Uma Reddy", "Project Manager", 8, 5, 140, "Carol Diaz"),
    ]
    resources = {}
    for nm, role, hpd, dpw, rate, mgr in resources_def:
        rid = _insert(
            """INSERT INTO resources (name, role_id, manager_id, hours_per_day,
               days_per_week, status, created_at, created_by)
               VALUES (?,?,?,?,?,?,?,?)""",
            (nm, roles[role], managers[mgr], hpd, dpw, "ACTIVE", now, user))
        resources[nm] = rid
        _insert(
            """INSERT INTO resource_billing_rates
               (resource_id, rate, effective_from_date, created_by, created_at)
               VALUES (?,?,?,?,?)""",
            (rid, rate, f"{year}-01-01", user, now))

    # A mid-year rate bump for one resource (to exercise as-of logic).
    _insert(
        """INSERT INTO resource_billing_rates
           (resource_id, rate, effective_from_date, created_by, created_at)
           VALUES (?,?,?,?,?)""",
        (resources["Omar Farah"], 165, f"{year}-07-01", user, now))

    # ----- Projects -----
    def add_project(name, code, client, is_baseline, sm, sy, em, ey, status, color,
                    priority, lead, budget, notes):
        pid = _insert(
            """INSERT INTO projects (name, code, client_id, is_baseline, start_month,
               start_year, end_month, end_year, status, color, priority,
               project_lead_id, notes, created_at, created_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name, code, clients[client], 1 if is_baseline else 0, sm, sy, em, ey,
             status, color, priority, managers[lead], notes, now, user))
        if budget:
            _insert(
                """INSERT INTO project_budgets (project_id, budget_amount,
                   effective_from_date, note, created_by, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (pid, budget, f"{sy}-{sm:02d}-01", "Initial budget", user, now))
        _insert(
            """INSERT INTO project_status_history (project_id, old_status,
               new_status, changed_at, changed_by, reason)
               VALUES (?,?,?,?,?,?)""",
            (pid, None, status, now, user, "Seeded"))
        return pid

    projects = {}
    # Baselines (READY_TO_USE so they show up everywhere).
    projects["Bench / Internal"] = add_project(
        "Bench / Internal", "BENCH", "Internal", True, 1, year, 12, year,
        "READY_TO_USE", "#90A4AE", "LOW", "Carol Diaz", 0,
        "Catch-all baseline absorbing unallocated capacity.")
    projects["Platform Maintenance"] = add_project(
        "Platform Maintenance", "PLAT-MAINT", "Internal", True, 1, year, 12, year,
        "READY_TO_USE", "#78909C", "MEDIUM", "Bob Chen", 600000,
        "Ongoing keep-the-lights-on baseline work.")

    # Delivery projects (READY_TO_USE non-baseline).
    projects["Acme Portal"] = add_project(
        "Acme Portal Rebuild", "ACME-PORT", "Acme Corp", False, 3, year, 10, year,
        "READY_TO_USE", "#4C78A8", "HIGH", "Alice Morgan", 850000,
        "Customer portal modernization.")
    projects["Globex Mobile"] = add_project(
        "Globex Mobile App", "GLBX-MOB", "Globex", False, 4, year, 11, year,
        "READY_TO_USE", "#F58518", "HIGH", "Bob Chen", 720000,
        "Cross-platform mobile build.")
    projects["Initech Data"] = add_project(
        "Initech Data Migration", "INIT-DATA", "Initech", False, 5, year, 8, year,
        "READY_TO_USE", "#54A24B", "MEDIUM", "Carol Diaz", 300000,
        "Legacy data lift-and-shift.")

    # Pipeline projects in various pre-ready statuses.
    add_project("Umbrella Analytics", "UMB-ANL", "Umbrella Inc", False, 8, year, 12, year,
                "APPROVED", "#E45756", "MEDIUM", "Alice Morgan", 400000,
                "Analytics platform — approved, not yet ready.")
    add_project("Acme AI Assistant", "ACME-AI", "Acme Corp", False, 9, year, 12, year,
                "GATE_1", "#72B7B2", "HIGH", "Bob Chen", 500000,
                "Exploratory AI assistant.")
    add_project("Globex IoT", "GLBX-IOT", "Globex", False, 10, year, 12, year,
                "ESTIMATE", "#B279A2", "LOW", "Carol Diaz", 0,
                "Early estimate stage.")

    # Project assumptions + a budget amendment for Acme Portal.
    _insert(
        """INSERT INTO project_assumptions (project_id, content, created_at, created_by)
           VALUES (?,?,?,?)""",
        (projects["Acme Portal"], "Assumes 2 senior engineers for full duration.",
         now, "Alice Morgan"))
    _insert(
        """INSERT INTO project_budgets (project_id, budget_amount,
           effective_from_date, note, created_by, created_at)
           VALUES (?,?,?,?,?,?)""",
        (projects["Acme Portal"], 920000, f"{year}-06-01",
         "Scope increase: added reporting module.", "Alice Morgan", now))

    # ----- Baseline allocations: everyone 100% on Bench/Internal all year -----
    bench = projects["Bench / Internal"]
    for rid in resources.values():
        for (y, m) in months_between(year, 1, year, 12):
            logic.set_baseline_allocation(rid, bench, y, m, user, "initial baseline")

    # ----- Sample non-baseline assignments -----
    def assign(res_name, proj_name, sm, em, pct):
        rid = resources[res_name]
        pid = projects[proj_name]
        mp = {(year, m): pct for (yy, m) in months_between(year, sm, year, em)
              for yy2 in [yy] if yy == year}
        # build month_pct only for year==year months
        mp = {}
        for (yy, m) in months_between(year, sm, year, em):
            mp[(yy, m)] = pct
        choice = {k: "__split__" for k in mp}
        at_open = {k: logic.baseline_pool(rid, k[0], k[1]) for k in mp}
        logic.assign_project(rid, pid, mp, choice, at_open, user, "seed assignment")

    # Acme Portal team
    assign("Nina Patel", "Acme Portal", 3, 10, 30)
    assign("Omar Farah", "Acme Portal", 3, 10, 60)
    assign("Quinn Lee", "Acme Portal", 4, 9, 50)
    assign("Sara Kim", "Acme Portal", 3, 7, 40)
    assign("Tom Becker", "Acme Portal", 6, 10, 35)

    # Globex Mobile team
    assign("Priya Sharma", "Globex Mobile", 4, 11, 70)
    assign("Rafael Gomez", "Globex Mobile", 4, 11, 55)
    assign("Sara Kim", "Globex Mobile", 8, 11, 30)
    assign("Uma Reddy", "Globex Mobile", 4, 11, 25)

    # Initech Data team
    assign("Quinn Lee", "Initech Data", 5, 8, 25)
    assign("Rafael Gomez", "Initech Data", 5, 8, 30)
    assign("Tom Becker", "Initech Data", 5, 8, 20)

    # Some Platform Maintenance load (a second baseline carrying real %).
    # Move 15% of a couple people onto Platform Maintenance for mid-year.
    # (Handled by reassigning baseline carrier for a few months.)

    # ----- Make assignment dates realistic for the weekly-hours export -----
    # Most allocations are treated as set up at the start of the year (so they
    # spread evenly across every week). One assignment is deliberately added
    # mid-month (Tom → Acme Portal on 19 Jun) to demonstrate that its hours land
    # only on the remaining weeks of June, leaving the already-submitted weeks
    # untouched.
    db.execute("UPDATE allocations SET assigned_date=? WHERE assigned_date IS NOT NULL",
               (f"{year}-01-01",))
    tom = resources["Tom Becker"]
    acme = projects["Acme Portal"]
    db.execute(
        "UPDATE allocations SET assigned_date=? WHERE resource_id=? AND project_id=? AND year=? AND month=6",
        (f"{year}-06-19", tom, acme, year))

    db.set_setting("seeded_at", now)
    db.set_setting("last_backup_at", "")
    return True


if __name__ == "__main__":
    created = seed(force=True)
    print("Seeded:", created)
    print("Resources:", db.query_one("SELECT COUNT(*) c FROM resources")["c"])
    print("Projects:", db.query_one("SELECT COUNT(*) c FROM projects")["c"])
    print("Allocations:", db.query_one("SELECT COUNT(*) c FROM allocations WHERE is_active=1")["c"])
    # Verify 100% rule for a sample resource/month.
    r = db.query_one("SELECT id FROM resources LIMIT 1")["id"]
    yr = _dt.date.today().year
    print("Sample total (res1, May):", logic.resource_month_total(r, yr, 5))
