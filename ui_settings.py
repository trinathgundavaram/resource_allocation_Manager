"""
ui_settings.py
--------------
Settings screen: backup info, manual backup, restore (with confirmation).
"""

import os

import pandas as pd
import streamlit as st

import database as db


def render(user):
    st.title("Settings")

    st.subheader("Backups")
    last = db.get_setting("last_backup_at", "-")
    st.metric("Last backup", last or "-")
    st.caption("A backup is taken automatically every time the app starts. "
               "The 30 most recent backups are kept.")

    if st.button("Create manual backup now"):
        path = db.make_backup()
        if path:
            st.success(f"Backup created: {os.path.basename(path)}")
            st.rerun()
        else:
            st.error("Nothing to back up yet.")

    backups = db.list_backups()
    st.markdown("#### Available backups")
    if backups:
        df = pd.DataFrame([{
            "file": fn, "size (KB)": round(os.path.getsize(full) / 1024, 1),
            "created": ts,
        } for fn, full, ts in backups])
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No backups yet.")

    st.divider()
    st.subheader("Restore")
    st.warning("Restoring replaces the current database. A safety backup of the "
               "current state is taken automatically before restoring.")
    if backups:
        labels = {f"{fn}  -  {ts}": full for fn, full, ts in backups}
        sel = st.selectbox("Choose a backup to restore", list(labels.keys()))
        confirm = st.checkbox("I understand this will overwrite current data.")
        if st.button("Restore selected backup", disabled=not confirm):
            try:
                db.restore_backup(labels[sel])
                st.cache_data.clear()    # restored DB -> drop any cached views
                st.session_state.clear()
                st.success("Restored. Reloading...")
                st.rerun()
            except Exception as e:
                st.error(f"Restore failed: {e}")
    else:
        st.info("No backups available to restore.")

    st.divider()
    st.subheader("App settings")
    settings = db.query("SELECT key, value, updated_at FROM app_settings ORDER BY key")
    if settings:
        st.dataframe(pd.DataFrame([dict(s) for s in settings]),
                     use_container_width=True, hide_index=True)
