"""
ui_audit.py
-----------
Audit Trail: every allocation change with old/new values, filterable by
resource, project, manager (changed_by) and date range. Exportable to Excel.
"""

import datetime as _dt
import io

import pandas as pd
import streamlit as st

import database as db
import logic


def render(user):
    st.title("📊 Audit Trail")

    resources = logic.get_resources()
    projects = logic.get_projects()
    rmap = {"(all)": None}
    rmap.update({r["name"]: r["id"] for r in resources})
    pmap = {"(all)": None}
    pmap.update({p["name"]: p["id"] for p in projects})

    users = [r["changed_by"] for r in db.query(
        "SELECT DISTINCT changed_by FROM allocation_history WHERE changed_by IS NOT NULL")]
    umap = {"(all)": None}
    umap.update({u: u for u in users})

    c1, c2, c3 = st.columns(3)
    rsel = c1.selectbox("Resource", list(rmap.keys()))
    psel = c2.selectbox("Project", list(pmap.keys()))
    usel = c3.selectbox("Changed by", list(umap.keys()))
    c4, c5 = st.columns(2)
    dfrom = c4.date_input("From date", _dt.date.today() - _dt.timedelta(days=365))
    dto = c5.date_input("To date", _dt.date.today())

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
        st.info("No audit entries match the filters.")
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

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xls:
        df.to_excel(xls, index=False, sheet_name="AuditTrail")
    st.download_button("📥 Export to Excel", buf.getvalue(),
                       file_name=f"audit_trail_{_dt.date.today()}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
