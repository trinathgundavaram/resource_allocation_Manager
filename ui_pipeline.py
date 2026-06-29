"""
ui_pipeline.py
--------------
Project Pipeline: a static Kanban board and a full Project Detail editor.

The Kanban is display-only (no drag, no move buttons) — clicking a card opens
the Project Detail page where every field is editable and status changes /
budget amendments are logged.
"""

import os
import datetime as _dt

import pandas as pd
import streamlit as st

import database as db
import logic
from ui_common import clear_after_save
from working_days import MONTH_NAMES


def render(user):
    st.title("📋 Project Pipeline")
    pid = st.session_state.get("detail_project_id")
    if pid:
        _project_detail(pid, user)
    else:
        _kanban(user)


# --------------------------------------------------------------------------- #
# Kanban (static)
# --------------------------------------------------------------------------- #
def _kanban(user):
    st.caption("Static board — click **Open** on a card to edit a project.")
    projects = logic.get_projects()
    if not projects:
        st.info("No projects yet. Add some in Setup → Projects.")
        return

    # Always-visible columns so empty lanes (e.g. GATE_0, CANCELLED, DENIED)
    # are still shown. NOT_ALLOCATED only appears when it actually has cards.
    always_show = logic.MAIN_FLOW + ["CANCELLED", "DENIED"]
    columns_order = logic.MAIN_FLOW + ["NOT_ALLOCATED", "CANCELLED", "DENIED"]
    by_status = {s: [] for s in columns_order}
    for p in projects:
        by_status.setdefault(p["status"], []).append(p)

    show = list(always_show)
    if by_status.get("NOT_ALLOCATED"):
        # insert NOT_ALLOCATED right after ALLOCATED for a sensible order
        show.insert(show.index("READY_TO_USE"), "NOT_ALLOCATED")

    cols = st.columns(len(show))
    for col, status in zip(cols, show):
        with col:
            color = logic.STATUS_COLORS.get(status, "#888")
            st.markdown(
                f"<div style='border-top:4px solid {color};padding-top:4px;"
                f"font-weight:600'>{status} ({len(by_status.get(status, []))})</div>",
                unsafe_allow_html=True)
            if not by_status.get(status):
                st.caption("—")
            for p in by_status.get(status, []):
                budget = logic.budget_for_month(p["id"], p["start_year"], p["start_month"])
                base = " ⭐" if p["is_baseline"] else ""
                added = (p["created_at"] or "")[:10]
                code = (p["code"] or "").strip()
                code_html = (f"<span style='font-size:0.72em;color:#fff;"
                             f"background:#607D8B;border-radius:3px;padding:1px 5px'>"
                             f"{code}</span> " if code else "")
                st.markdown(
                    f"<div style='border:1px solid #ddd;border-left:5px solid "
                    f"{p['color'] or color};border-radius:6px;padding:8px;"
                    f"margin:6px 0;background:rgba(127,127,127,0.06)'>"
                    f"{code_html}<b>{p['name']}{base}</b><br>"
                    f"<span style='font-size:0.85em'>💰 {budget:,.0f}</span><br>"
                    f"<span style='font-size:0.78em;color:#888'>added {added}</span>"
                    f"</div>", unsafe_allow_html=True)
                if st.button("Open", key=f"open_{p['id']}"):
                    st.session_state["detail_project_id"] = p["id"]
                    st.rerun()


# --------------------------------------------------------------------------- #
# Project Detail
# --------------------------------------------------------------------------- #
def _project_detail(pid, user):
    project = logic.get_project(pid)
    if not project:
        st.error("Project not found.")
        st.session_state.pop("detail_project_id", None)
        return

    c1, c2 = st.columns([4, 1])
    c1.subheader(f"{logic.project_label(project)}  ·  {project['status']}")
    if c2.button("← Back to board"):
        st.session_state.pop("detail_project_id", None)
        st.rerun()

    tabs = st.tabs(["Details", "Assumptions", "Attachments",
                    "Status History", "Allocations"])
    with tabs[0]:
        _details_tab(project, user)
    with tabs[1]:
        _assumptions_tab(project, user)
    with tabs[2]:
        _attachments_tab(project, user)
    with tabs[3]:
        _status_history_tab(project)
    with tabs[4]:
        _allocations_tab(project, user)


def _details_tab(project, user):
    pid = project["id"]
    managers = logic.get_managers()
    mmap = {m["name"]: m["id"] for m in managers}

    # Read-only metadata (cannot be edited).
    m1, m2 = st.columns(2)
    m1.markdown(f"**Added (created):** {(project['created_at'] or '—')}")
    m2.markdown(f"**Created by:** {project['created_by'] or '—'}")

    st.markdown("##### Editable fields")
    c1, c2 = st.columns(2)
    with c1:
        name = st.text_input("Name", project["name"])
        code = st.text_input("Project code (PCode for timesheets)",
                             project["code"] or "")
        lead_names = list(mmap.keys())
        cur_lead = logic.manager_name(project["project_lead_id"])
        lead = st.selectbox("Project lead", lead_names,
                            index=lead_names.index(cur_lead)
                            if cur_lead in lead_names else 0)
        prio_opts = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
        priority = st.selectbox(
            "Priority", prio_opts,
            index=prio_opts.index(project["priority"])
            if project["priority"] in prio_opts else 1)
    with c2:
        sm = st.selectbox("Start month", list(range(1, 13)),
                          index=project["start_month"] - 1,
                          format_func=lambda x: MONTH_NAMES[x])
        years = list(range(2020, 2101))
        sy = st.selectbox("Start year", years,
                          index=years.index(project["start_year"]))
        em = st.selectbox("End month", list(range(1, 13)),
                          index=project["end_month"] - 1,
                          format_func=lambda x: MONTH_NAMES[x])
        ey = st.selectbox("End year", years, index=years.index(project["end_year"]))
    is_base = st.checkbox("Is baseline project?", bool(project["is_baseline"]))
    color = st.color_picker("Color", project["color"] or "#4C78A8")
    notes = st.text_area("Notes", project["notes"] or "")

    if st.button("💾 Save details"):
        try:
            db.execute(
                """UPDATE projects SET name=?, code=?, project_lead_id=?,
                   priority=?, start_month=?, start_year=?, end_month=?, end_year=?,
                   is_baseline=?, color=?, notes=? WHERE id=?""",
                (name.strip(), code.strip() or None, mmap.get(lead), priority,
                 sm, sy, em, ey, 1 if is_base else 0, color, notes, pid))
            db.audit_log("UPDATE", "project", pid,
                         f"Updated project details '{name.strip()}'", user)
            st.success("Saved."); st.rerun()
        except Exception as e:
            st.error(f"Save failed (check end ≥ start): {e}")

    st.divider()
    st.markdown("##### Budget")
    cur_budget = logic.budget_for_month(pid, project["start_year"], project["start_month"])
    st.metric("Current budget (as of start)", f"{cur_budget:,.2f}")
    budgets = db.query(
        "SELECT * FROM project_budgets WHERE project_id=? ORDER BY effective_from_date DESC, id DESC",
        (pid,))
    if budgets:
        st.dataframe(pd.DataFrame([{
            "amount": b["budget_amount"], "effective_from": b["effective_from_date"],
            "note": b["note"], "by": b["created_by"],
        } for b in budgets]), use_container_width=True, hide_index=True)
    clear_after_save(f"_clr_budget_{pid}", [f"budget_note_{pid}"])
    with st.form("amend_budget"):
        st.caption("Changing the budget creates a new amendment entry (as-of aware).")
        amt = st.number_input("New budget amount", 0.0, 1e9, float(cur_budget), 1000.0)
        eff = st.date_input("Effective from", _dt.date.today())
        note = st.text_input("Amendment note", key=f"budget_note_{pid}")
        if st.form_submit_button("Add budget amendment"):
            db.execute(
                """INSERT INTO project_budgets (project_id, budget_amount,
                   effective_from_date, note, created_by, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (pid, amt, eff.isoformat(), note, user, db.now_iso()))
            db.audit_log("UPDATE", "project", pid,
                         f"Budget amendment {amt:g} effective {eff.isoformat()} "
                         f"on '{project['name']}'"
                         + (f" — {note}" if note.strip() else ""), user)
            st.session_state[f"_clr_budget_{pid}"] = True
            st.success("Budget amendment added.")
            st.rerun()

    st.divider()
    st.markdown("##### Status change")
    st.caption(f"Current status: **{project['status']}**. A project can be moved "
               "directly to any status.")
    transitions = logic.allowed_transitions(project["status"])
    if not transitions:
        st.info("No other statuses available.")
    else:
        new_status = st.selectbox("New status", transitions)
        reason = st.text_input("Reason (optional)", key="status_reason")
        ack = st.checkbox("Confirm this status change")
        if st.button("Apply status change", disabled=not ack):
            try:
                logic.change_project_status(pid, new_status, user, reason)
                st.success(f"Status → {new_status}"); st.rerun()
            except logic.ValidationError as e:
                st.error(str(e))


def _assumptions_tab(project, user):
    pid = project["id"]
    st.caption("Append-only log. Past entries cannot be edited or deleted.")
    rows = db.query(
        "SELECT * FROM project_assumptions WHERE project_id=? ORDER BY created_at ASC, id ASC",
        (pid,))
    if rows:
        for r in rows:
            st.markdown(
                f"<div style='border-left:3px solid #4C78A8;padding:4px 10px;"
                f"margin:6px 0'><b>{r['created_by'] or '—'}</b> "
                f"<span style='color:#888;font-size:0.85em'>{r['created_at']}</span><br>"
                f"{r['content']}</div>", unsafe_allow_html=True)
    else:
        st.info("No assumptions recorded yet.")
    clear_after_save(f"_clr_assume_{pid}", [f"assume_content_{pid}"])
    with st.form("add_assumption"):
        content = st.text_area("New assumption / note", key=f"assume_content_{pid}")
        if st.form_submit_button("Add entry") and content.strip():
            db.execute(
                """INSERT INTO project_assumptions (project_id, content, created_at, created_by)
                   VALUES (?,?,?,?)""",
                (pid, content.strip(), db.now_iso(), user))
            db.audit_log("CREATE", "project", pid,
                         f"Added assumption to '{project['name']}'", user)
            st.session_state[f"_clr_assume_{pid}"] = True
            st.success("Added.")
            st.rerun()


def _attachments_tab(project, user):
    pid = project["id"]
    up = st.file_uploader("Upload a file (any type)", key=f"up_{pid}")
    if up is not None and st.button("Save attachment"):
        safe = f"{pid}_{int(_dt.datetime.now().timestamp())}_{up.name}"
        dest = os.path.join(db.UPLOAD_DIR, safe)
        with open(dest, "wb") as f:
            f.write(up.getbuffer())
        db.execute(
            """INSERT INTO project_attachments (project_id, filename, filepath,
               uploaded_at, uploaded_by) VALUES (?,?,?,?,?)""",
            (pid, up.name, dest, db.now_iso(), user))
        db.audit_log("CREATE", "project", pid,
                     f"Uploaded attachment '{up.name}' to '{project['name']}'", user)
        st.success(f"Uploaded {up.name}."); st.rerun()

    rows = db.query("SELECT * FROM project_attachments WHERE project_id=? ORDER BY id DESC",
                    (pid,))
    if not rows:
        st.info("No attachments.")
        return
    for r in rows:
        c1, c2, c3 = st.columns([3, 2, 1])
        c1.write(f"📎 {r['filename']}")
        c1.caption(f"{r['uploaded_at']} · {r['uploaded_by']}")
        exists = os.path.exists(r["filepath"])
        if exists:
            ext = os.path.splitext(r["filename"])[1].lower()
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                with c1.expander("Preview"):
                    st.image(r["filepath"])
            with open(r["filepath"], "rb") as f:
                c2.download_button("⬇ Download", f.read(), file_name=r["filename"],
                                   key=f"dl_{r['id']}")
        else:
            c2.warning("file missing")
        if c3.button("🗑", key=f"delatt_{r['id']}"):
            try:
                if exists:
                    os.remove(r["filepath"])
            except OSError:
                pass
            db.execute("DELETE FROM project_attachments WHERE id=?", (r["id"],))
            db.audit_log("DELETE", "project", pid,
                         f"Deleted attachment '{r['filename']}' from '{project['name']}'",
                         user)
            st.rerun()


def _status_history_tab(project):
    rows = db.query(
        "SELECT * FROM project_status_history WHERE project_id=? ORDER BY changed_at ASC, id ASC",
        (project["id"],))
    if not rows:
        st.info("No status history.")
        return
    for r in rows:
        old = r["old_status"] or "—"
        st.markdown(
            f"**{old} → {r['new_status']}**  "
            f"<span style='color:#888;font-size:0.85em'>{r['changed_at']} · "
            f"{r['changed_by'] or '—'}</span>  \n{r['reason'] or ''}",
            unsafe_allow_html=True)


def _allocations_tab(project, user):
    pid = project["id"]
    if project["status"] != logic.USABLE_STATUS:
        st.warning(f"Project is **{project['status']}**. Only "
                   f"**{logic.USABLE_STATUS}** projects can hold allocations.")
        if logic.USABLE_STATUS in logic.allowed_transitions(project["status"]):
            reason = st.text_input("Reason to promote", "Ready for allocation",
                                   key="promote_reason")
            if st.button(f"Promote to {logic.USABLE_STATUS}"):
                try:
                    logic.change_project_status(pid, logic.USABLE_STATUS, user, reason)
                    st.success("Promoted."); st.rerun()
                except logic.ValidationError as e:
                    st.error(str(e))
        else:
            st.info("Advance the project through its lifecycle first "
                    "(see Details → Status change).")
        return

    import ui_project_view
    ui_project_view.render_project(pid, user)
