"""
ui_dashboard.py
---------------
Landing dashboard.

Allocation model note: a resource is always at 100% (baseline absorbs the
remainder). "Portfolio assignment" here means work on non-baseline (portfolio)
projects. Time sitting on a baseline is NOT counted as free capacity - a
resource fully on a baseline simply has *no portfolio assignment*. Resources
are bucketed by TOTAL allocation as:
  * Fully allocated   - totals 100% (portfolio + baseline)
  * Partially allocated - totals 1-99%
  * No allocation     - not onboarded for the month (no rows)

Heavy per-resource/project maths is cached and keyed to a write-counter data
version, so any change anywhere refreshes it instantly.
"""

import datetime as _dt

import altair as alt
import pandas as pd
import streamlit as st

import database as db
import logic
from working_days import month_label, months_between, MONTH_NAMES, MONTH_ABBR


def _data_version():
    a = db.query_one(
        "SELECT COUNT(*) c, COALESCE(MAX(last_modified_at),'') m FROM allocations")
    return f"seq{db.get_write_seq()}|{a['c']}-{a['m']}"


def _alloc_status(total_pct):
    """Status is based on TOTAL allocation (baseline + delivery). A resource on
    a baseline at 100% is fully allocated; 'No allocation' means not onboarded
    at all (no allocation rows for the month)."""
    if total_pct >= 100:
        return "Fully allocated"
    if total_pct > 0:
        return "Partially allocated"
    return "No allocation"


@st.cache_data(show_spinner="Crunching allocations...")
def _utilization(year, month, _version):
    """Per-resource portfolio-assignment load for the month (delivery_* keys
    are kept internally for the non-baseline portion)."""
    out = []
    for res in logic.get_resources(active_only=True):
        rows = logic.get_month_allocations(res["id"], year, month)
        baseline = sum(float(x["percentage"]) for x in rows if x["is_baseline"])
        delivery = sum(float(x["percentage"]) for x in rows if not x["is_baseline"])
        n_del = len([x for x in rows if not x["is_baseline"] and x["percentage"] > 0])
        hrs = logic.resource_working_hours(res["id"], year, month)
        rate = logic.billing_rate_for_month(res["id"], year, month)
        del_hours = hrs * delivery / 100.0
        out.append({
            "resource": res["name"], "role": logic.role_name(res["role_id"]),
            "manager": logic.manager_name(res["manager_id"]),
            "delivery_pct": round(delivery, 0), "baseline_pct": round(baseline, 0),
            "total_pct": round(baseline + delivery, 0),
            "status": _alloc_status(baseline + delivery),
            "n_delivery": n_del,
            "delivery_hours": round(del_hours, 1),
            "delivery_cost": round(del_hours * rate, 2),
            "rate": rate,
            "projects": ", ".join(x["project_name"] for x in rows
                                  if not x["is_baseline"] and x["percentage"] > 0) or "-",
        })
    return out


@st.cache_data(show_spinner=False)
def _monthly_burn(year, _version):
    """Total planned burn per month (Jan->Dec) across all projects."""
    projects = logic.get_projects()
    return [round(sum(logic.project_month_cost(p["id"], year, m) for p in projects), 2)
            for m in range(1, 13)]


@st.cache_data(show_spinner=False)
def _project_monthly(year, project_id, _version):
    """Per-month allocated capacity-hours and planned burn (cost) for one
    project across all resources (Jan->Dec)."""
    out = []
    for m in range(1, 13):
        rows = db.query(
            """SELECT resource_id, percentage FROM allocations
               WHERE project_id=? AND year=? AND month=? AND is_active=1""",
            (project_id, year, m))
        hours = cost = 0.0
        for r in rows:
            wh = logic.resource_working_hours(r["resource_id"], year, m)
            h = wh * float(r["percentage"]) / 100.0
            hours += h
            cost += h * logic.billing_rate_for_month(r["resource_id"], year, m)
        out.append({"hours": round(hours, 2), "cost": round(cost, 2)})
    return out


@st.cache_data(show_spinner=False)
def _capacity_split(year, _version):
    """Per-month split of the team's allocated capacity-hours into baseline vs
    delivery (Jan->Dec). Used for the 'how much of allocation is baseline'
    indicator at month / YTD / full-year scope."""
    out = []
    resources = logic.get_resources(active_only=True)
    for m in range(1, 13):
        baseline_h = delivery_h = 0.0
        for res in resources:
            hrs = logic.resource_working_hours(res["id"], year, m)
            for r in logic.get_month_allocations(res["id"], year, m):
                share = hrs * float(r["percentage"]) / 100.0
                if r["is_baseline"]:
                    baseline_h += share
                else:
                    delivery_h += share
        out.append({"baseline": baseline_h, "delivery": delivery_h,
                    "total": baseline_h + delivery_h})
    return out


def _baseline_share(split):
    """(month_idx-independent) helper: given a list slice of capacity splits,
    return baseline / total as a percentage (0 if no allocation)."""
    b = sum(x["baseline"] for x in split)
    t = sum(x["total"] for x in split)
    return (b / t * 100.0) if t > 0 else 0.0


def _total_budget(year):
    """Sum of each project's budget as of year-end (latest amendment)."""
    return sum(logic.budget_for_month(p["id"], year, 12) for p in logic.get_projects())


@st.cache_data(show_spinner=False)
def _project_health(year, month, _version):
    """Project health scoped to the selected YEAR: planned spend, remaining and
    % of budget are all for Jan-Dec of ``year`` (clipped to the project's
    window). Budget is the amendment effective as of that year-end."""
    out = []
    for p in logic.get_projects():
        window = months_between(p["start_year"], p["start_month"],
                                p["end_year"], p["end_month"])
        wset = set(window)
        fy_spend = sum(logic.project_month_cost(p["id"], year, m)
                       for m in range(1, 13) if (year, m) in wset)
        budget, is_annual = logic.annual_budget(p["id"], year)
        this_month = logic.project_month_cost(p["id"], year, month) \
            if (year, month) in wset else 0.0
        out.append({
            "project": logic.project_label(p),
            "status": p["status"], "baseline": "*" if p["is_baseline"] else "",
            "budget": round(budget, 0), "budget_basis": "annual" if is_annual else "overall",
            "fy_spend": round(fy_spend, 0),
            "fy_remaining": round(budget - fy_spend, 0),
            "pct_of_budget": round(fy_spend / budget * 100, 1) if budget else 0.0,
            "this_month_burn": round(this_month, 0),
        })
    return out


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
def render(user):
    today = _dt.date.today()

    h1, h2, h3, h4 = st.columns([3, 1.4, 1.4, 1])
    h1.title("Dashboard")
    st.session_state.setdefault("dash_month", today.month)
    st.session_state.setdefault("dash_year", today.year)
    month = h2.selectbox("Month", list(range(1, 13)),
                         index=st.session_state["dash_month"] - 1,
                         format_func=lambda x: MONTH_NAMES[x], key="dash_month")
    years = list(range(2020, 2101))
    year = h3.selectbox("Year", years,
                        index=years.index(st.session_state["dash_year"])
                        if st.session_state["dash_year"] in years else years.index(today.year),
                        key="dash_year")
    h4.write("")
    if h4.button("Refresh"):
        _utilization.clear()
        _project_health.clear()
        _monthly_burn.clear()
        _capacity_split.clear()
        _project_monthly.clear()
        st.rerun()

    is_current = (year == today.year and month == today.month)
    st.caption(f"Showing **{MONTH_NAMES[month]} {year}**"
               + ("  -  current month" if is_current else "  -  historical/forecast view"))

    version = _data_version()

    if not logic.has_allocations_for_year(year):
        st.warning(
            f"**{year}** has no allocations recorded yet. Creating a project "
            "or resource does not allocate anyone - head to the **Monthly Grid** "
            "and use *Put a resource on a baseline* to onboard each resource, then "
            "assign portfolio work.")

    _action_items(user)
    st.divider()

    util = _utilization(year, month, version)
    split = _capacity_split(year, version)

    # ===== Section: Team summary =====
    with st.container(border=True):
        st.subheader("Team summary")
        n = len(util)
        fully = sum(1 for u in util if u["status"] == "Fully allocated")
        partial = sum(1 for u in util if u["status"] == "Partially allocated")
        none_alloc = sum(1 for u in util if u["status"] == "No allocation")
        avg_load = round(sum(u["delivery_pct"] for u in util) / n, 1) if n else 0.0
        month_burn = sum(u["delivery_cost"] for u in util)
        usable_projects = len(logic.get_projects(usable_only=True))
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Active resources", n)
        k2.metric("Avg portfolio assignment", f"{avg_load:.0f}%",
                  help="Average % of capacity on portfolio (non-baseline) work. "
                       "The rest of each resource's 100% sits on a baseline.")
        k3.metric("READY projects", usable_projects)
        k4.metric(f"Portfolio burn ({MONTH_ABBR[month]})", f"{month_burn:,.0f}")

    st.write("")

    # ===== Section: Allocation status =====
    with st.container(border=True):
        st.subheader("Allocation status")
        st.caption("Based on **total** allocation. A resource at 100% - whether "
                   "on a portfolio project or a baseline - is *Fully allocated*. "
                   "*No allocation* means not onboarded for this month yet.")
        s1, s2, s3 = st.columns(3)
        s1.metric("Fully allocated", fully, help="Totals 100% (portfolio + baseline).")
        s2.metric("Partially allocated", partial, help="Totals between 1-99%.")
        s3.metric("No allocation", none_alloc,
                  help="No allocation rows yet - not onboarded for this month.")
        status_order = ["Fully allocated", "Partially allocated", "No allocation"]
        status_df = pd.DataFrame({"Status": status_order,
                                  "Resources": [fully, partial, none_alloc]})
        status_chart = (
            alt.Chart(status_df).mark_bar().encode(
                x=alt.X("Resources:Q", title="# resources",
                        axis=alt.Axis(tickMinStep=1)),
                y=alt.Y("Status:N", sort=status_order, title=None),
                color=alt.Color("Status:N", sort=status_order, legend=None,
                                scale=alt.Scale(range=["#2E7D32", "#F9A825", "#9E9E9E"])),
                tooltip=["Status", "Resources"])
            .properties(height=140))
        st.altair_chart(status_chart, use_container_width=True)

    st.write("")

    # ===== Section: Baseline share of allocation =====
    with st.container(border=True):
        bs_month = _baseline_share(split[month - 1:month])
        bs_ytd = _baseline_share(split[:month])
        bs_fy = _baseline_share(split)
        baselines = logic.get_baseline_projects(usable_only=False)
        if len(baselines) == 1:
            bl_note = f"Baseline: **{logic.project_label(baselines[0])}**."
        elif len(baselines) > 1:
            bl_note = ("**Combined across " + str(len(baselines)) + " baselines**: "
                       + ", ".join(logic.project_label(b) for b in baselines)
                       + ". (Use the project picker below for a single baseline.)")
        else:
            bl_note = "No baseline projects defined."
        st.subheader("Baseline share of allocation")
        st.caption("Of all allocated capacity (hours), how much sits on baseline "
                   "(non-portfolio) work - combined across every baseline project. "
                   + bl_note)
        b1, b2, b3 = st.columns(3)
        b1.metric(f"This month ({MONTH_ABBR[month]})", f"{bs_month:.0f}%")
        b2.metric(f"YTD (Jan-{MONTH_ABBR[month]})", f"{bs_ytd:.0f}%")
        b3.metric(f"Full year ({year})", f"{bs_fy:.0f}%")

    st.write("")

    # ===== Section: Project allocation & burn (picker) =====
    with st.container(border=True):
        _project_allocation_section(year, month, version, split)

    st.write("")

    # ===== Section: Financials =====
    with st.container(border=True):
        monthly = _monthly_burn(year, version)
        ytd = sum(monthly[:month])
        fy = sum(monthly)
        rest = fy - ytd
        total_budget = _total_budget(year)
        remaining_budget = total_budget - fy
        st.subheader("Financials (planned burn)")
        f1, f2, f3, f4, f5 = st.columns(5)
        f1.metric(f"YTD burn (Jan-{MONTH_ABBR[month]})", f"{ytd:,.0f}")
        f2.metric("Rest-of-year projection", f"{rest:,.0f}")
        f3.metric("Full-year projection", f"{fy:,.0f}")
        f4.metric("Total budget", f"{total_budget:,.0f}")
        f5.metric("Budget remaining (FY)", f"{remaining_budget:,.0f}",
                  delta=f"{remaining_budget:,.0f}", delta_color="normal",
                  help="Total budget minus full-year projected burn. Negative = "
                       "projected to go over budget.")
        if remaining_budget < 0:
            st.error(f"Projected **over budget** by {abs(remaining_budget):,.0f} "
                     f"for {year} (full-year burn {fy:,.0f} vs budget {total_budget:,.0f}).")
        st.caption("Projection = planned burn from current allocations. "
                   "YTD = Jan->selected month; rest-of-year = remaining months; "
                   "full-year = all 12 months.")
        with st.expander("Monthly burn trend (Jan-Dec)", expanded=False):
            order = [MONTH_ABBR[m] for m in range(1, 13)]
            burn_df = pd.DataFrame({
                "Month": order,
                "Planned burn": [round(x, 2) for x in monthly],
                "Cumulative": [round(x, 2) for x in pd.Series(monthly).cumsum()],
            })
            bar = (alt.Chart(burn_df).mark_bar(color="#4C78A8").encode(
                       x=alt.X("Month:N", sort=order, title="Month"),
                       y=alt.Y("Planned burn:Q", title="Planned burn"),
                       tooltip=["Month", "Planned burn"])
                   .properties(height=240))
            st.altair_chart(bar, use_container_width=True)
            line = (alt.Chart(burn_df).mark_line(point=True, color="#E45756").encode(
                        x=alt.X("Month:N", sort=order, title="Month"),
                        y=alt.Y("Cumulative:Q", title="Cumulative burn"),
                        tooltip=["Month", "Cumulative"])
                    .properties(height=200))
            st.altair_chart(line, use_container_width=True)

    st.write("")

    # ===== Section: Project health =====
    with st.container(border=True):
        _project_health_section(year, month, version)

    st.write("")

    # ===== Section: Resource utilization =====
    with st.container(border=True):
        _utilization_section(util)


def _project_allocation_section(year, month, version, split):
    """Project picker showing the same allocation-share metrics for any chosen
    project, plus that project's monthly + cumulative burn charts."""
    st.subheader("Project allocation & burn")
    projects = logic.get_projects()
    if not projects:
        st.info("No projects yet.")
        return
    pmap = {logic.project_label(p) + (" *" if p["is_baseline"] else ""): p["id"]
            for p in projects}
    label = st.selectbox("Project", list(pmap.keys()), key="dash_proj_pick")
    pid = pmap[label]

    pm = _project_monthly(year, pid, version)
    p_hours = [x["hours"] for x in pm]
    tot = [s["total"] for s in split]

    def share(num, den):
        d = sum(den)
        return (sum(num) / d * 100.0) if d > 0 else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric(f"Alloc share - {MONTH_ABBR[month]}",
              f"{share(p_hours[month-1:month], tot[month-1:month]):.0f}%")
    c2.metric(f"Alloc share - YTD (Jan-{MONTH_ABBR[month]})",
              f"{share(p_hours[:month], tot[:month]):.0f}%")
    c3.metric(f"Alloc share - FY {year}", f"{share(p_hours, tot):.0f}%")
    st.caption("Allocation share = this project's allocated capacity-hours / the "
               "team's total allocated hours, for the period.")

    order = [MONTH_ABBR[m] for m in range(1, 13)]
    pdf = pd.DataFrame({
        "Month": order,
        "Planned burn": [x["cost"] for x in pm],
        "Cumulative": [round(c, 2) for c in pd.Series([x["cost"] for x in pm]).cumsum()],
    })
    bar = (alt.Chart(pdf).mark_bar(color="#4C78A8").encode(
               x=alt.X("Month:N", sort=order, title="Month"),
               y=alt.Y("Planned burn:Q", title="Planned burn"),
               tooltip=["Month", "Planned burn"]).properties(height=220))
    st.altair_chart(bar, use_container_width=True)
    line = (alt.Chart(pdf).mark_line(point=True, color="#E45756").encode(
                x=alt.X("Month:N", sort=order, title="Month"),
                y=alt.Y("Cumulative:Q", title="Cumulative burn"),
                tooltip=["Month", "Cumulative"]).properties(height=180))
    st.altair_chart(line, use_container_width=True)


def _action_items(user):
    past = logic.projects_past_end()
    soon = logic.projects_ending_soon(30)
    if not past and not soon:
        return
    label = "Action items"
    if past:
        label += f" - {len(past)} need closure"
    if soon:
        label += f" - {len(soon)} ending soon"
    with st.expander(label, expanded=bool(past)):
        if soon:
            st.warning("Ending within 30 days: " +
                       ", ".join(f"{p['name']} ({d}d)" for p, d in soon))
        for p in past:
            st.markdown(f"**{p['name']}** - ended "
                        f"{month_label(p['end_year'], p['end_month'])} "
                        f"(status {p['status']})")
            c1, c2, c3 = st.columns([1, 1, 2])
            if c1.button("Close", key=f"close_{p['id']}"):
                logic.close_project(p["id"], user, "acknowledged closure (past end)")
                st.success("Closed."); st.rerun()
            new_year = c2.number_input("Extend yr", 2020, 2100, p["end_year"],
                                       key=f"exy_{p['id']}", label_visibility="collapsed")
            new_month = c3.selectbox("Extend to", list(range(1, 13)),
                                     index=p["end_month"] - 1,
                                     format_func=lambda x: MONTH_NAMES[x],
                                     key=f"exm_{p['id']}", label_visibility="collapsed")
            if c3.button("Extend instead", key=f"ext_{p['id']}"):
                try:
                    logic.extend_project(p["id"], int(new_month), int(new_year), user)
                    st.success("Extended."); st.rerun()
                except logic.ValidationError as e:
                    st.error(str(e))


def _project_health_section(year, month, version):
    st.subheader("Project health")
    st.caption(f"Planned spend, remaining and **% of budget** are for the full "
               f"year **Jan-Dec {year}** (clipped to each project's window). "
               "*Basis* = **annual** if a per-year budget is set for this year, "
               "else **overall** (the project's total budget). Set a per-year "
               "budget in Project Pipeline -> Detail -> Budget.")
    health = _project_health(year, month, version)
    if not health:
        st.info("No projects yet.")
        return
    show_all = st.toggle("Include closed / cancelled", value=False, key="dash_ph_all")
    rows = health if show_all else [
        h for h in health if h["status"] not in ("CLOSED", "CANCELLED", "DENIED")]
    if not rows:
        st.info("No active projects.")
        return
    df = pd.DataFrame([{
        "Project": h["project"], "Status": h["status"], "baseline": h["baseline"],
        "Budget": h["budget"], "Basis": h["budget_basis"], "FY planned": h["fy_spend"],
        "FY remaining": h["fy_remaining"], "% Budget (FY)": h["pct_of_budget"],
        "This-mo burn": h["this_month_burn"],
    } for h in rows])
    st.dataframe(
        df, use_container_width=True, hide_index=True,
        column_config={
            "% Budget (FY)": st.column_config.ProgressColumn(
                "% of budget (FY)", min_value=0, max_value=100, format="%.0f%%"),
            "Budget": st.column_config.NumberColumn("Budget", format="$%.0f"),
            "FY planned": st.column_config.NumberColumn("FY planned", format="$%.0f"),
            "FY remaining": st.column_config.NumberColumn("FY remaining", format="$%.0f"),
            "This-mo burn": st.column_config.NumberColumn("This-mo burn", format="$%.0f"),
        })
    over = [h for h in rows if h["budget"] and h["fy_spend"] > h["budget"]]
    if over:
        st.warning(f"Over budget for {year}: " +
                   ", ".join(f"{h['project']} (+{h['fy_spend']-h['budget']:,.0f})"
                             for h in over))


def _utilization_section(util):
    st.subheader("Resource utilization")
    if not util:
        st.info("No active resources.")
        return

    roles = sorted({u["role"] for u in util})
    f1, f2, f3 = st.columns([2, 2, 2])
    search = f1.text_input("Search name / project", key="dash_search").strip().lower()
    role_filter = f2.multiselect("Role", roles, key="dash_roles")
    state = f3.selectbox(
        "Allocation status",
        ["All", "Fully allocated", "Partially allocated", "No allocation",
         "Under-utilized (<50% portfolio)"], key="dash_state")

    def keep(u):
        if search and search not in u["resource"].lower() \
                and search not in u["projects"].lower():
            return False
        if role_filter and u["role"] not in role_filter:
            return False
        if state in ("Fully allocated", "Partially allocated", "No allocation") \
                and u["status"] != state:
            return False
        if state == "Under-utilized (<50% portfolio)" and u["delivery_pct"] >= 50:
            return False
        return True

    filtered = [u for u in util if keep(u)]
    st.caption(f"Showing **{len(filtered)}** of {len(util)} resources. "
               "Baseline time is shown separately - it is not 'free' capacity.")

    if not filtered:
        st.info("No resources match the filters.")
        return

    df = pd.DataFrame([{
        "Resource": u["resource"], "Role": u["role"], "Manager": u["manager"],
        "Status": u["status"],
        "Portfolio Assignment %": u["delivery_pct"], "Baseline %": u["baseline_pct"],
        "Projects": u["n_delivery"], "Hours": u["delivery_hours"],
        "Cost": u["delivery_cost"], "On": u["projects"],
    } for u in filtered])

    st.dataframe(
        df, use_container_width=True, hide_index=True,
        height=min(620, 70 + 35 * len(df)),
        column_config={
            "Portfolio Assignment %": st.column_config.ProgressColumn(
                "Portfolio Assignment %", min_value=0, max_value=100, format="%d%%"),
            "Baseline %": st.column_config.NumberColumn("Baseline %", format="%d%%"),
            "Cost": st.column_config.NumberColumn("Cost", format="$%.0f"),
            "On": st.column_config.TextColumn("On projects", width="medium"),
        })
