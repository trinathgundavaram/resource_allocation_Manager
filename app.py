"""
app.py
------
Entry point for the Resource Allocation Management application.

Run with:  streamlit run app.py
"""

import datetime as _dt

import streamlit as st

import database as db
import logic

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
    page_icon="",
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
    db.auto_backup_on_startup()        # timestamped copy + prune to 30
    logic.maybe_annual_reset()         # archive previous year if new year
    st.session_state["_booted"] = True


startup()


# --------------------------------------------------------------------------- #
# Current user (acts as changed_by / assigned_by everywhere).
# --------------------------------------------------------------------------- #
def current_user():
    return st.session_state.get("current_user")


def pick_user_gate():
    """Block the app until a manager identity is chosen for the session.

    Once set it is locked for the rest of the session (no switching). The name
    must be a real name - the literal word "manager" is rejected.
    """
    if st.session_state.get("current_user"):
        return  # already chosen and locked
    st.title("Who are you?")
    st.caption("Pick the manager you're acting as for this session. Everything "
               "you create or change is recorded against this name, and it "
               "can't be changed until you restart the session.")

    managers = [m["name"] for m in logic.get_managers()]
    CREATE = "Create a new manager..."
    if managers:
        choice = st.selectbox("Manager", managers + [CREATE])
    else:
        st.info("No managers exist yet - create one to continue.")
        choice = CREATE

    if choice == CREATE:
        new = st.text_input("Manager name")
        if st.button("Continue", disabled=not new.strip()):
            nm = new.strip()
            if nm.lower() == "manager":
                st.error("Please enter your actual name, not the word 'manager'.")
            else:
                existing = next((m for m in managers if m.lower() == nm.lower()), None)
                if not existing:
                    db.execute("INSERT INTO managers (name, created_at) VALUES (?,?)",
                               (nm, db.now_iso()))
                    db.audit_log("CREATE", "manager", None,
                                 f"Created manager '{nm}' (session sign-in)", nm)
                st.session_state["current_user"] = existing or nm
                st.rerun()
    else:
        if st.button(f"Continue as {choice}"):
            st.session_state["current_user"] = choice
            st.rerun()
    st.stop()


# --------------------------------------------------------------------------- #
# Sidebar navigation
# --------------------------------------------------------------------------- #
PAGES = {
    "Dashboard": ui_dashboard.render,
    "Setup": ui_setup.render,
    "Project Pipeline": ui_pipeline.render,
    "Monthly Grid": ui_grid.render,
    "Project View": ui_project_view.render,
    "Availability": ui_availability.render,
    "Financials": ui_financials.render,
    "Audit Trail": ui_audit.render,
    "Export": ui_reports.render,
    "Settings": ui_settings.render,
}


def sidebar():
    st.sidebar.title("Resource Allocator")

    # Acting-as is fixed for the session (chosen at the sign-in gate).
    st.sidebar.markdown(f"**Acting as:** {current_user()}")
    st.sidebar.caption("Locked for this session.")

    st.sidebar.divider()
    choice = st.sidebar.radio("Navigate", list(PAGES.keys()),
                              key="nav_choice", label_visibility="collapsed")
    st.sidebar.divider()

    # New-year banner trigger info
    year = _dt.date.today().year
    if not logic.has_allocations_for_year(year):
        st.sidebar.warning(f"No allocations recorded for {year} yet.")

    last_bk = db.get_setting("last_backup_at", "-")
    st.sidebar.caption(f"Last backup: {last_bk or '-'}")
    return choice


def main():
    pick_user_gate()            # blocks until a manager identity is chosen
    choice = sidebar()
    page = PAGES[choice]
    page(current_user())


# Streamlit executes this script top-to-bottom on every run.
main()
