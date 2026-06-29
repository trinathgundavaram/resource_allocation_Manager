"""
ui_assign.py
------------
The shared Assignment Panel, reused by the Monthly Grid, Project View and
Availability screens.

It supports the two assignment modes required by the spec:

  * Same % across a month range.
  * Different % per month (grid entry).

Sliders / number inputs snap to 5%. A live preview shows hours, billing,
baseline-remaining and budget impact per month. The Confirm button stays
disabled until every month is valid and no baseline would go negative.
Saving re-reads the baseline fresh from the DB (concurrent-edit protection)
inside a single transaction.

This module exposes `assignment_panel(...)` (an inline panel) rather than a
top-level page; the `render` function is a thin help screen.
"""

import streamlit as st

import logic
from working_days import MONTH_NAMES, month_label, months_between, month_index


def render(user):
    st.title("🎯 Assignment Panel")
    st.info("The assignment panel opens from the Monthly Grid, Project View or "
            "Availability screens. Pick a resource + project there to assign work.")
    st.markdown("You can also use the quick panel below.")
    assignment_panel(user, key="standalone")


def _project_months(project):
    """Months inside the project window."""
    return months_between(project["start_year"], project["start_month"],
                          project["end_year"], project["end_month"])


def assignment_panel(user, resource_id=None, project_id=None,
                     locked_resource=False, locked_project=False, key="ap"):
    """Render the assignment panel. Returns True if a save succeeded."""
    resources = logic.get_resources(active_only=True)
    if not resources:
        st.warning("No active resources.")
        return False
    usable_projects = [p for p in logic.get_projects(usable_only=True)
                       if not p["is_baseline"]]
    if not usable_projects:
        st.warning("No READY_TO_USE non-baseline projects to assign.")
        return False

    # ---- Resource select ----
    rmap = {f"{r['name']} ({logic.role_name(r['role_id'])} · "
            f"mgr: {logic.manager_name(r['manager_id'])})": r["id"]
            for r in resources}
    rids = list(rmap.values())
    r_index = rids.index(resource_id) if resource_id in rids else 0
    if locked_resource and resource_id in rids:
        lr = logic.get_resource(resource_id)
        st.markdown(f"**Resource:** {lr['name']} "
                    f"({logic.role_name(lr['role_id'])} · "
                    f"mgr: {logic.manager_name(lr['manager_id'])})")
        sel_rid = resource_id
    else:
        sel_label = st.selectbox("Resource", list(rmap.keys()), index=r_index,
                                 key=f"{key}_res")
        sel_rid = rmap[sel_label]

    # ---- Project select ----
    pmap = {logic.project_label(p): p["id"] for p in usable_projects}
    pids = list(pmap.values())
    p_index = pids.index(project_id) if project_id in pids else 0
    if locked_project and project_id in pids:
        st.markdown(f"**Project:** {logic.project_label(logic.get_project(project_id))}")
        sel_pid = project_id
    else:
        sel_plabel = st.selectbox("Project (READY only)", list(pmap.keys()),
                                  index=p_index, key=f"{key}_proj")
        sel_pid = pmap[sel_plabel]

    project = logic.get_project(sel_pid)
    window = _project_months(project)
    if not window:
        st.error("Project has no valid month window.")
        return False

    # ---- Capture baseline_at_open (concurrent-edit protection) ----
    pair_key = f"{key}_pair"
    atopen_key = f"{key}_atopen"
    if st.session_state.get(pair_key) != (sel_rid, sel_pid):
        st.session_state[pair_key] = (sel_rid, sel_pid)
        st.session_state[atopen_key] = {
            (y, m): logic.baseline_pool(sel_rid, y, m) for (y, m) in window
        }
        # New cell selected → let every % widget re-default to its current value
        # (same-% inputs AND per-month sliders), so switching resource/project
        # never carries the previous selection's edits into this one.
        for wkey in [k for k in list(st.session_state.keys())
                     if k == f"{key}_pct_s" or k == f"{key}_pct_n"
                     or k.startswith(f"{key}_m_")]:
            st.session_state.pop(wkey, None)
    at_open = st.session_state.get(atopen_key, {})

    # ---- Mode toggle ----
    mode = st.radio("Mode", ["Same % across months", "Different % per month"],
                    key=f"{key}_mode", horizontal=True)

    month_pct = {}
    if mode == "Same % across months":
        labels = [month_label(y, m) for (y, m) in window]
        c1, c2 = st.columns(2)
        fi = c1.selectbox("From month", range(len(window)),
                          format_func=lambda i: labels[i], key=f"{key}_from")
        ui = c2.selectbox("Until month", range(len(window)),
                          index=len(window) - 1,
                          format_func=lambda i: labels[i], key=f"{key}_until")
        if ui < fi:
            st.error("Until month must be on/after From month.")
            return False
        rng = window[fi:ui + 1]
        # Max = tightest available baseline (pool + existing on this project).
        max_pct = 100
        for (y, m) in rng:
            avail = logic.available_for_new(sel_rid, y, m, sel_pid)
            max_pct = min(max_pct, logic.snap5(avail))
        max_pct = max(0, max_pct)
        # Slider and number input share one source of truth and stay in sync;
        # both snap to 5% increments. Upper bound is the tightest available
        # baseline (kept >= 5 so the widgets remain valid even at 0 capacity).
        sk, nk = f"{key}_pct_s", f"{key}_pct_n"
        wmax = max(5, max_pct)
        existing0 = logic.snap5(
            logic.get_allocation_value(sel_rid, sel_pid, rng[0][0], rng[0][1]))
        if sk not in st.session_state:
            st.session_state[sk] = min(existing0, wmax)
            st.session_state[nk] = st.session_state[sk]
        # Clamp to current bounds (the range/max can change between reruns).
        st.session_state[sk] = max(0, min(int(st.session_state[sk]), wmax))
        st.session_state[nk] = max(0, min(int(st.session_state[nk]), wmax))

        def _sync_from_slider():
            st.session_state[nk] = st.session_state[sk]

        def _sync_from_number():
            v = max(0, min(logic.snap5(st.session_state[nk]), wmax))
            st.session_state[nk] = v
            st.session_state[sk] = v

        c3, c4 = st.columns(2)
        c3.slider("Percentage", 0, wmax, step=5, key=sk,
                  on_change=_sync_from_slider)
        c4.number_input("Percentage (number)", 0, wmax, step=5, key=nk,
                        on_change=_sync_from_number)
        pct = int(st.session_state[sk])
        st.caption(f"Tightest available baseline across range: **{max_pct}%** "
                   "· slider & number stay in sync, 5% steps only.")
        for (y, m) in rng:
            month_pct[(y, m)] = pct
    else:
        st.caption("Set a percentage for each month (max = that month's available "
                   "baseline). Step 5%.")
        for (y, m) in window:
            avail = logic.snap5(logic.available_for_new(sel_rid, y, m, sel_pid))
            existing = logic.get_allocation_value(sel_rid, sel_pid, y, m)
            c1, c2, c3 = st.columns([2, 3, 2])
            c1.markdown(f"**{month_label(y, m)}**  \n_max {max(0, avail)}%_")
            val = c2.slider(f"{month_label(y, m)} %", 0, 100,
                            int(logic.snap5(existing)), step=5,
                            key=f"{key}_m_{y}_{m}", label_visibility="collapsed")
            running = logic.resource_month_total(sel_rid, y, m) - existing + val
            c3.markdown(f"total → **{running:.0f}%**")
            month_pct[(y, m)] = logic.snap5(val)

    # ---- Baseline choice (which baseline to reduce) ----
    month_baselines = logic.baseline_rows(sel_rid, window[0][0], window[0][1])
    baseline_choice = {}
    if len(month_baselines) > 1:
        opts = {"Split proportionally": "__split__"}
        for b in month_baselines:
            opts[b["project_name"]] = b["project_id"]
        chosen = st.selectbox("Reduce from which baseline?", list(opts.keys()),
                              key=f"{key}_bchoice")
        for (y, m) in month_pct:
            baseline_choice[(y, m)] = opts[chosen]
    else:
        for (y, m) in month_pct:
            baseline_choice[(y, m)] = "__split__"

    # ---- Validate + live preview ----
    v = logic.validate_assignment(sel_rid, sel_pid, month_pct, baseline_choice, project)

    st.markdown("##### Live preview")
    preview_rows = []
    tot_hours = tot_billing = 0.0
    for (y, m) in sorted(month_pct.keys()):
        cell = v["preview"].get((y, m), {})
        hrs = cell.get("hours", 0.0)
        bil = cell.get("billing", 0.0)
        rem = cell.get("baseline_remaining", None)
        tot_hours += hrs
        tot_billing += bil
        preview_rows.append({
            "month": month_label(y, m), "%": month_pct[(y, m)],
            "hours": round(hrs, 1), "billing": round(bil, 2),
            "baseline left": "—" if rem is None else round(rem, 1),
        })
    if preview_rows:
        import pandas as pd
        st.dataframe(pd.DataFrame(preview_rows), use_container_width=True,
                     hide_index=True)
    cph1, cph2 = st.columns(2)
    cph1.metric("Total hours", f"{tot_hours:,.1f}")
    cph2.metric("Total billing", f"{tot_billing:,.2f}")

    for w in v["warnings"]:
        st.warning("⚠ " + w)
    for e in v["errors"]:
        st.error("⛔ " + e)

    reason = st.text_input("Reason / note", "assignment", key=f"{key}_reason")
    # Confirm stays disabled until every month passes validation.
    disabled = not v["ok"] or not month_pct
    if disabled and month_pct:
        st.caption("⛔ Confirm is disabled until all validation issues above are "
                   "resolved.")
    if st.button("✅ Confirm assignment", disabled=disabled, key=f"{key}_confirm"):
        import time
        try:
            relevant_atopen = {k: at_open[k] for k in month_pct if k in at_open}
            is_edit = any(
                logic.get_allocation_value(sel_rid, sel_pid, y, m) > 0
                for (y, m) in month_pct)
            logic.assign_project(sel_rid, sel_pid, month_pct, baseline_choice,
                                 relevant_atopen, user, reason)
            # Reset captured baseline + synced widgets so next open re-reads fresh.
            for kk in (atopen_key, pair_key, f"{key}_pct_s", f"{key}_pct_n"):
                st.session_state.pop(kk, None)
            verb = "updated" if is_edit else "saved"
            st.success(f"✅ Allocation {verb} successfully.")
            st.toast(f"✅ Allocation {verb}", icon="✅")
            time.sleep(1.5)  # keep the confirmation visible briefly
            return True
        except logic.ConcurrentEditError as e:
            st.session_state.pop(atopen_key, None)
            st.session_state.pop(pair_key, None)
            st.error(f"🔒 {e}")
        except logic.ValidationError as e:
            st.error(f"⛔ {e}")
    return False
