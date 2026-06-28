"""
ui_project_view.py
------------------
Project View: per-project allocation detail with inline edit / remove and an
add-resource panel. `render_project` is reused by the Pipeline Allocations tab.
"""

import pandas as pd
import streamlit as st

import logic
import ui_assign
from working_days import month_label, months_between, MONTH_NAMES


def render(user):
    st.title("🎯 Project View")
    usable = logic.get_projects(usable_only=True)
    if not usable:
        st.info("No READY_TO_USE projects. Promote projects in the Pipeline.")
        return
    pmap = {logic.project_label(p): p["id"] for p in usable}
    sel = st.selectbox("Project (READY only)", list(pmap.keys()))
    render_project(pmap[sel], user)


def render_project(pid, user):
    project = logic.get_project(pid)
    if not project:
        st.error("Project not found.")
        return
    window = months_between(project["start_year"], project["start_month"],
                            project["end_year"], project["end_month"])
    st.markdown(f"**Window:** {month_label(project['start_year'], project['start_month'])} "
                f"– {month_label(project['end_year'], project['end_month'])} · "
                f"**Status:** {project['status']}"
                + ("  ·  ⭐ baseline" if project["is_baseline"] else ""))

    view = st.radio("View", ["Timeline", "Single month"], horizontal=True,
                    key=f"pv_view_{pid}")

    # Resources that have any allocation on this project in the window.
    res_rows = logic.db.query(
        """SELECT DISTINCT a.resource_id FROM allocations a
           WHERE a.project_id=? AND a.is_active=1""", (pid,))
    resource_ids = [r["resource_id"] for r in res_rows]

    if view == "Timeline":
        _timeline(project, window, resource_ids)
    else:
        _single_month(project, window, user)

    st.divider()
    st.subheader("👥 Resources on this project")
    st.caption(
        "**% of resource** = how much of that person's own capacity goes to this "
        "project (across the team this can total well over 100% — that's the "
        "project's total effort, i.e. number of FTEs). "
        "**% of project** in the header = each person's share of the project's "
        "total staffing over the whole window, so these **sum to 100%** across "
        "resources. In the per-month table, the monthly **% of project** column "
        "sums to 100% within each month.")
    if not resource_ids:
        st.info("No resources assigned yet.")

    # Total allocation on this project per month (denominator for % of project).
    project_total = {}
    for (y, m) in window:
        row = logic.db.query_one(
            """SELECT COALESCE(SUM(percentage),0) s FROM allocations
               WHERE project_id=? AND year=? AND month=? AND is_active=1""",
            (pid, y, m))
        project_total[(y, m)] = float(row["s"]) if row else 0.0
    project_grand_total = sum(project_total.values())  # total person-% over window

    for rid in resource_ids:
        res = logic.get_resource(rid)
        months_on = [(y, m) for (y, m) in window
                     if logic.get_allocation_value(rid, pid, y, m) > 0]
        if not months_on:
            continue
        res_pcts = [logic.get_allocation_value(rid, pid, y, m) for (y, m) in months_on]
        avg_res = sum(res_pcts) / len(res_pcts)
        # Overall share of the project = this resource's total person-% over the
        # window ÷ the project's total person-% over the window. Summed across
        # all resources this is exactly 100%.
        res_grand = sum(res_pcts)
        overall_share = (res_grand / project_grand_total * 100.0) \
            if project_grand_total > 0 else 0.0
        header = (f"{res['name']} — {len(months_on)} mo · "
                  f"~{avg_res:.0f}% of resource (avg) · "
                  f"{overall_share:.0f}% of project")
        with st.expander(header):
            rows = []
            for (y, m) in months_on:
                pct = logic.get_allocation_value(rid, pid, y, m)
                hrs = logic.resource_working_hours(rid, y, m) * pct / 100.0
                rate = logic.billing_rate_for_month(rid, y, m)
                ptot = project_total[(y, m)]
                share = (pct / ptot * 100.0) if ptot > 0 else 0.0
                rows.append({"month": month_label(y, m),
                             "% of resource": pct,
                             "% of project": round(share, 1),
                             "hours": round(hrs, 1),
                             "billed": round(hrs * rate, 2)})
            st.dataframe(pd.DataFrame(rows), use_container_width=True,
                         hide_index=True)

            st.markdown("**Edit allocation** (same shared panel)")
            saved = ui_assign.assignment_panel(
                user, resource_id=rid, project_id=pid,
                locked_resource=True, locked_project=True,
                key=f"pv_edit_{pid}_{rid}")
            if saved:
                st.rerun()

            st.markdown("**Remove from project**")
            scope = st.selectbox(
                "Scope", ["All months"] + [month_label(y, m) for (y, m) in months_on],
                key=f"pv_rm_scope_{pid}_{rid}")
            tgt = "__split__"
            if st.button("🗑 Remove", key=f"pv_rm_btn_{pid}_{rid}"):
                try:
                    if scope == "All months":
                        logic.remove_assignment(rid, pid, months_on, tgt, user,
                                                "removed via project view")
                    else:
                        ym = [(y, m) for (y, m) in months_on
                              if month_label(y, m) == scope]
                        logic.remove_assignment(rid, pid, ym, tgt, user,
                                                "removed via project view")
                    st.success("Removed."); st.rerun()
                except logic.ValidationError as e:
                    st.error(str(e))

    st.divider()
    st.subheader("➕ Add a resource")
    if project["is_baseline"]:
        st.info("Baseline project allocations are auto-calculated.")
    else:
        saved = ui_assign.assignment_panel(
            user, project_id=pid, locked_project=True, key=f"pv_add_{pid}")
        if saved:
            st.rerun()


def _timeline(project, window, resource_ids):
    pid = project["id"]
    data = []
    for (y, m) in window:
        row = {"Month": month_label(y, m)}
        tot = 0.0
        for rid in resource_ids:
            res = logic.get_resource(rid)
            pct = logic.get_allocation_value(rid, pid, y, m)
            row[res["name"]] = pct
            tot += pct
        row["Total %"] = tot
        row["Cost"] = round(logic.project_month_cost(pid, y, m), 2)
        data.append(row)
    cols = ["Month"] + [logic.get_resource(r)["name"] for r in resource_ids] + \
           ["Total %", "Cost"]
    df = pd.DataFrame(data, columns=cols)
    st.dataframe(df, use_container_width=True, hide_index=True)


def _single_month(project, window, user):
    pid = project["id"]
    labels = [month_label(y, m) for (y, m) in window]
    idx = st.selectbox("Month", range(len(window)), format_func=lambda i: labels[i],
                       key=f"pv_sm_{pid}")
    y, m = window[idx]
    rows = logic.db.query(
        """SELECT a.resource_id, a.percentage FROM allocations a
           WHERE a.project_id=? AND a.year=? AND a.month=? AND a.is_active=1""",
        (pid, y, m))
    if not rows:
        st.info(f"No allocations in {month_label(y, m)}.")
        return
    out = []
    for r in rows:
        res = logic.get_resource(r["resource_id"])
        hrs = logic.resource_working_hours(r["resource_id"], y, m) * r["percentage"] / 100
        rate = logic.billing_rate_for_month(r["resource_id"], y, m)
        out.append({"resource": res["name"], "role": logic.role_name(res["role_id"]),
                    "%": r["percentage"], "hours": round(hrs, 1),
                    "rate": rate, "billed": round(hrs * rate, 2)})
    st.dataframe(pd.DataFrame(out), use_container_width=True, hide_index=True)
    st.metric(f"Total cost {month_label(y, m)}",
              f"{logic.project_month_cost(pid, y, m):,.2f}")
