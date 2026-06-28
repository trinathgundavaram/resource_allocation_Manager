"""
app.py
------
Entry point for the Resource Allocation Management application.

Run with:  streamlit run app.py
"""

import os
import datetime as _dt

import streamlit as st

import database as db
import logic
import seed as seed_module

# Set RA_SEED_SAMPLE_DATA=0 (or false/no) to start with an EMPTY database —
# the tables are still created automatically, just with no demo data. Default
# is to load the sample dataset the first time the DB is empty.
SEED_SAMPLE = os.getenv("RA_SEED_SAMPLE_DATA", "1").lower() not in ("0", "false", "no")

# UI modules
import ui_dashboard
import ui_setup
import ui_pipeline
import ui_grid
import ui_project_view
import ui_availability
import ui_financials
import ui_audit
import ui_reports
import ui_settings

st.set_page_config(
    page_title="Resource Allocation Manager",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# --------------------------------------------------------------------------- #
# One-time startup work (guarded so Streamlit reruns don't repeat it).
# --------------------------------------------------------------------------- #
def startup():
    if st.session_state.get("_booted"):
        return
    db.init_db()                       # creates all tables if missing
    if SEED_SAMPLE:
        seed_module.seed()             # loads sample data only when DB is empty
    db.auto_backup_on_startup()        # timestamped copy + prune to 30
    logic.maybe_annual_reset()         # archive previous year if new year
    st.session_state["_booted"] = True


startup()


# --------------------------------------------------------------------------- #
# Current user (acts as changed_by / assigned_by everywhere).
# --------------------------------------------------------------------------- #
def current_user():
    return st.session_state.get("current_user", "manager")


# --------------------------------------------------------------------------- #
# Sidebar navigation
# --------------------------------------------------------------------------- #
PAGES = {
    "🏠 Dashboard": ui_dashboard.render,
    "⚙️ Setup": ui_setup.render,
    "📋 Project Pipeline": ui_pipeline.render,
    "📅 Monthly Grid": ui_grid.render,
    "🎯 Project View": ui_project_view.render,
    "🔍 Availability": ui_availability.render,
    "💰 Financials": ui_financials.render,
    "📊 Audit Trail": ui_audit.render,
    "📤 Export": ui_reports.render,
    "🛠️ Settings": ui_settings.render,
}


def sidebar():
    st.sidebar.title("📊 Resource Allocator")

    managers = [m["name"] for m in logic.get_managers()] or ["manager"]
    options = ["manager"] + managers
    # de-dup preserving order
    seen, opts = set(), []
    for o in options:
        if o not in seen:
            seen.add(o); opts.append(o)
    st.session_state.setdefault("current_user", opts[0])
    st.session_state["current_user"] = st.sidebar.selectbox(
        "Acting as", opts, index=opts.index(st.session_state["current_user"])
        if st.session_state["current_user"] in opts else 0,
    )

    st.sidebar.divider()
    choice = st.sidebar.radio("Navigate", list(PAGES.keys()),
                              key="nav_choice", label_visibility="collapsed")
    st.sidebar.divider()

    # New-year banner trigger info
    year = _dt.date.today().year
    if not logic.has_allocations_for_year(year):
        st.sidebar.warning(f"No allocations recorded for {year} yet.")

    last_bk = db.get_setting("last_backup_at", "—")
    st.sidebar.caption(f"Last backup: {last_bk or '—'}")
    return choice


def main():
    choice = sidebar()
    page = PAGES[choice]
    page(current_user())


if __name__ == "__main__":
    main()
else:
    # Streamlit imports the module rather than running __main__.
    main()
