"""
ui_availability.py
------------------
Availability view: who has spare baseline capacity in a given month, filtered
by a minimum-available threshold. Pick a resource to open the assignment panel
pre-filled.
"""

import datetime as _dt

import pandas as pd
import streamlit as st

import logic
import ui_assign
from working_days import MONTH_NAMES


def render(user):
    st.title("🔍 Availability")
    today = _dt.date.today()
    c1, c2, c3 = st.columns(3)
    month = c1.selectbox("Month", list(range(1, 13)), index=today.month - 1,
                         format_func=lambda x: MONTH_NAMES[x])
    year = c2.selectbox("Year", list(range(2020, 2101)),
                        index=list(range(2020, 2101)).index(today.year))
    min_pct = c3.slider("Minimum available %", 0, 100, 0, step=5)

    rows = logic.availability(year, month, float(min_pct))
    if not rows:
        st.info("No resources match the filter.")
        return

    table = []
    for r in rows:
        projs = ", ".join(f"{n} ({p:.0f}%)" for n, p in r["projects"]) or "—"
        table.append({
            "resource": r["name"], "role": r["role"], "manager": r["manager"],
            "rate": r["rate"], "available %": round(r["available_pct"], 1),
            "current projects": projs,
        })
    df = pd.DataFrame(table)
    st.dataframe(
        df.style.format({"rate": "{:.2f}", "available %": "{:.0f}"}),
        use_container_width=True, hide_index=True,
        height=min(600, 80 + 35 * len(df)))

    st.divider()
    st.subheader("Assign a resource")
    st.caption("Pick a resource from the list above to open the assignment panel.")
    rmap = {f"{r['name']} — {r['available_pct']:.0f}% free": r["resource_id"]
            for r in rows}
    sel = st.selectbox("Resource", list(rmap.keys()))
    with st.container(border=True):
        saved = ui_assign.assignment_panel(
            user, resource_id=rmap[sel], locked_resource=True, key="avail_panel")
    if saved:
        st.rerun()
