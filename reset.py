"""
reset.py
--------
Start fresh. You normally never touch SQLite by hand — the app creates every
table automatically on startup (see database.init_db). This helper just wipes
the database file so you can begin clean.

Usage:
    python reset.py            Delete the database (and WAL/backups side files).
                               Next `streamlit run app.py` recreates EMPTY tables.
                               Sample data is loaded only if RA_SEED_SAMPLE_DATA
                               is not 0/false/no.

    python reset.py --empty    Delete, then create the empty schema now (no
                               sample data). Ready for you to enter real data
                               via the Setup screens.

    python reset.py --sample   Delete, then load the sample/demo dataset now.

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
        print("Created empty schema (no data). Start the app and enter data via Setup.")
    elif arg == "--sample":
        import seed
        seed.seed(force=True)
        print("Loaded sample data.")
    else:
        print("Database removed. Next app start will create empty tables "
              "(sample data loads only if RA_SEED_SAMPLE_DATA is not 0).")


if __name__ == "__main__":
    main()
