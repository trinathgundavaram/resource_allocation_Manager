"""
ui_reports.py
-------------
Export / Reports: allocation grid, project financials (as-of), resource
utilization, cross-project cost and audit trail - all to Excel via openpyxl.
"""

import datetime as _dt
import io

import pandas as pd
import streamlit as st

import database as db
import logic
from working_days import month_label, months_between, MONTH_NAMES


def _to_excel(sheets):
    """sheets: dict {sheet_name: DataFrame} -> xlsx bytes."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xls:
        for name, df in sheets.items():
            df.to_excel(xls, index=False, sheet_name=name[:31])
    return buf.getvalue()


PERIODS = ["Selected month", "YTD (Jan -> selected month)", "Full year (Jan -> Dec)"]


def _period_months(year, month, period):
    """Return the list of months covered by the chosen period."""
    if period == PERIODS[0]:
        return [month]
    if period == PERIODS[1]:
        return list(range(1, month + 1))
    return list(range(1, 13))


def _grid_frame(year, month, resources, projects):
    """Allocation-grid DataFrame for one month."""
    data = []
    for r in resources:
        row = {"Resource": r["name"], "Role": logic.role_name(r["role_id"])}
        tot = 0.0
        for p in projects:
            v = logic.get_allocation_value(r["id"], p["id"], year, month)
            row[p["name"]] = v
            tot += v
        row["Total"] = tot
        data.append(row)
    return pd.DataFrame(data)


def _util_frame(year, month):
    """Resource-utilization DataFrame for one month."""
    util = logic.resource_utilization(year, month)
    return pd.DataFrame([{
        "resource": u["name"], "role": u["role"], "manager": u["manager"],
        "allocated %": u["allocated_pct"], "available %": u["available_pct"],
        "billed hours": round(u["billed_hours"], 1),
        "rate": u["rate"], "projects": ", ".join(u["projects"]),
    } for u in util])


# --------------------------------------------------------------------------- #
# Weekly hours
# --------------------------------------------------------------------------- #
def _weekly_rows(year, month, cutoff):
    """Long-form weekly-hours rows for every resource in a month (hours > 0)."""
    out = []
    for res in logic.get_resources(active_only=False):
        weeks, rows = logic.weekly_project_hours(res["id"], year, month, cutoff)
        for r in rows:
            if r["hours"] <= 0.005:
                continue
            out.append({
                "Resource": res["name"],
                "Manager": logic.manager_name(res["manager_id"]),
                "PCode": r.get("code", ""),
                "Project": r["project"] + (" *" if r["is_baseline"] else ""),
                "Year": year, "Month": MONTH_NAMES[month],
                "Week": r["week_label"],
                "Status": "LOCKED" if r["locked"] else "open",
                "Hours": round(r["hours"], 1),
            })
    return out


def _weekly_pivot(year, month, cutoff):
    """Wide matrix: rows = (Resource, Project), columns = week labels, with a
    per-resource weekly TOTAL row. Locked weeks get a in the column header."""
    weeks_meta = None
    blocks = []
    for res in logic.get_resources(active_only=False):
        weeks, rows = logic.weekly_project_hours(res["id"], year, month, cutoff)
        if not weeks:
            continue
        weeks_meta = weeks
        week_cols = [w["label"] for w in weeks]
        # aggregate hours by project x week (project label carries its PCode)
        proj_names = {}
        for r in rows:
            code = r.get("code", "")
            pname = ((code + " - ") if code else "") + r["project"] \
                + (" *" if r["is_baseline"] else "")
            proj_names.setdefault(pname, {})
            proj_names[pname][r["week_label"]] = \
                proj_names[pname].get(r["week_label"], 0.0) + r["hours"]
        for pname, wk in sorted(proj_names.items()):
            if sum(wk.values()) <= 0.005:
                continue
            row = {"Resource": res["name"], "Project": pname}
            for c in week_cols:
                row[c] = round(wk.get(c, 0.0), 1)
            blocks.append(row)
        # weekly total row for the resource
        total = {"Resource": res["name"], "Project": "> TOTAL"}
        for c in week_cols:
            total[c] = round(sum(r["hours"] for r in rows if r["week_label"] == c), 1)
        blocks.append(total)
    if not blocks:
        return pd.DataFrame(), []
    cols = ["Resource", "Project"] + ([w["label"] for w in weeks_meta] if weeks_meta else [])
    df = pd.DataFrame(blocks, columns=cols)
    # Mark locked (already-submitted) week columns with a padlock.
    rename = {w["label"]: (w["label"] + "" if w.get("locked") else w["label"])
              for w in (weeks_meta or [])}
    df = df.rename(columns=rename)
    return df, (weeks_meta or [])


_ASSUMPTIONS = pd.DataFrame({"Weekly hours - assumptions": [
    "A full Mon-Fri week = hours_per_day x days_per_week hours (40h by default).",
    "Partial weeks scale by their number of working days (Mon-Fri, minus holidays).",
    "Weeks never cross a month boundary: the first/last partial stretch of a month "
    "is its own week (Week 1 of <Mon>, ... last week of <Mon>); the next month "
    "restarts at Week 1.",
    "A project assigned mid-month only loads the weeks from its assigned date "
    "onward; earlier (already submitted) weeks keep 0 for that project.",
    "Baseline projects balance each week up to full capacity, so submitted weeks "
    "stay fully booked and a newly added project's hours land on the remaining "
    "weeks. Monthly hours per project are preserved.",
]})


def _weekly_hours_tab(today):
    st.markdown("#### Weekly hours to book - by resource & project")
    st.caption("Distributes each resource's monthly allocation into weekly hours "
               "(assuming a 40-hour full week). Weeks ending before the **lock "
               "date** are already submitted () and are never rewritten - new "
               "or rebalanced hours land only on the open weeks on/after it.")
    c1, c2, c3, c4 = st.columns(4)
    month = c1.selectbox("Month", list(range(1, 13)), index=today.month - 1,
                         format_func=lambda x: MONTH_NAMES[x], key="rep_wk_m")
    year = c2.selectbox("Year", list(range(2020, 2101)),
                        index=list(range(2020, 2101)).index(today.year),
                        key="rep_wk_y")
    period = c3.selectbox("Export period", PERIODS, key="rep_wk_period")
    cutoff = c4.date_input("Lock date (weeks ending before are submitted)",
                           today, key="rep_wk_cutoff")

    # Preview: wide matrix for the selected month.
    pivot, weeks_meta = _weekly_pivot(year, month, cutoff)
    if pivot.empty:
        st.info(f"No allocations to break down for {MONTH_NAMES[month]} {year}.")
    else:
        with st.expander("Week definitions for this month", expanded=False):
            st.dataframe(pd.DataFrame([{
                "Week": w["label"],
                "From": w["start"].isoformat(), "To": w["end"].isoformat(),
                "Working days": w["working_days"],
                "Status": "LOCKED" if w["locked"] else "open",
            } for w in weeks_meta]), use_container_width=True, hide_index=True)
        n_locked = sum(1 for w in weeks_meta if w["locked"])
        st.caption(f"Preview - {MONTH_NAMES[month]} {year} (hours per week). "
                   f"= locked/submitted ({n_locked} of {len(weeks_meta)} weeks). "
                   "Rows marked > TOTAL are the resource's weekly total.")
        st.dataframe(pivot, use_container_width=True, hide_index=True,
                     height=min(640, 80 + 28 * len(pivot)))

    # Export covers the chosen period (long-form + per-month wide sheets).
    months = _period_months(year, month, period)
    long_rows = []
    sheets = {}
    for m in months:
        long_rows += _weekly_rows(year, m, cutoff)
        wide, _ = _weekly_pivot(year, m, cutoff)
        if not wide.empty:
            sheets[f"{MONTH_NAMES[m][:3]} {year}"] = wide
    long_df = pd.DataFrame(long_rows)
    book = {"Weekly Hours (long)": long_df if not long_df.empty else
            pd.DataFrame(columns=["Resource", "Manager", "PCode", "Project", "Year",
                                  "Month", "Week", "Status", "Hours"])}
    book.update(sheets)
    book["Assumptions"] = _ASSUMPTIONS
    suffix = {PERIODS[0]: f"{year}_{month:02d}",
              PERIODS[1]: f"{year}_YTD_to_{month:02d}",
              PERIODS[2]: f"{year}_full"}[period]
    if len(months) > 1:
        st.caption(f"Workbook will contain a long-form sheet plus {len(sheets)} "
                   "monthly matrix sheets.")
    st.download_button(
        f"Download weekly hours - {period}", _to_excel(book),
        file_name=f"weekly_hours_{suffix}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def render(user):
    st.title("Export / Reports")
    st.caption("All exports use openpyxl (.xlsx).")
    today = _dt.date.today()

    tabs = st.tabs(["Allocation grid", "Weekly hours", "Project financials",
                    "Resource utilization", "Cross-project cost", "Audit trail"])

    # ---- Allocation grid ----
    with tabs[0]:
        c1, c2, c3 = st.columns(3)
        month = c1.selectbox("Month", list(range(1, 13)), index=today.month - 1,
                             format_func=lambda x: MONTH_NAMES[x], key="rep_g_m")
        year = c2.selectbox("Year", list(range(2020, 2101)),
                            index=list(range(2020, 2101)).index(today.year),
                            key="rep_g_y")
        period = c3.selectbox("Export period", PERIODS, key="rep_g_period")
        resources = logic.get_resources()
        projects = logic.get_projects(usable_only=True)

        df = _grid_frame(year, month, resources, projects)
        st.caption(f"Preview: {MONTH_NAMES[month]} {year} (% of capacity). "
                   "The export covers the selected period below.")
        st.dataframe(df, use_container_width=True, hide_index=True)

        months = _period_months(year, month, period)
        sheets = {f"{MONTH_NAMES[m][:3]} {year}": _grid_frame(year, m, resources, projects)
                  for m in months}
        suffix = {PERIODS[0]: f"{year}_{month:02d}", PERIODS[1]: f"{year}_YTD_to_{month:02d}",
                  PERIODS[2]: f"{year}_full"}[period]
        st.download_button(
            f"Download allocation grid - {period}", _to_excel(sheets),
            file_name=f"allocation_grid_{suffix}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        if len(months) > 1:
            st.caption(f"Workbook will contain {len(months)} monthly sheets.")

    # ---- Weekly hours ----
    with tabs[1]:
        _weekly_hours_tab(today)

    # ---- Project financials ----
    with tabs[2]:
        projects = logic.get_projects()
        pmap = {logic.project_label(p): p["id"] for p in projects}
        if not pmap:
            st.info("No projects yet.")
        else:
            psel = st.selectbox("Project", list(pmap.keys()), key="rep_fin_p")
            as_of = st.date_input("As-of date", today, key="rep_fin_asof")
            rows = logic.project_financials(pmap[psel], as_of.isoformat())
            df = pd.DataFrame([{
                "month": r["label"], "allocated %": r["allocated_pct"],
                "cost": round(r["cost"], 2),
                "budget": round(r["budget"], 2),
                "unstaffed": "YES" if r["unstaffed"] else "",
                "gap": round(r["gap"], 2),
            } for r in rows])
            st.dataframe(df, use_container_width=True, hide_index=True)
            if not df.empty:
                st.download_button(
                    "Download project financials", _to_excel({"Financials": df}),
                    file_name=f"financials_{psel}_{as_of}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ---- Resource utilization ----
    with tabs[3]:
        c1, c2, c3 = st.columns(3)
        month = c1.selectbox("Month", list(range(1, 13)), index=today.month - 1,
                             format_func=lambda x: MONTH_NAMES[x], key="rep_u_m")
        year = c2.selectbox("Year", list(range(2020, 2101)),
                            index=list(range(2020, 2101)).index(today.year),
                            key="rep_u_y")
        period = c3.selectbox("Export period", PERIODS, key="rep_u_period")
        df = _util_frame(year, month)
        st.caption(f"Preview: {MONTH_NAMES[month]} {year}. "
                   "The export covers the selected period below.")
        st.dataframe(df, use_container_width=True, hide_index=True)
        if not df.empty:
            months = _period_months(year, month, period)
            sheets = {f"{MONTH_NAMES[m][:3]} {year}": _util_frame(year, m)
                      for m in months}
            suffix = {PERIODS[0]: f"{year}_{month:02d}",
                      PERIODS[1]: f"{year}_YTD_to_{month:02d}",
                      PERIODS[2]: f"{year}_full"}[period]
            if len(months) > 1:
                st.caption(f"Workbook will contain {len(months)} monthly sheets.")
            st.download_button(
                f"Download utilization - {period}", _to_excel(sheets),
                file_name=f"utilization_{suffix}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ---- Cross-project cost ----
    with tabs[4]:
        year = st.selectbox("Year", list(range(2020, 2101)),
                            index=list(range(2020, 2101)).index(today.year),
                            key="rep_x_y")
        projects = logic.get_projects()
        data = []
        for m in range(1, 13):
            row = {"month": month_label(year, m)}
            total = 0.0
            for p in projects:
                cost = logic.project_month_cost(p["id"], year, m)
                row[p["name"]] = round(cost, 2)
                total += cost
            row["TOTAL"] = round(total, 2)
            data.append(row)
        df = pd.DataFrame(data)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button(
            "Download cross-project cost", _to_excel({"CrossProject": df}),
            file_name=f"cross_project_cost_{year}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ---- Audit trail ----
    with tabs[5]:
        rows = db.query(
            """SELECT h.*, r.name AS resource_name, p.name AS project_name
               FROM allocation_history h
               LEFT JOIN resources r ON r.id=h.resource_id
               LEFT JOIN projects p ON p.id=h.project_id
               ORDER BY h.changed_at DESC, h.id DESC""")
        df = pd.DataFrame([{
            "when": r["changed_at"], "by": r["changed_by"],
            "resource": r["resource_name"], "project": r["project_name"],
            "year": r["year"], "month": r["month"],
            "old %": r["old_percentage"], "new %": r["new_percentage"],
            "type": r["change_type"], "reason": r["reason"],
        } for r in rows])
        st.dataframe(df, use_container_width=True, hide_index=True,
                     height=min(500, 80 + 25 * max(1, len(df))))
        if not df.empty:
            st.download_button(
                "Download audit trail", _to_excel({"AuditTrail": df}),
                file_name=f"audit_trail_{today}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
