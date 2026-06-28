# Resource Allocation Manager

A full-scale resource allocation management application built with **Python + Streamlit + SQLite**.

## Run

```bash
pip install streamlit pandas openpyxl
cd resource_allocator
streamlit run app.py
```

On first launch the database is created and seeded with sample data (8 resources,
8 projects across the lifecycle, holidays, billing rates, budgets and a full year
of baseline + delivery allocations). A timestamped backup is taken on every start.

## Files

| File | Purpose |
|------|---------|
| `app.py` | Entry point, sidebar navigation, startup (seed/backup/annual-reset). |
| `database.py` | SQLite schema (WAL, STRICT, CHECK constraints), connections, backups. |
| `working_days.py` | Working-day / working-hour calendar maths. |
| `logic.py` | All business rules: 100% allocation, baseline maths, concurrency, lifecycle, financials, annual reset. |
| `seed.py` | Sample-data generator (idempotent). |
| `ui_setup.py` | Resources, Projects, Clients, Roles, Managers, Holidays. |
| `ui_pipeline.py` | Kanban (static) + Project Detail (tabs: Details/Assumptions/Attachments/Status History/Allocations). |
| `ui_grid.py` | Monthly allocation grid (green=100%, red≠100%). |
| `ui_assign.py` | Shared assignment panel (same-% / per-month modes, live preview, concurrency-safe save). |
| `ui_project_view.py` | Per-project allocation detail, inline edit/remove, add resource. |
| `ui_availability.py` | Spare-capacity finder with assign panel. |
| `ui_financials.py` | Per-project economics, baseline actual-vs-budget, cross-project burn. |
| `ui_dashboard.py` | Summary, project health, utilization, end-warnings, closure prompts, new-year banner. |
| `ui_audit.py` | Filterable audit trail of allocation changes + Excel export. |
| `ui_reports.py` | Excel exports (grid, financials, utilization, cross-project, audit). |
| `ui_settings.py` | Backup info, manual backup, restore (confirmation). |
| `uploads/` | Project attachments. |
| `backups/` | Auto/manual DB backups (last 30 kept). |

## Core rules enforced

- Every resource totals **exactly 100%** per month; baseline absorbs the remainder.
- Non-baseline allocations snap to **5%**; baseline % is computed, never typed.
- No mid-month proration — a project active any part of a month counts as the full month.
- Multiple baselines supported; hard-block if a baseline would go negative.
- **Concurrent-edit protection**: baseline is re-read fresh on save inside a transaction; mismatched state is rejected and rolled back.
- Allocations must stay within the project's start/end window.
- Project lifecycle `ESTIMATE → … → READY_TO_USE → CLOSED` (plus `CANCELLED`/`DENIED`); only `READY_TO_USE` projects appear in grid/views/assignment.
- Annual reset archives the previous year on the first run of a new year.
