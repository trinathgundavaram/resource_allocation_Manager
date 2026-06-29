"""
ui_grid.py
----------
Monthly allocation grid: resources (rows) × projects (columns).

Only READY_TO_USE projects appear as columns; baseline projects are shown in
their own block. Rows that total exactly 100% are green; anything else is red.
Pick a resource + project below the grid to open the shared assignment panel.
"""

import datetime as _dt

import pandas as pd
import streamlit as st

import database as db
import logic
import ui_assign
from working_days import MONTH_NAMES


def render(user):
    st.title("📅 Monthly Grid")

    today = _dt.date.today()
    year = today.year
    if not logic.has_allocations_for_year(year):
        st.warning(f"🎉 New year: no allocations recorded for {year} yet. "
                   "Start assigning below.")

    c1, c2 = st.columns(2)
    month = c1.selectbox("Month", list(range(1, 13)), index=today.month - 1,
                         format_func=lambda x: MONTH_NAMES[x])
    sel_year = c2.selectbox("Year", list(range(2020, 2101)),
                            index=list(range(2020, 2101)).index(year))

    resources = logic.get_resources(active_only=False)
    usable = logic.get_projects(usable_only=True)
    baselines = [p for p in usable if p["is_baseline"]]
    deliverys = [p for p in usable if not p["is_baseline"]]

    if not resources:
        st.info("No resources. Add some in Setup.")
        return
    if not usable:
        st.info("No READY_TO_USE projects yet. Promote projects in the Pipeline.")
        return

    ordered = baselines + deliverys
    proj_cols = [p["name"] for p in ordered]

    # Build the grid.
    data = []
    totals = []
    for res in resources:
        row = {"Resource": res["name"]}
        rtotal = 0.0
        for p in ordered:
            val = logic.get_allocation_value(res["id"], p["id"], sel_year, month)
            row[p["name"]] = val
            rtotal += val
        row["Total"] = rtotal
        totals.append(rtotal)
        data.append(row)

    df = pd.DataFrame(data, columns=["Resource"] + proj_cols + ["Total"])

    def highlight(row):
        ok = abs(row["Total"] - 100.0) < 0.01
        color = "background-color: rgba(76,175,80,0.18)" if ok \
            else "background-color: rgba(244,67,54,0.18)"
        return [color] * len(row)

    st.caption("Values are **% of each resource's monthly capacity** allocated "
               "to each project (not hours). 🟩 row = 100% · 🟥 row ≠ 100%. "
               "Baseline columns shown first. Scroll horizontally for many projects.")
    styled = df.style.apply(highlight, axis=1).format(
        {c: "{:.0f}%" for c in proj_cols + ["Total"]})
    st.dataframe(styled, use_container_width=True, hide_index=True, height=min(
        600, 80 + 35 * len(df)))

    green = sum(1 for t in totals if abs(t - 100) < 0.01)
    st.caption(f"{green}/{len(totals)} resources at exactly 100% for "
               f"{MONTH_NAMES[month]} {sel_year}.")

    st.divider()
    _onboard_baseline(user, resources, baselines, sel_year, month, green, len(totals))

    st.divider()
    st.subheader("✏️ Edit a cell")
    st.caption("Select a resource and a project, then assign in the panel.")
    rmap = {f"{r['name']} ({logic.role_name(r['role_id'])})": r["id"]
            for r in resources}
    dmap = {p["name"]: p["id"] for p in deliverys}
    if not dmap:
        st.info("No non-baseline READY projects to assign (baseline % is "
                "auto-calculated).")
        return
    cc1, cc2 = st.columns(2)
    rsel = cc1.selectbox("Resource", list(rmap.keys()), key="grid_res")
    psel = cc2.selectbox("Project", list(dmap.keys()), key="grid_proj")

    # The grid owns the resource/project selection, so the panel is locked to it
    # (no duplicate selectors) — one source of truth.
    with st.container(border=True):
        saved = ui_assign.assignment_panel(
            user, resource_id=rmap[rsel], project_id=dmap[psel],
            locked_resource=True, locked_project=True, key="grid_panel")
    if saved:
        st.rerun()


def _onboard_baseline(user, resources, baselines, sel_year, month, green, total):
    """One-click action to place a resource onto a baseline at 100% across a
    range of months (the onboarding step for brand-new resources)."""
    needs_attention = green < total
    with st.expander("🚀 Put a resource on a baseline (onboarding)",
                     expanded=needs_attention):
        st.caption("A brand-new resource has no allocations. Place them on a "
                   "baseline at 100% across a month range so they total 100% and "
                   "can take delivery work. Months that already have delivery work "
                   "keep it — the baseline just carries the remainder.")
        if not baselines:
            st.info("Create a baseline project first: Setup → Projects (tick "
                    "**Is baseline**), then promote it to READY_TO_USE in the "
                    "Pipeline.")
            return
        rmap = {f"{r['name']} ({logic.role_name(r['role_id'])})": r["id"]
                for r in resources}
        bmap = {logic.project_label(p): p["id"] for p in baselines}
        oc1, oc2 = st.columns(2)
        rsel = oc1.selectbox("Resource", list(rmap.keys()), key="ob_res")
        bsel = oc2.selectbox("Baseline project", list(bmap.keys()), key="ob_base")
        oc3, oc4 = st.columns(2)
        from_m = oc3.selectbox("From month", list(range(1, 13)), index=month - 1,
                               format_func=lambda x: MONTH_NAMES[x], key="ob_from")
        to_m = oc4.selectbox("To month", list(range(1, 13)), index=11,
                             format_func=lambda x: MONTH_NAMES[x], key="ob_to")
        if to_m < from_m:
            st.error("To month must be on/after From month.")
            return
        label = (f"Put on baseline at 100% · "
                 f"{MONTH_NAMES[from_m][:3]}–{MONTH_NAMES[to_m][:3]} {sel_year}")
        if st.button(label, key="ob_go"):
            import time
            try:
                for m in range(from_m, to_m + 1):
                    logic.set_baseline_allocation(rmap[rsel], bmap[bsel], sel_year,
                                                  m, user, "onboarding to baseline")
                db.audit_log("ASSIGN", "allocation", bmap[bsel],
                             f"Onboarded {rsel} to baseline {bsel} "
                             f"({MONTH_NAMES[from_m]}–{MONTH_NAMES[to_m]} {sel_year})",
                             user)
                st.cache_data.clear()   # so the Dashboard reflects it immediately
                st.success(f"✅ {rsel} placed on baseline for "
                           f"{MONTH_NAMES[from_m]}–{MONTH_NAMES[to_m]} {sel_year}.")
                st.toast("✅ Resource placed on baseline", icon="✅")
                time.sleep(1.3)         # keep the confirmation visible
                st.rerun()
            except logic.ValidationError as e:
                st.error(str(e))
