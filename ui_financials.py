"""
ui_financials.py
----------------
Financials: per-project month-by-month economics, baseline actual-vs-budget,
and a cross-project burn summary.
"""

import datetime as _dt

import pandas as pd
import streamlit as st

import database as db
import logic
from working_days import month_label, last_day_of_month, months_between


def render(user):
    st.title("Financials")
    tabs = st.tabs(["Per project", "Baseline cost", "Cross-project summary"])
    with tabs[0]:
        _per_project()
    with tabs[1]:
        _baseline_cost()
    with tabs[2]:
        _cross_project()


def _per_project():
    projects = logic.get_projects()
    if not projects:
        st.info("No projects.")
        return
    pmap = {f"{logic.project_label(p)} ({p['status']})": p["id"] for p in projects}
    sel = st.selectbox("Project", list(pmap.keys()))
    pid = pmap[sel]
    as_of = st.date_input("As-of date (for budget & consumed/projected split)",
                          _dt.date.today())
    as_of_iso = as_of.isoformat()

    rows = logic.project_financials(pid, as_of_iso)
    if not rows:
        st.info("Project has no month window.")
        return

    budget_total = logic.budget_as_of(pid, as_of_iso)
    cum = 0.0
    table = []
    consumed = projected = 0.0
    for r in rows:
        month_end = last_day_of_month(r["year"], r["month"])
        is_past = month_end <= as_of
        cum += r["cost"]
        if is_past:
            consumed += r["cost"]
        else:
            projected += r["cost"]
        table.append({
            "month": r["label"],
            "phase": "consumed" if is_past else "projected",
            "alloc %": r["allocated_pct"],
            "cost": round(r["cost"], 2),
            "cumulative": round(cum, 2),
            "budget(as-of)": round(logic.budget_for_month(pid, r["year"], r["month"]), 2),
            "remaining": round(budget_total - cum, 2),
            "status": "UNSTAFFED" if r["unstaffed"] else "",
            "gap": round(r["gap"], 2) if r["unstaffed"] else 0.0,
        })
    df = pd.DataFrame(table)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Budget (as-of)", f"{budget_total:,.0f}")
    c2.metric("Consumed", f"{consumed:,.0f}")
    c3.metric("Projected", f"{projected:,.0f}")
    c4.metric("Remaining", f"{budget_total - cum:,.0f}",
              delta=f"{budget_total - cum:,.0f}")

    st.dataframe(df, use_container_width=True, hide_index=True)

    unstaffed = [r for r in rows if r["unstaffed"]]
    if unstaffed:
        st.warning(f"{len(unstaffed)} unstaffed month(s): "
                   + ", ".join(r["label"] for r in unstaffed))

    st.markdown("##### Budget amendment history")
    budgets = db.query(
        "SELECT * FROM project_budgets WHERE project_id=? ORDER BY effective_from_date, id",
        (pid,))
    if budgets:
        st.dataframe(pd.DataFrame([{
            "amount": b["budget_amount"], "effective_from": b["effective_from_date"],
            "note": b["note"], "by": b["created_by"],
        } for b in budgets]), use_container_width=True, hide_index=True)
    else:
        st.caption("No budget set.")


def _baseline_cost():
    st.caption("Baseline projects: actual cost vs budget. Over/under is shown, "
               "never blocked.")
    baselines = [p for p in logic.get_projects() if p["is_baseline"]]
    if not baselines:
        st.info("No baseline projects.")
        return
    pmap = {p["name"]: p["id"] for p in baselines}
    sel = st.selectbox("Baseline project", list(pmap.keys()))
    pid = pmap[sel]
    rows = logic.project_financials(pid)
    table = []
    for r in rows:
        variance = r["budget"] - r["cost"]
        table.append({
            "month": r["label"], "actual": round(r["cost"], 2),
            "budget": round(r["budget"], 2),
            "variance": round(variance, 2),
            "over/under": "UNDER" if variance >= 0 else "OVER",
        })
    df = pd.DataFrame(table)
    if not df.empty:
        st.dataframe(
            df.style.map(
                lambda v: "color: #c62828" if v == "OVER" else "color: #2e7d32",
                subset=["over/under"]),
            use_container_width=True, hide_index=True)
        st.metric("Cumulative actual", f"{df['actual'].sum():,.0f}")
        st.metric("Cumulative budget", f"{df['budget'].sum():,.0f}")


def _cross_project():
    st.caption("Total burn per month across every project, plus per-resource "
               "contribution for a chosen month.")
    today = _dt.date.today()
    year = st.selectbox("Year", list(range(2020, 2101)),
                        index=list(range(2020, 2101)).index(today.year))

    projects = logic.get_projects()
    months = list(range(1, 13))
    data = []
    for m in months:
        row = {"month": month_label(year, m)}
        total = 0.0
        for p in projects:
            cost = logic.project_month_cost(p["id"], year, m)
            if cost:
                row[p["name"]] = round(cost, 0)
            total += cost
        row["TOTAL"] = round(total, 0)
        data.append(row)
    df = pd.DataFrame(data).fillna(0)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.bar_chart(df.set_index("month")["TOTAL"])

    st.markdown("##### Per-resource contribution")
    month = st.selectbox("Month", months, index=today.month - 1,
                         format_func=lambda x: month_label(year, x))
    util = logic.resource_utilization(year, month)
    rows = []
    for u in util:
        cost = 0.0
        for p in projects:
            cost += logic.monthly_cost(u["resource_id"], p["id"], year, month)
        rows.append({"resource": u["name"], "role": u["role"],
                     "allocated %": u["allocated_pct"],
                     "billed hours": round(u["billed_hours"], 1),
                     "cost": round(cost, 2)})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
