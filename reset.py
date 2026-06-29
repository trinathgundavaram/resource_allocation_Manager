"""
reset.py
--------
Start fresh. You normally never touch SQLite by hand — the app creates every
table automatically on startup (see database.init_db). This helper just wipes
the database file so you can begin clean.

Usage:
    python reset.py            Delete the database (and WAL side files).
                               Next `streamlit run app.py` recreates EMPTY tables.

    python reset.py --empty    Delete, then create the empty schema now. Ready
                               for you to enter real data via the Setup screens.

The previous database, if any, is backed up to backups/ before deletion.
"""

import os
import sys

import database as db


def _wipe():
    # Safety backup of whatever is there now.
    try:
        db.make_backup()
    except Exception:
        pass
    for ext in ("", "-wal", "-shm"):
        path = db.DB_PATH + ext
        if os.path.exists(path):
            os.remove(path)
            print(f"removed {os.path.basename(path)}")


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    _wipe()
    if arg == "--empty":
        db.init_db()
        print("Created empty schema. Start the app and enter data via Setup.")
    else:
        print("Database removed. Next app start will create empty tables.")


if __name__ == "__main__":
    main()
