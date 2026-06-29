"""
ui_dashboard.py
---------------
Landing dashboard: a month/year-selectable summary that scales to 50+ resources.

Heavy per-resource maths is computed once and cached (keyed to a cheap data
version so it invalidates when allocations / rates / holidays change). The
utilization view is built for large teams: KPI roll-ups, search + role + state
filters, a capacity distribution, and a compact scrollable table — so you can
find the resource you care about instead of scrolling 50 rows.
"""

import datetime as _dt

import pandas as pd
import streamlit as st

import database as db
import logic
from working_days import month_label, months_between, MONTH_NAMES


# --------------------------------------------------------------------------- #
# Cached computation
# --------------------------------------------------------------------------- #
def _data_version():
    """Cheap signature that changes whenever data the dashboard reads changes."""
    a = db.query_one(
        "SELECT COUNT(*) c, COALESCE(MAX(last_modified_at),'') m FROM allocations")
    r = db.query_one(
        "SELECT COUNT(*) c, COALESCE(MAX(created_at),'') m FROM resource_billing_rates")
    h = db.query_one("SELECT COUNT(*) c FROM holidays")
    p = db.query_one("SELECT COUNT(*) c FROM projects")
    s = db.query_one("SELECT COUNT(*) c FROM project_status_history")
    b = db.query_one("SELECT COUNT(*) c FROM project_budgets")
    return f"{a['c']}-{a['m']}|{r['c']}-{r['m']}|{h['c']}|{p['c']}|{s['c']}|{b['c']}"


@st.cache_data(show_spinner="Crunching allocations…")
def _utilization(year, month, _version):
    """Per-resource capacity for the month. One pass, cached."""
    out = []
    for res in logic.get_resources(active_only=True):
        rows = logic.get_month_allocations(res["id"], year, month)
        free = sum(float(x["percentage"]) for x in rows if x["is_baseline"])
        delivery = sum(float(x["percentage"]) for x in rows if not x["is_baseline"])
        n_del = len([x for x in rows if not x["is_baseline"] and x["percentage"] > 0])
        hrs = logic.resource_working_hours(res["id"], year, month)
        rate = logic.billing_rate_for_month(res["id"], year, month)
        del_hours = hrs * delivery / 100.0
        out.append({
            "resource": res["name"], "role": logic.role_name(res["role_id"]),
            "manager": logic.manager_name(res["manager_id"]),
            "delivery_pct": round(delivery, 0), "free_pct": round(free, 0),
            "n_delivery": n_del,
            "delivery_hours": round(del_hours, 1),
            "delivery_cost": round(del_hours * rate, 2),
            "rate": rate,
            "projects": ", ".join(x["project_name"] for x in rows
                                  if not x["is_baseline"] and x["percentage"] > 0) or "—",
        })
    return out


@st.cache_data(show_spinner=False)
def _ytd_projection(year, month, _version):
    """Year-to-date actuals (Jan→selected month) and the full-year projection
    (Jan→Dec using current allocations) across every project."""
    ytd = 0.0
    fy = 0.0
    for p in logic.get_projects():
        for m in range(1, 13):
            cost = logic.project_month_cost(p["id"], year, m)
            fy += cost
            if m <= month:
                ytd += cost
    remaining = fy - ytd
    return {"ytd": ytd, "fy_projection": fy, "remaining_projection": remaining}


@st.cache_data(show_spinner=False)
def _project_health(year, month, _version):
    out = []
    for p in logic.get_projects():
        window = months_between(p["start_year"], p["start_month"],
                                p["end_year"], p["end_month"])
        spent = sum(logic.project_month_cost(p["id"], y, m) for (y, m) in window)
        budget = logic.budget_for_month(p["id"], p["end_year"], p["end_month"])
        this_month = logic.project_month_cost(p["id"], year, month) \
            if (year, month) in window else 0.0
        out.append({
            "project": p["name"], "client": logic.client_name(p["client_id"]),
            "status": p["status"], "baseline": "⭐" if p["is_baseline"] else "",
            "budget": round(budget, 0), "planned_spend": round(spent, 0),
            "pct_of_budget": round(spent / budget * 100, 1) if budget else 0.0,
            "this_month_burn": round(this_month, 0),
        })
    return out


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
def render(user):
    today = _dt.date.today()

    # ---- Header: title + month/year selector + refresh ----
    h1, h2, h3, h4 = st.columns([3, 1.4, 1.4, 1])
    h1.title("🏠 Dashboard")
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
    if h4.button("🔄 Refresh"):
        _utilization.clear()
        _project_health.clear()
        _ytd_projection.clear()
        st.rerun()

    is_current = (year == today.year and month == today.month)
    st.caption(f"Showing **{MONTH_NAMES[month]} {year}**"
               + ("  ·  current month" if is_current else "  ·  historical/forecast view"))

    version = _data_version()

    # ---- No-allocations banner ----
    if not logic.has_allocations_for_year(year):
        st.warning(
            f"📭 **{year}** has no allocations recorded yet. Creating a project "
            "or resource does not allocate anyone — head to the **Monthly Grid** "
            "and use *Put a resource on a baseline* to onboard each resource to "
            "100%, then assign delivery work. (Prior-year data, if any, stays "
            "queryable in Export/Reports.)")

    # ---- Action items (closures + end warnings), only relevant to 'today' ----
    _action_items(user)

    # ---- KPI roll-up ----
    util = _utilization(year, month, version)
    n = len(util)
    fully = sum(1 for u in util if u["free_pct"] <= 0)
    avg_util = round(sum(u["delivery_pct"] for u in util) / n, 1) if n else 0.0
    avg_free = round(sum(u["free_pct"] for u in util) / n, 1) if n else 0.0
    month_burn = sum(u["delivery_cost"] for u in util)
    usable_projects = len(logic.get_projects(usable_only=True))

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Active resources", n)
    k2.metric("Avg delivery util.", f"{avg_util:.0f}%")
    k3.metric("Fully committed", f"{fully}/{n}")
    k4.metric("Avg free capacity", f"{avg_free:.0f}%")
    k5.metric(f"Delivery burn ({MONTH_NAMES[month][:3]})", f"{month_burn:,.0f}")

    # ---- YTD + full-year projection ----
    proj = _ytd_projection(year, month, version)
    y1, y2, y3 = st.columns(3)
    y1.metric(f"YTD burn (Jan–{MONTH_NAMES[month][:3]} {year})",
              f"{proj['ytd']:,.0f}")
    y2.metric(f"Remaining-year projection ({year})",
              f"{proj['remaining_projection']:,.0f}")
    y3.metric(f"Full-year projection ({year})", f"{proj['fy_projection']:,.0f}")
    st.caption("YTD = actual planned cost Jan→selected month. Full-year "
               "projection uses current allocations across all 12 months.")

    st.divider()

    # ---- Resource utilization (built for 50+) ----
    _utilization_section(util)

    st.divider()

    # ---- Project health ----
    _project_health_section(year, month, version)


def _action_items(user):
    past = logic.projects_past_end()
    soon = logic.projects_ending_soon(30)
    if not past and not soon:
        return
    label = "🔔 Action items"
    if past:
        label += f" · {len(past)} need closure"
    if soon:
        label += f" · {len(soon)} ending soon"
    with st.expander(label, expanded=bool(past)):
        if soon:
            st.warning("Ending within 30 days: " +
                       ", ".join(f"{p['name']} ({d}d)" for p, d in soon))
        for p in past:
            st.markdown(f"**{p['name']}** — ended "
                        f"{month_label(p['end_year'], p['end_month'])} "
                        f"(status {p['status']})")
            c1, c2, c3 = st.columns([1, 1, 2])
            if c1.button("✅ Close", key=f"close_{p['id']}"):
                logic.close_project(p["id"], user, "acknowledged closure (past end)")
                st.success("Closed."); st.rerun()
            new_year = c2.number_input("Extend yr", 2020, 2100, p["end_year"],
                                       key=f"exy_{p['id']}", label_visibility="collapsed")
            new_month = c3.selectbox("Extend to", list(range(1, 13)),
                                     index=p["end_month"] - 1,
                                     format_func=lambda x: MONTH_NAMES[x],
                                     key=f"exm_{p['id']}", label_visibility="collapsed")
            if c3.button("📅 Extend instead", key=f"ext_{p['id']}"):
                try:
                    logic.extend_project(p["id"], int(new_month), int(new_year), user)
                    st.success("Extended."); st.rerun()
                except logic.ValidationError as e:
                    st.error(str(e))


def _utilization_section(util):
    st.subheader("👥 Resource utilization")
    if not util:
        st.info("No active resources.")
        return

    roles = sorted({u["role"] for u in util})
    f1, f2, f3 = st.columns([2, 2, 2])
    search = f1.text_input("🔎 Search name / project", key="dash_search").strip().lower()
    role_filter = f2.multiselect("Role", roles, key="dash_roles")
    state = f3.selectbox(
        "Capacity", ["All", "Fully committed (0% free)",
                     "Has free capacity (>0%)", "Under-utilized (<50% delivery)"],
        key="dash_state")

    def keep(u):
        if search and search not in u["resource"].lower() \
                and search not in u["projects"].lower():
            return False
        if role_filter and u["role"] not in role_filter:
            return False
        if state == "Fully committed (0% free)" and u["free_pct"] > 0:
            return False
        if state == "Has free capacity (>0%)" and u["free_pct"] <= 0:
            return False
        if state == "Under-utilized (<50% delivery)" and u["delivery_pct"] >= 50:
            return False
        return True

    filtered = [u for u in util if keep(u)]
    st.caption(f"Showing **{len(filtered)}** of {len(util)} resources.")

    # Capacity distribution (quick visual scan across the whole team).
    buckets = {"0% free": 0, "5–25%": 0, "30–50%": 0, "55–95%": 0, "100% free": 0}
    for u in util:
        fp = u["free_pct"]
        if fp <= 0:
            buckets["0% free"] += 1
        elif fp <= 25:
            buckets["5–25%"] += 1
        elif fp <= 50:
            buckets["30–50%"] += 1
        elif fp < 100:
            buckets["55–95%"] += 1
        else:
            buckets["100% free"] += 1
    with st.expander("📊 Team capacity distribution", expanded=False):
        st.bar_chart(pd.DataFrame(
            {"resources": list(buckets.values())}, index=list(buckets.keys())))

    if not filtered:
        st.info("No resources match the filters.")
        return

    df = pd.DataFrame([{
        "Resource": u["resource"], "Role": u["role"], "Manager": u["manager"],
        "Delivery %": u["delivery_pct"], "Free %": u["free_pct"],
        "Projects": u["n_delivery"], "Hours": u["delivery_hours"],
        "Cost": u["delivery_cost"], "On": u["projects"],
    } for u in filtered])

    st.dataframe(
        df, use_container_width=True, hide_index=True,
        height=min(620, 70 + 35 * len(df)),
        column_config={
            "Delivery %": st.column_config.ProgressColumn(
                "Delivery %", min_value=0, max_value=100, format="%d%%"),
            "Free %": st.column_config.NumberColumn("Free %", format="%d%%"),
            "Cost": st.column_config.NumberColumn("Cost", format="$%.0f"),
            "On": st.column_config.TextColumn("On projects", width="medium"),
        })


def _project_health_section(year, month, version):
    st.subheader("📊 Project health")
    health = _project_health(year, month, version)
    if not health:
        st.info("No projects.")
        return
    show_all = st.toggle("Include closed / cancelled", value=False, key="dash_ph_all")
    rows = health if show_all else [
        h for h in health if h["status"] not in ("CLOSED", "CANCELLED", "DENIED")]
    df = pd.DataFrame([{
        "Project": h["project"], "Status": h["status"],
        "baseline": h["baseline"], "Budget": h["budget"], "Planned": h["planned_spend"],
        "% Budget": h["pct_of_budget"], "This-mo burn": h["this_month_burn"],
    } for h in rows])
    st.dataframe(
        df, use_container_width=True, hide_index=True,
        column_config={
            "% Budget": st.column_config.ProgressColumn(
                "% of budget", min_value=0, max_value=100, format="%.0f%%"),
            "Budget": st.column_config.NumberColumn("Budget", format="$%.0f"),
            "Planned": st.column_config.NumberColumn("Planned", format="$%.0f"),
            "This-mo burn": st.column_config.NumberColumn("This-mo burn", format="$%.0f"),
        })
