# Learn this codebase (beginner guide + Streamlit cheat-sheet)

This document explains the Resource Allocation Manager for someone new to Python
web apps and to **Streamlit**. It has three parts:

1. **Big picture** - how the app is wired together and how a request flows.
2. **File-by-file walkthrough** - what every file does, function by function,
   with the important lines explained.
3. **Streamlit cheat-sheet** - every Streamlit feature used here: what it is,
   how we use it (with the file it appears in), and what else you could use.

Keep this open next to the code. When you see a function name here, open that
file and read along.

---

## Part 1 - Big picture

### What the app is
A single-user-at-a-time (2-3 people) internal tool to plan who works on what,
each month, at what percentage of their time - and to see the cost and budget
impact. Data lives in a local **SQLite** file (`allocations.db`). The UI is
**Streamlit** (pure Python - no HTML/JS to write).

### The layers (separation of concerns)
```
working_days.py   calendar math (no database)         <- pure functions
database.py       open DB, schema, read/write helpers  <- persistence only
logic.py          business rules (the "100% rule" etc.)<- uses database.py
ui_*.py           screens (Streamlit)                  <- uses logic.py + database.py
app.py            entry point: sign-in + navigation    <- ties screens together
```
Rule of thumb: **UI never writes allocation rows directly.** It calls functions
in `logic.py` (like `assign_project`) so the rules always hold.

### How Streamlit runs your script
This is the single most important Streamlit idea:

> Streamlit re-runs your **entire** Python script from top to bottom on every
> interaction (every click, every dropdown change).

There is no "onClick handler" that runs in isolation. When you click a button,
Streamlit runs `app.py` again from line 1. Widgets "remember" their values
between runs via **session state** (explained below). This is why you see
patterns like "compute everything, then draw it" and `st.rerun()`.

### Start-up flow (read `app.py`)
1. `st.set_page_config(...)` - must be the first Streamlit call.
2. `startup()` - runs once per browser session (guarded by a session-state
   flag): creates tables, takes a backup, runs the annual reset check.
3. `main()` -> `pick_user_gate()` - blocks the app until you pick a manager
   name (your identity for the audit trail). If not chosen, it draws the
   sign-in screen and calls `st.stop()`.
4. `sidebar()` - draws the left navigation; returns the chosen page name.
5. `PAGES[choice](current_user())` - calls that page's `render(user)` function.

---

## Part 2 - File-by-file walkthrough

### `working_days.py` - calendar math (pure functions, no DB)
These are plain Python functions you could unit-test without Streamlit or a
database. That is deliberate - keep "hard logic" testable and dependency-free.

- `MONTH_NAMES`, `MONTH_ABBR` - lists indexed 1-12 (`MONTH_NAMES[1] == "January"`).
  Index 0 is an empty string so month numbers map directly.
- `last_day_of_month(year, month)` - uses `calendar.monthrange` to get the last
  day; used by "as-of" budget/rate lookups ("rate effective on or before the
  last day of the month").
- `working_days(month, year, holidays)` - loops over each day of the month,
  skips weekends (`d.weekday() >= 5`) and any date in `holidays`, and counts the
  rest. This is the basis of all hour/cost math.
- `working_hours(month, year, hours_per_day, days_per_week, holidays)` -
  `working_days * hours_per_day * (days_per_week / 5)`. A 5-day, 8h resource in
  a 23-working-day month has `23 * 8 * 1 = 184` hours.
- `month_weeks(year, month, holidays)` - splits a month into calendar weeks that
  never cross the month boundary (used by the weekly-hours export). It walks day
  by day: from the current day to the coming Sunday (or month end), records that
  as one "week", then continues. Returns dicts with `start`, `end`, `working_days`.
- `iter_months / months_between` - yield `(year, month)` pairs across a range,
  handling year rollover (December -> January).
- `month_index(year, month)` = `year*12 + month` - a single sortable integer so
  you can compare/clip month ranges with simple `<=` / `>=`.

### `database.py` - persistence only
Owns the SQLite file and the schema. Knows nothing about "the 100% rule".

- **Paths** - `DB_PATH`, `BACKUP_DIR`, `UPLOAD_DIR` are built from `__file__` so
  they are absolute and consistent no matter where you launch from.
- `get_connection()` - opens SQLite with three important PRAGMAs:
  - `journal_mode=WAL` - "write-ahead logging" lets one writer and many readers
    work at once (good for 2-3 users).
  - `foreign_keys=ON` - SQLite enforces foreign keys only if you turn them on.
  - `busy_timeout=30000` - wait up to 30s for a lock instead of erroring.
  `row_factory = sqlite3.Row` makes rows behave like dicts (`row["name"]`).
- `transaction()` - a **context manager** (`with transaction() as conn:`).
  It runs `BEGIN IMMEDIATE`, yields the connection, then `COMMIT`; on any
  exception it `ROLLBACK`s and re-raises. On success it bumps a module-global
  `_WRITE_SEQ` counter - that counter is how the dashboard knows "something
  changed" (see caching below). This is the backbone of the "all-or-nothing"
  saves in `logic.py`.
- `query / query_one / execute` - thin helpers. `query` returns a list of rows,
  `query_one` returns one row or `None`, `execute` runs a single write inside a
  `transaction()` and returns the new row id.
- `SCHEMA` - one big SQL string with `CREATE TABLE IF NOT EXISTS ...`. Tables use
  `STRICT` (SQLite enforces column types) and `CHECK` constraints (e.g.
  `percentage >= 0 AND percentage <= 100`, `month BETWEEN 1 AND 12`). These are a
  safety net even if app code has a bug.
- `init_db()` - runs the schema, then `_migrate()`.
- `_migrate(conn)` - **additive migrations**: it checks `PRAGMA table_info(...)`
  and `ALTER TABLE ADD COLUMN` for columns added later (`resources.manager_id`,
  `projects.code`, `project_budgets.budget_year`). This is how existing
  databases get new columns without losing data.
- `get_setting / set_setting` - a tiny key/value table for app settings
  (last backup time, the annual-archive flag).
- `audit_log(action, entity_type, entity_id, summary, user, conn=None)` -
  inserts one row into `audit_log`. Pass `conn` to record inside an existing
  transaction (so the audit entry commits together with the change).
- **Backups**: `make_backup()` checkpoints the WAL then copies the DB file to
  `backups/allocations_YYYYMMDD_HHMMSS.db`, and `_prune_backups()` keeps only the
  newest 30. `restore_backup()` takes a safety backup first, deletes the WAL/SHM
  side files, then copies the chosen backup over the live DB.
- `get_write_seq()` - returns the write counter for the dashboard cache key.

### `logic.py` - the business rules (the heart)
Everything that is "policy" rather than "storage". Uses `database.py` for I/O.

Key constants and helpers:
- `MAIN_FLOW`, `ALL_STATUSES`, `USABLE_STATUS` - the project lifecycle. Only
  `READY_TO_USE` projects appear in the grid / assignment dropdowns.
- `allowed_transitions(current)` - returns every other status (we made status
  changes unrestricted).
- `snap5(value)` - rounds to the nearest 5 (allocations must be multiples of 5%).
- `get_holiday_dates()` - **memoized** (cached in a module global) because it is
  called once per resource-per-month inside the hour math; without caching, 50
  resources x 12 months would fire hundreds of identical queries. `clear_holiday_cache()`
  is called after editing holidays.
- `project_label(p)` - formats `"[CODE] Name"` for display.

As-of lookups (rates and budgets change over time):
- `billing_rate_for_month` - "the latest rate effective on or before the last
  day of the month". This is the classic **as-of / point-in-time** query
  (`WHERE effective_from_date <= ? ORDER BY ... DESC LIMIT 1`).
- `budget_for_month` / `budget_as_of` - same idea for the project's *overall*
  budget (only rows where `budget_year IS NULL`).
- `annual_budget(project, year)` - returns the per-year budget if one was set,
  else falls back to the overall budget; returns `(amount, is_annual_flag)`.

Allocation reads:
- `get_month_allocations` - all active rows for a resource in a month, joined to
  the project so you also get `is_baseline` and the project name.
- `resource_month_total` - sum of percentages (must equal 100 for an onboarded
  resource).
- `baseline_pool` - sum of percentages currently sitting on baseline projects -
  this is the capacity you can pull *from* when you assign portfolio work.
- `get_allocation_value` - one cell's percentage.

The save path (the most important code in the app):
- `validate_assignment(...)` - pure checks, **no writes**. Used both for the live
  preview and again inside the save. Checks 5% increments, the project window,
  that a baseline exists, that the baseline pool can cover the change, etc.
  Returns `{ok, errors, warnings, preview}`.
- `_plan_baseline(b_rows, delta, choice)` - figures out the new baseline
  percentages after taking/returning `delta` percent (either from one chosen
  baseline or split proportionally). Returns `None` if it would go negative.
- `assign_project(resource, project, month_pct, baseline_choice, baseline_at_open, user, reason)`:
  1. Opens a single `transaction()`.
  2. **Concurrent-edit check**: re-reads the baseline pool fresh and compares to
     `baseline_at_open` (what the panel saw when opened). If it changed, raise
     `ConcurrentEditError` -> the whole transaction rolls back.
  3. Re-validates against fresh data.
  4. For each month: writes the project's new percentage, adjusts the baseline
     rows, records `allocation_history` rows, and asserts the month still totals
     100% (or rolls back).
  5. Writes one `audit_log` summary line.
- `remove_assignment`, `set_baseline_allocation` - same transactional pattern.
- `change_project_status / close_project / extend_project` - update the project,
  append to `project_status_history`, and write an `audit_log` entry.

Financials & projections:
- `project_month_cost` - for a project-month, sums each resource's
  `working_hours * pct/100 * rate`.
- `project_financials`, `resource_utilization`, `availability` - reporting reads.
- `weekly_project_hours(resource, year, month, cutoff_date)` - splits a month's
  allocation into per-week hours, freezing weeks before the cutoff ("submitted")
  and loading new work onto the open weeks. Used by the weekly-hours export.

Annual reset:
- `archive_year`, `maybe_annual_reset` - on a new year, copy the previous year's
  allocations into `allocations_archive` and clear them; guarded by a setting so
  it runs once.

### `app.py` - entry point and navigation
- `st.set_page_config(...)` - title, icon, `layout="wide"`. Must be first.
- `startup()` - guarded by `st.session_state["_booted"]` so the create/backup/
  reset work runs once per session, not on every rerun.
- `current_user()` - reads the chosen manager from session state.
- `pick_user_gate()` - if no user yet, draws the "Who are you?" screen
  (existing managers + "create new"), validates the name (rejects "manager"),
  stores it in session state, and `st.stop()`s so nothing else renders. Once
  set, it returns immediately (locked for the session).
- `PAGES` - a dict mapping the sidebar label to the page's `render` function.
  This is a simple, explicit "router".
- `sidebar()` - shows the acting-as name (read-only), the navigation radio, and
  a last-backup caption.
- `main()` - gate -> sidebar -> dispatch to the chosen page.

### `ui_common.py` - one shared helper
- `clear_after_save(flag_key, field_keys)` - the trick for "clear the form after
  a successful save". You cannot blank a widget after it is drawn in the same
  run, so: on success you set a flag and rerun; on the next run this function
  (called *before* the widgets) deletes those widget keys so they re-initialise
  empty. Used by every "Add" form.

### `ui_setup.py` - Resources / Projects / Roles / Managers / Holidays
- `render(user)` - builds tabs with `st.tabs([...])`.
- `_simple_table(table, label, user)` - generic CRUD for name-only tables (roles,
  managers): an add form, a table, and a rename/delete expander. `_REF_NULL`
  lists foreign keys to null out before deleting (so deleting a manager that is a
  project lead doesn't fail).
- `_resources(user)` - add form + editable table + per-resource billing-rate
  history. Note the **edit form fix**: when you pick a different resource it
  pops the field keys so the form refreshes (otherwise Streamlit keeps the old
  values because `key=` overrides `value=`).
- `_projects(user)` - add form (name, code, lead, start/end month-year, baseline
  flag, color, budget, notes). The detail editing lives in the Pipeline screen.
- `_holidays(user)` - add/delete holidays; calls `logic.clear_holiday_cache()`.

### `ui_pipeline.py` - Kanban board + Project detail
- `_kanban(user)` - draws status columns with `st.columns`, each project as a
  small HTML card via `st.markdown(..., unsafe_allow_html=True)`; an "Open"
  button stores the project id in session state and reruns.
- `_project_detail(pid, user)` - `st.tabs` for Details / Assumptions /
  Attachments / Status History / Allocations.
- `_details_tab` - editable fields, the per-year/overall **budget amendment**
  form, and the status-change control. Each save writes an `audit_log` entry.
- `_attachments_tab` - `st.file_uploader` to upload, `st.download_button` to
  download, `st.image` to preview images.
- `_allocations_tab` - if the project isn't `READY_TO_USE`, shows a promote
  button; otherwise delegates to `ui_project_view.render_project`.

### `ui_grid.py` - the monthly allocation grid
- Builds a resources x projects table of percentages, colours each row green
  (=100%) or red via a pandas **Styler** (`df.style.apply(highlight, axis=1)`).
- `_onboard_baseline(...)` - the "Put a resource on a baseline" action: loops
  `logic.set_baseline_allocation` over the chosen months, clears the dashboard
  cache, toasts, and reruns.
- The "Edit a cell" block locks the assignment panel to the selected
  resource+project so there is one source of truth.

### `ui_assign.py` - the shared Assignment Panel
`assignment_panel(...)` is reused by the grid, project view and availability.
- Resource and project selectors (locked when the caller fixes them).
- A `st.radio` mode toggle: "Same % across months" vs "Different % per month".
- Slider + number input that **stay in sync** (both write the same session-state
  key via `on_change` callbacks) and snap to 5%.
- Captures `baseline_at_open` when the panel opens (for the concurrent-edit
  check), shows a **live preview** by calling `logic.validate_assignment`, and
  disables the Confirm button until everything is valid.
- On confirm: calls `logic.assign_project`, clears caches, toasts, returns `True`.

### `ui_project_view.py`, `ui_availability.py`
- Project View - per-project resource list with inline edit (reuses the
  assignment panel) and the "% of resource / % of project" breakdown.
- Availability - resources with spare baseline capacity for a month, filterable.

### `ui_financials.py`
Per-project month-by-month table (consumed vs projected, unstaffed flags),
baseline cost view, and a cross-project summary. All use `logic` reads.

### `ui_audit.py`
Two tabs: the **Activity log** (everything, from `audit_log`) with
action/entity/user/date/text filters, and the detailed **Allocation changes**
(old%->new% from `allocation_history`). Both export to Excel.

### `ui_reports.py` - Excel exports
- `_to_excel(sheets)` - writes a dict of `{sheet_name: DataFrame}` to an
  in-memory `.xlsx` using **pandas + openpyxl**, returned as bytes for
  `st.download_button`.
- Period selector (Month / YTD / Full year) builds multi-sheet workbooks.
- Weekly-hours export uses `logic.weekly_project_hours` with a lock-date.

### `ui_settings.py`
Backup info, manual backup, and restore (with a confirmation checkbox). Restore
clears caches and session state, then reruns.

### `ui_dashboard.py` - the dashboard (and the caching pattern)
- `_data_version()` returns a string built from `db.get_write_seq()`. It is
  passed as the last argument to every cached function.
- `@st.cache_data` functions (`_utilization`, `_monthly_burn`, `_project_monthly`,
  `_capacity_split`, `_project_health`) do the heavy per-resource/per-project
  maths once and reuse the result. Because the cache key includes the write
  counter, **any** committed change anywhere produces a new key -> the cache
  recomputes; otherwise it returns instantly.
- `render(user)` lays the page out as bordered "cards" (`st.container(border=True)`),
  each with an `st.subheader`, with `st.write("")` spacers between them.
- Charts use **Altair** (`st.altair_chart`) with an explicit `sort=` on the month
  axis so months render Jan->Dec (Streamlit's built-in `st.bar_chart` would sort
  the month names alphabetically).

### `reset.py`
A command-line helper (not part of the app) to wipe the DB and optionally create
empty tables. Run with `python reset.py --empty`.

---

## Part 3 - Streamlit cheat-sheet (linked to this project)

For each item: **what it is**, **where we use it**, and **alternatives**.

### The execution model
- **Top-to-bottom rerun on every interaction.** What it is: Streamlit reruns the
  whole script each time. Where: everywhere - that is why pages are functions
  that "draw" the UI. Alternative: `@st.fragment` reruns only part of the page
  (useful to avoid recomputing a heavy dashboard when only one widget changes).

### Page config & layout
- `st.set_page_config(page_title, page_icon, layout="wide", initial_sidebar_state)`
  - app.py. Must be the first Streamlit command. Alternative: defaults (centered,
  narrow) if you omit it.
- `st.columns([3,1,1])` - side-by-side layout; the list sets relative widths.
  Where: KPI rows, header controls, grid edit row. Alternative: `st.tabs`,
  `st.container`, or just stacking vertically.
- `st.container(border=True)` - a bordered box used to group each dashboard
  section into a "card". Where: `ui_dashboard.render`. Alternative: `st.expander`
  (collapsible), or `st.divider()` lines between sections.
- `st.tabs([...])` - tabbed sections. Where: Setup, Project detail, Audit,
  Export. Alternative: a `st.radio` or `st.selectbox` that switches content.
- `st.expander("title", expanded=)` - collapsible panel. Where: action items,
  monthly burn trend, rename/delete. Alternative: always-visible container.
- `st.sidebar` - the left panel. Where: navigation in app.py. Alternative: put
  controls in the main area; or the newer `st.navigation` / `st.Page` multipage
  API (a more "official" router than our `PAGES` dict).
- `st.divider()` / `st.write("")` - a horizontal rule / blank spacer for visual
  separation. Where: between dashboard cards and sections.

### Text & status output
- `st.title / st.subheader / st.markdown / st.caption / st.write` - text at
  different sizes; `markdown` supports `**bold**`, and `unsafe_allow_html=True`
  lets you inject HTML (used for the Kanban cards). Where: everywhere.
- `st.metric(label, value, delta=, delta_color=, help=)` - a big number with an
  optional up/down delta and a hover tooltip. Where: all dashboard KPIs (e.g.
  "Budget remaining (FY)" uses a coloured delta). Alternative: plain markdown.
- `st.success / st.error / st.warning / st.info` - coloured message boxes.
  Where: save confirmations, validation errors, banners.
- `st.toast("...", icon=)` - a small, auto-dismissing popup. Where: after saving
  an allocation/onboarding (survives the rerun). Alternative: `st.success` (stays
  until the next rerun).

### Input widgets
All return their current value on each run; pass a unique `key=` to store the
value in session state.
- `st.button(label, disabled=)` - returns `True` on the run where it was clicked.
  Where: everywhere. Note: because of reruns, you handle a click with
  `if st.button(...):` right after it.
- `st.selectbox / st.multiselect` - single / multi dropdown. Where: month/year,
  project & resource pickers, filters. The dropdown is searchable by default.
- `st.text_input / st.text_area / st.number_input` - text and numbers. Where:
  forms throughout. `number_input` has `min_value/max_value/step`.
- `st.slider(min, max, value, step=5)` - the allocation % sliders. Where:
  assignment panel, resource hours/days. We snap to 5 in callbacks.
- `st.checkbox / st.toggle` - boolean. Where: "Is baseline", "Confirm this
  change", "Include closed projects".
- `st.date_input / st.color_picker` - date and colour pickers. Where: effective
  dates, project colour.
- `st.radio(label, options, horizontal=)` - exclusive choice. Where: the
  assignment-panel mode toggle, the sidebar navigation.
- `st.file_uploader` - upload a file (returns a file-like object). Where: project
  attachments. We write `.getbuffer()` to disk.
- `st.download_button(label, data_bytes, file_name, mime)` - download bytes.
  Where: every Excel export, attachment downloads.

### Forms
- `st.form("name")` + `st.form_submit_button("Save")` - groups inputs so the
  script does **not** rerun on each keystroke; it reruns once when the submit
  button is pressed (good for multi-field add forms). Where: all "Add" forms.
  Option `clear_on_submit=True` clears the form on submit (we instead use the
  success-only `clear_after_save` pattern). Alternative: loose widgets +
  `on_change` callbacks.

### State & control flow
- `st.session_state` - a dict that **persists across reruns** (per browser
  session). Where: the chosen user, the current page, panel "open" state,
  "clear this form" flags, cached `baseline_at_open`. This is how Streamlit apps
  remember anything between interactions.
- `st.rerun()` - immediately restart the script. Where: after a save, to redraw
  with fresh data. Use sparingly.
- `st.stop()` - stop executing the rest of the script. Where: the sign-in gate
  (nothing renders until a user is chosen).
- Widget `key=` - gives a widget a stable identity and a slot in session state.
  Where: needed whenever the same widget type appears more than once, or when you
  want to read/clear its value via session state.

### Caching & performance
- `@st.cache_data(show_spinner=)` - caches a function's return value by its
  arguments. Where: all heavy dashboard computations. We add a `_version`
  argument (the DB write counter) so the cache invalidates on any change.
  `.clear()` empties it (used by the Refresh button and after writes).
  Alternative: `@st.cache_resource` - for caching *objects* you want to share and
  not copy (DB connections, ML models). We deliberately do **not** cache the DB
  connection because each query opens/closes its own.

### Data display
- `st.dataframe(df, use_container_width=, hide_index=, height=, column_config=)`
  - an interactive (sortable, scrollable) table. Where: grid, utilization,
  project health, audit, financials. Read-only.
- `st.column_config.*` - controls how columns render:
  - `ProgressColumn` (a bar, e.g. "Portfolio Assignment %", "% of budget"),
  - `NumberColumn(format="$%.0f")` (currency),
  - `TextColumn(width="medium")`.
  Where: dashboard tables.
- pandas **Styler** (`df.style.apply(fn, axis=1).format(...)`) - colour cells/rows
  and format numbers; pass the Styler to `st.dataframe`. Where: the green/red
  rows in the Monthly Grid.
- Alternatives: `st.table` (static, no scroll), `st.data_editor` (an **editable**
  grid - you could let users type percentages directly instead of using a panel).

### Charts
- `st.altair_chart(chart, use_container_width=True)` - render an **Altair** chart.
  Where: dashboard status bar, monthly/cumulative burn, per-project burn. We use
  Altair (not the built-in charts) because it lets us set `x` axis `sort=` for
  correct month order, colours, and tooltips.
- `st.bar_chart / st.line_chart` - quick charts from a DataFrame. Where: an early
  version used these; they are simpler but sort category axes alphabetically.
- Alternatives: `st.plotly_chart`, `st.pyplot` (matplotlib), `st.vega_lite_chart`.

### Things we do NOT use but you could
- `st.navigation` + `st.Page` - the modern multipage router (replaces our manual
  `PAGES` dict and sidebar radio).
- `st.dialog` - a real modal popup (our assignment "panel" is inline instead).
- `st.fragment` - rerun only part of the page (would let the project picker
  refresh without recomputing the whole dashboard).
- `st.secrets` / `st.connection` - managed secrets and DB connections (useful if
  you move from SQLite to Postgres on a server).
- `st.query_params` - read/write the URL query string (deep links, bookmarks).
- `st.chat_input` / `st.chat_message` - chat UIs.

---

## Part 4 - Recurring patterns in this codebase

1. **Compute, then draw.** Each page reads data (via `logic`), then renders
   widgets. No hidden callbacks doing work in the background.
2. **All writes go through a transaction** (`database.transaction()`), and
   allocation writes go through `logic.assign_project` so the 100% rule, audit
   trail, and concurrency checks always run.
3. **Cache + version key.** Heavy reads are `@st.cache_data` keyed on a write
   counter, so the UI is fast but never stale.
4. **Session-state gates and flags.** The sign-in gate, the "open panel", and the
   "clear form after save" all use `st.session_state` plus `st.rerun()`.
5. **As-of lookups.** Rates and budgets are time-versioned; "the value for month
   M" means "the latest row effective on or before the end of M".

If you read `working_days.py`, then `database.py`, then `logic.py`, then `app.py`
in that order, the UI files will make sense quickly - they are mostly Streamlit
widgets wired to the `logic.py` functions described above.
