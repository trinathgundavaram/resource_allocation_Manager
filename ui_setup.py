"""
ui_setup.py
-----------
Setup screens: Resources, Projects, Clients, Roles, Managers, Holidays.
"""

import datetime as _dt

import pandas as pd
import streamlit as st

import database as db
import logic
from ui_common import clear_after_save
from working_days import MONTH_NAMES


def render(user):
    st.title("⚙️ Setup")
    tabs = st.tabs(["Resources", "Projects", "Roles", "Managers", "Holidays"])
    with tabs[0]:
        _resources(user)
    with tabs[1]:
        _projects(user)
    with tabs[2]:
        _simple_table("roles", "Role", user)
    with tabs[3]:
        _simple_table("managers", "Manager", user)
    with tabs[4]:
        _holidays(user)


# Foreign-key references that must be nulled before a row can be deleted, so
# editing/deleting a Role or Manager never fails on a constraint.
_REF_NULL = {
    "managers": [("projects", "project_lead_id"), ("resources", "manager_id")],
    "roles": [("resources", "role_id")],
    "clients": [("projects", "client_id")],
}


# --------------------------------------------------------------------------- #
# Simple name-only tables (clients / roles / managers)
# --------------------------------------------------------------------------- #
def _simple_table(table, label, user):
    st.subheader(f"{label}s")
    clear_after_save(f"_clr_add_{table}", [f"add_{table}_name"])
    with st.form(f"add_{table}"):
        name = st.text_input(f"New {label} name", key=f"add_{table}_name")
        if st.form_submit_button(f"Add {label}") and name.strip():
            try:
                nid = db.execute(f"INSERT INTO {table} (name, created_at) VALUES (?,?)",
                                 (name.strip(), db.now_iso()))
                db.audit_log("CREATE", table, nid,
                             f"Created {label} '{name.strip()}'", user)
                st.session_state[f"_clr_add_{table}"] = True
                st.success(f"Added {label} '{name}'.")
                st.rerun()
            except Exception as e:
                st.error(f"Could not add: {e}")

    rows = db.query(f"SELECT * FROM {table} ORDER BY name")
    if rows:
        df = pd.DataFrame([dict(r) for r in rows])[["id", "name", "created_at"]]
        st.dataframe(df, use_container_width=True, hide_index=True)
        with st.expander("Rename / delete"):
            ids = {f"{r['name']} (#{r['id']})": r["id"] for r in rows}
            sel = st.selectbox("Select", list(ids.keys()), key=f"sel_{table}")
            new_name = st.text_input("New name", key=f"rn_{table}")
            c1, c2 = st.columns(2)
            if c1.button("Rename", key=f"do_rn_{table}") and new_name.strip():
                db.execute(f"UPDATE {table} SET name=? WHERE id=?",
                           (new_name.strip(), ids[sel]))
                db.audit_log("UPDATE", table, ids[sel],
                             f"Renamed {label} #{ids[sel]} to '{new_name.strip()}'", user)
                st.success("Renamed."); st.rerun()
            refs = _REF_NULL.get(table, [])
            if refs:
                st.caption("Deleting will unlink this from any item that "
                           "references it (the reference is cleared, not deleted).")
            if c2.button("Delete", key=f"do_del_{table}"):
                try:
                    # Clear references first so the FK constraint can't block it.
                    for ref_tbl, ref_col in refs:
                        db.execute(
                            f"UPDATE {ref_tbl} SET {ref_col}=NULL WHERE {ref_col}=?",
                            (ids[sel],))
                    db.execute(f"DELETE FROM {table} WHERE id=?", (ids[sel],))
                    db.audit_log("DELETE", table, ids[sel],
                                 f"Deleted {label} '{sel}'", user)
                    st.success("Deleted."); st.rerun()
                except Exception as e:
                    st.error(f"Could not delete: {e}")
    else:
        st.info(f"No {label.lower()}s yet.")


# --------------------------------------------------------------------------- #
# Resources
# --------------------------------------------------------------------------- #
def _resources(user):
    st.subheader("Resources")
    roles = logic.get_roles()
    role_map = {r["name"]: r["id"] for r in roles}
    managers = logic.get_managers()
    mgr_map = {m["name"]: m["id"] for m in managers}
    if not roles:
        st.warning("Add at least one Role first (Roles tab).")

    clear_after_save("_clr_add_resource", ["add_res_name"])
    with st.expander("➕ Add a resource", expanded=False):
        with st.form("add_resource"):
            name = st.text_input("Name", key="add_res_name")
            role = st.selectbox("Role", list(role_map.keys()) or ["—"])
            mgr = st.selectbox("Manager", ["(none)"] + list(mgr_map.keys()))
            hpd = st.slider("Hours / day", 1.0, 24.0, 8.0, 0.5)
            dpw = st.slider("Days / week", 1.0, 7.0, 5.0, 0.5)
            rate = st.number_input("Initial billing rate (per hour)", 0.0, 100000.0,
                                   100.0, 5.0)
            eff = st.date_input("Rate effective from", _dt.date(_dt.date.today().year, 1, 1))
            status = st.selectbox("Status", ["ACTIVE", "INACTIVE"])
            if st.form_submit_button("Add resource") and name.strip() and role_map:
                rid = db.execute(
                    """INSERT INTO resources (name, role_id, manager_id, hours_per_day,
                       days_per_week, status, created_at, created_by)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (name.strip(), role_map[role], mgr_map.get(mgr), hpd, dpw, status,
                     db.now_iso(), user))
                db.execute(
                    """INSERT INTO resource_billing_rates (resource_id, rate,
                       effective_from_date, created_by, created_at) VALUES (?,?,?,?,?)""",
                    (rid, rate, eff.isoformat(), user, db.now_iso()))
                db.audit_log("CREATE", "resource", rid,
                             f"Created resource '{name.strip()}' ({role}, rate {rate:g})",
                             user)
                st.session_state["_clr_add_resource"] = True
                st.success(f"Added {name}.")
                st.rerun()

    resources = logic.get_resources()
    if not resources:
        st.info("No resources yet.")
        return

    # Editable table view
    df = pd.DataFrame([{
        "id": r["id"], "name": r["name"],
        "role": logic.role_name(r["role_id"]),
        "manager": logic.manager_name(r["manager_id"]),
        "hours/day": r["hours_per_day"], "days/week": r["days_per_week"],
        "status": r["status"],
    } for r in resources])
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown("#### Edit resource")
    rmap = {f"{r['name']} (#{r['id']})": r for r in resources}
    sel = st.selectbox("Resource", list(rmap.keys()), key="ed_resource_sel")
    res = rmap[sel]
    # When the selected resource changes, drop the field-widget state so each
    # field re-initialises from the newly selected resource (otherwise Streamlit
    # keeps the previous resource's values because key= overrides value=).
    if st.session_state.get("_ed_resource_for") != res["id"]:
        st.session_state["_ed_resource_for"] = res["id"]
        for k in ("ed_name", "ed_role", "ed_mgr", "ed_hpd", "ed_dpw", "ed_status"):
            st.session_state.pop(k, None)
    c1, c2, c3 = st.columns(3)
    with c1:
        new_name = st.text_input("Name", res["name"], key="ed_name")
        role_names = list(role_map.keys())
        cur_role = logic.role_name(res["role_id"])
        role_sel = st.selectbox("Role", role_names,
                                index=role_names.index(cur_role)
                                if cur_role in role_names else 0, key="ed_role")
        mgr_names = ["(none)"] + list(mgr_map.keys())
        cur_mgr = logic.manager_name(res["manager_id"])
        mgr_sel = st.selectbox("Manager", mgr_names,
                               index=mgr_names.index(cur_mgr)
                               if cur_mgr in mgr_names else 0, key="ed_mgr")
    with c2:
        new_hpd = st.slider("Hours / day", 1.0, 24.0, float(res["hours_per_day"]),
                            0.5, key="ed_hpd")
        new_dpw = st.slider("Days / week", 1.0, 7.0, float(res["days_per_week"]),
                            0.5, key="ed_dpw")
    with c3:
        new_status = st.selectbox("Status", ["ACTIVE", "INACTIVE"],
                                  index=0 if res["status"] == "ACTIVE" else 1,
                                  key="ed_status")
    if st.button("💾 Save resource"):
        db.execute(
            """UPDATE resources SET name=?, role_id=?, manager_id=?, hours_per_day=?,
               days_per_week=?, status=? WHERE id=?""",
            (new_name.strip(), role_map.get(role_sel), mgr_map.get(mgr_sel),
             new_hpd, new_dpw, new_status, res["id"]))
        db.audit_log("UPDATE", "resource", res["id"],
                     f"Updated resource '{new_name.strip()}' "
                     f"(role {role_sel}, mgr {mgr_sel}, {new_hpd:g}h/{new_dpw:g}d, {new_status})",
                     user)
        st.success("Saved."); st.rerun()

    # Billing rate history + add
    st.markdown("#### Billing rate history")
    rates = db.query(
        """SELECT * FROM resource_billing_rates WHERE resource_id=?
           ORDER BY effective_from_date DESC, id DESC""", (res["id"],))
    if rates:
        st.dataframe(pd.DataFrame([{
            "rate": r["rate"], "effective_from": r["effective_from_date"],
            "by": r["created_by"], "added": r["created_at"],
        } for r in rates]), use_container_width=True, hide_index=True)
    yr = _dt.date.today().year
    cur_rate = logic.billing_rate_for_month(res["id"], yr, _dt.date.today().month)
    st.caption(f"Current effective rate: **{cur_rate:,.2f}**")
    with st.form("add_rate"):
        nr = st.number_input("New rate", 0.0, 100000.0, float(cur_rate or 100.0), 5.0)
        ne = st.date_input("Effective from", _dt.date.today(), key="rate_eff")
        if st.form_submit_button("Add rate"):
            db.execute(
                """INSERT INTO resource_billing_rates (resource_id, rate,
                   effective_from_date, created_by, created_at) VALUES (?,?,?,?,?)""",
                (res["id"], nr, ne.isoformat(), user, db.now_iso()))
            db.audit_log("CREATE", "resource", res["id"],
                         f"Added billing rate {nr:g} for '{res['name']}' "
                         f"effective {ne.isoformat()}", user)
            st.success("Rate added.")


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #
def _month_year_picker(label, def_m, def_y, key):
    c1, c2 = st.columns(2)
    m = c1.selectbox(f"{label} month", list(range(1, 13)),
                     index=def_m - 1, format_func=lambda x: MONTH_NAMES[x],
                     key=f"{key}_m")
    years = list(range(2020, 2101))
    y = c2.selectbox(f"{label} year", years, index=years.index(def_y),
                     key=f"{key}_y")
    return m, y


def _projects(user):
    st.subheader("Projects")
    managers = logic.get_managers()
    mmap = {m["name"]: m["id"] for m in managers}
    yr = _dt.date.today().year

    clear_after_save("_clr_add_project",
                     ["add_proj_name", "add_proj_code", "add_proj_notes"])
    with st.expander("➕ Add a project", expanded=False):
        with st.form("add_project"):
            name = st.text_input("Project name", key="add_proj_name")
            code = st.text_input("Project code (PCode for timesheets)",
                                 key="add_proj_code",
                                 help="Short code used on actual timesheet submissions.")
            lead = st.selectbox("Project lead", ["(none)"] + list(mmap.keys()))
            sm, sy = _month_year_picker("Start", 1, yr, "newp_start")
            em, ey = _month_year_picker("End", 12, yr, "newp_end")
            is_base = st.checkbox("Is baseline project?")
            priority = st.selectbox("Priority", ["LOW", "MEDIUM", "HIGH", "CRITICAL"], 1)
            color = st.color_picker("Color", "#4C78A8")
            budget = st.number_input("Initial budget", 0.0, 1e9, 0.0, 1000.0)
            notes = st.text_area("Notes", key="add_proj_notes")
            if st.form_submit_button("Create project") and name.strip():
                try:
                    pid = db.execute(
                        """INSERT INTO projects (name, code, client_id, is_baseline,
                           start_month, start_year, end_month, end_year, status,
                           color, priority, project_lead_id, notes, created_at, created_by)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (name.strip(), code.strip() or None, None, 1 if is_base else 0,
                         sm, sy, em, ey, "ESTIMATE", color, priority,
                         mmap.get(lead), notes, db.now_iso(), user))
                    if budget:
                        db.execute(
                            """INSERT INTO project_budgets (project_id, budget_amount,
                               effective_from_date, note, created_by, created_at)
                               VALUES (?,?,?,?,?,?)""",
                            (pid, budget, f"{sy}-{sm:02d}-01", "Initial budget",
                             user, db.now_iso()))
                    db.execute(
                        """INSERT INTO project_status_history (project_id, old_status,
                           new_status, changed_at, changed_by, reason)
                           VALUES (?,?,?,?,?,?)""",
                        (pid, None, "ESTIMATE", db.now_iso(), user, "Created"))
                    db.audit_log("CREATE", "project", pid,
                                 f"Created project '{name.strip()}'"
                                 + (f" [{code.strip()}]" if code.strip() else "")
                                 + (" (baseline)" if is_base else ""), user)
                    st.session_state["_clr_add_project"] = True
                    st.success(f"Created '{name}'. Edit full lifecycle in Project Pipeline.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not create: {e}")

    _projects_table()


def _projects_table():
    projects = logic.get_projects()
    if not projects:
        st.info("No projects yet.")
        return
    df = pd.DataFrame([{
        "id": p["id"], "code": p["code"] or "", "name": p["name"],
        "baseline": "✔" if p["is_baseline"] else "",
        "status": p["status"],
        "start": f"{MONTH_NAMES[p['start_month']][:3]} {p['start_year']}",
        "end": f"{MONTH_NAMES[p['end_month']][:3]} {p['end_year']}",
        "budget": logic.budget_for_month(p["id"], p["start_year"], p["start_month"]),
        "added": (p["created_at"] or "")[:10],
    } for p in projects])
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("Edit full project details (incl. status & budget amendments) "
               "from **Project Pipeline → Project Detail**.")


# --------------------------------------------------------------------------- #
# Holidays
# --------------------------------------------------------------------------- #
def _holidays(user):
    st.subheader("Holidays")
    st.caption("Weekday holidays reduce working-day counts used in all "
               "hours/cost calculations.")
    clear_after_save("_clr_add_holiday", ["add_holiday_name"])
    with st.form("add_holiday"):
        c1, c2 = st.columns(2)
        d = c1.date_input("Date", _dt.date.today())
        nm = c2.text_input("Name", key="add_holiday_name")
        if st.form_submit_button("Add holiday") and nm.strip():
            try:
                hid = db.execute(
                    "INSERT INTO holidays (holiday_date, name, created_at) VALUES (?,?,?)",
                    (d.isoformat(), nm.strip(), db.now_iso()))
                logic.clear_holiday_cache()
                db.audit_log("CREATE", "holiday", hid,
                             f"Added holiday '{nm.strip()}' on {d.isoformat()}", user)
                st.session_state["_clr_add_holiday"] = True
                st.success(f"Added {nm}.")
                st.rerun()
            except Exception as e:
                st.error(f"Could not add (duplicate date?): {e}")

    rows = db.query("SELECT * FROM holidays ORDER BY holiday_date")
    if rows:
        df = pd.DataFrame([{"date": r["holiday_date"], "name": r["name"]}
                           for r in rows])
        st.dataframe(df, use_container_width=True, hide_index=True)
        ids = {f"{r['holiday_date']} — {r['name']}": r["id"] for r in rows}
        sel = st.selectbox("Delete a holiday", list(ids.keys()))
        if st.button("Delete holiday"):
            db.execute("DELETE FROM holidays WHERE id=?", (ids[sel],))
            logic.clear_holiday_cache()
            db.audit_log("DELETE", "holiday", ids[sel], f"Deleted holiday '{sel}'", user)
            st.success("Deleted."); st.rerun()
    else:
        st.info("No holidays defined.")
