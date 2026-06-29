"""
ui_audit.py
-----------
Audit Trail.

Two views:
  * Activity log - every change in the app (create/update/delete of resources,
    projects, roles, managers, holidays, rates, budgets, status changes,
    assignments, onboarding, ...), filterable by user/action/entity/date/text.
  * Allocation changes - the detailed per-cell old%->new% history.

Both export to Excel.
"""

import datetime as _dt
import io

import pandas as pd
import streamlit as st

import database as db
import logic


def _to_excel(df, sheet):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xls:
        df.to_excel(xls, index=False, sheet_name=sheet)
    return buf.getvalue()


def render(user):
    st.title("Audit Trail")
    tabs = st.tabs(["Activity log (everything)", "Allocation changes (detail)"])
    with tabs[0]:
        _activity_log()
    with tabs[1]:
        _allocation_history()


def _activity_log():
    st.caption("Every create / update / delete and save across the app.")
    actions = [r["action"] for r in db.query(
        "SELECT DISTINCT action FROM audit_log ORDER BY action")]
    entities = [r["entity_type"] for r in db.query(
        "SELECT DISTINCT entity_type FROM audit_log WHERE entity_type IS NOT NULL "
        "ORDER BY entity_type")]
    users = [r["changed_by"] for r in db.query(
        "SELECT DISTINCT changed_by FROM audit_log WHERE changed_by IS NOT NULL "
        "ORDER BY changed_by")]

    c1, c2, c3 = st.columns(3)
    asel = c1.selectbox("Action", ["(all)"] + actions, key="al_action")
    esel = c2.selectbox("Entity", ["(all)"] + entities, key="al_entity")
    usel = c3.selectbox("Changed by", ["(all)"] + users, key="al_user")
    c4, c5, c6 = st.columns(3)
    dfrom = c4.date_input("From date", _dt.date.today() - _dt.timedelta(days=365),
                          key="al_from")
    dto = c5.date_input("To date", _dt.date.today(), key="al_to")
    search = c6.text_input("Search text", key="al_search").strip()

    sql = "SELECT * FROM audit_log WHERE date(changed_at) BETWEEN ? AND ?"
    params = [dfrom.isoformat(), dto.isoformat()]
    if asel != "(all)":
        sql += " AND action = ?"; params.append(asel)
    if esel != "(all)":
        sql += " AND entity_type = ?"; params.append(esel)
    if usel != "(all)":
        sql += " AND changed_by = ?"; params.append(usel)
    if search:
        sql += " AND summary LIKE ?"; params.append(f"%{search}%")
    sql += " ORDER BY changed_at DESC, id DESC"

    rows = db.query(sql, tuple(params))
    if not rows:
        st.info("No activity matches the filters.")
        return
    df = pd.DataFrame([{
        "when": r["changed_at"], "by": r["changed_by"], "action": r["action"],
        "entity": r["entity_type"], "summary": r["summary"],
    } for r in rows])
    st.caption(f"{len(df)} change(s).")
    st.dataframe(df, use_container_width=True, hide_index=True,
                 height=min(620, 80 + 30 * len(df)))
    st.download_button("Export activity log", _to_excel(df, "ActivityLog"),
                       file_name=f"activity_log_{_dt.date.today()}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _allocation_history():
    st.caption("Per-cell allocation changes with old -> new percentages.")
    resources = logic.get_resources()
    projects = logic.get_projects()
    rmap = {"(all)": None}
    rmap.update({r["name"]: r["id"] for r in resources})
    pmap = {"(all)": None}
    pmap.update({logic.project_label(p): p["id"] for p in projects})
    users = [r["changed_by"] for r in db.query(
        "SELECT DISTINCT changed_by FROM allocation_history WHERE changed_by IS NOT NULL")]
    umap = {"(all)": None}
    umap.update({u: u for u in users})

    c1, c2, c3 = st.columns(3)
    rsel = c1.selectbox("Resource", list(rmap.keys()), key="ah_res")
    psel = c2.selectbox("Project", list(pmap.keys()), key="ah_proj")
    usel = c3.selectbox("Changed by", list(umap.keys()), key="ah_user")
    c4, c5 = st.columns(2)
    dfrom = c4.date_input("From date", _dt.date.today() - _dt.timedelta(days=365),
                          key="ah_from")
    dto = c5.date_input("To date", _dt.date.today(), key="ah_to")

    sql = """SELECT h.*, r.name AS resource_name, p.name AS project_name
             FROM allocation_history h
             LEFT JOIN resources r ON r.id = h.resource_id
             LEFT JOIN projects p ON p.id = h.project_id
             WHERE date(h.changed_at) BETWEEN ? AND ?"""
    params = [dfrom.isoformat(), dto.isoformat()]
    if rmap[rsel] is not None:
        sql += " AND h.resource_id = ?"; params.append(rmap[rsel])
    if pmap[psel] is not None:
        sql += " AND h.project_id = ?"; params.append(pmap[psel])
    if umap[usel] is not None:
        sql += " AND h.changed_by = ?"; params.append(umap[usel])
    sql += " ORDER BY h.changed_at DESC, h.id DESC"

    rows = db.query(sql, tuple(params))
    if not rows:
        st.info("No allocation changes match the filters.")
        return
    df = pd.DataFrame([{
        "when": r["changed_at"], "by": r["changed_by"],
        "resource": r["resource_name"] or r["resource_id"],
        "project": r["project_name"] or r["project_id"],
        "year": r["year"], "month": r["month"],
        "old %": r["old_percentage"], "new %": r["new_percentage"],
        "type": r["change_type"], "reason": r["reason"],
    } for r in rows])
    st.caption(f"{len(df)} change(s).")
    st.dataframe(df, use_container_width=True, hide_index=True,
                 height=min(600, 80 + 30 * len(df)))
    st.download_button("Export allocation changes", _to_excel(df, "AllocationChanges"),
                       file_name=f"allocation_changes_{_dt.date.today()}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
