# Fir dashboard: Lab and Me pages

Date: 2026-11-25. Status: approved in conversation (Fang), awaiting written review.
Replaces the 16-panel "Boards" layout tagged `v1-thuan-boards`.

## Goal

One dashboard, two pages, no hunting:

- **Lab**: the whole picture of def-kanj241 and def-acilab for the PI and Steve. Storage pressure and compute
  use are equally important; the page exists to support requests for more storage, compute and money.
- **Me**: the signed-in member's own quotas, usage and jobs, with enough detail to act (clean up, fix a job).

Everything runs as today: per user, inside a Fir JupyterHub session (compute node), entered from
https://fir.gaig.ca. Nothing is shared or published.

## Not in scope

- Any login, VM or snapshot work (fir.gaig.ca stays a one-button page).
- Showing other members' /home or /scratch (Lustre refuses other users' quota queries).
- Group, filesystem and me/lab filters (removed). Both groups are always shown.
- The "Whole filesystems", "Lab queue", "Lab jobs", "Maintenance" panels (dropped). Job efficiency and
  pending jobs move to Me.

## Navigation and chrome

Top bar: NDAC logo, title, two tabs **Lab** | **Me**, one "last N days" select. Sidebar keeps only the logo,
the two tabs (for narrow screens), the short "about" text and the build time/host. Page and window are URL
parameters, `?page=lab|me&days=N`, default `lab` and `30` (Lab) / `7` (Me); choices 7, 30, 90. Works under the
JupyterHub prefix because every link is relative.

## Lab page (top to bottom)

1. **KPI row, 6 tiles**: /project space %, /project file-count %, nearline total (TiB, tape included),
   this month's CPU core-hours, this month's GPU-hours, median queue wait (last N days). Each tile names
   the project it refers to (the fuller of the two for quota tiles) and turns amber at 85 %, red at 95 %.
2. **Storage, two columns**. Left: quota bars for def-kanj241 /project, def-acilab /project and both
   /nearline, space and file count side by side. Right: "/project by user (TiB)" and "/nearline by user (TiB)"
   (tape hatched). One colour per member, shared with every per-member chart on the page.
3. **Compute, two columns**. Left: core-hours per month stacked by member, CPU and GPU as two charts, last
   3 calendar months (sreport). Right: queue wait per week, median and 90th percentile (sacct Submit→Start);
   below it two small bars, fairshare of the four accounts and cluster busy/idle.
4. **Jobs, two columns**. Left: jobs finished per day, completed vs problems (existing line chart). Right:
   jobs by member, completed vs problems (existing). Under both, the collapsed "Problem jobs, all members"
   table (existing).

## Me page (top to bottom)

1. **KPI row, 5 tiles**: /home %, /scratch %, my share of /project (GiB and % of the group quota, summed
   over groups), running · pending, problem jobs in the window. The /scratch tile carries one line:
   files untouched for 60 days are purged.
2. **My storage, two columns**. Left: /home and /scratch quota bars, space and files. Right: my /project
   and /nearline usage per group as bars against the group quota, with TiB and file count.
3. **My jobs, two columns**. Left: my jobs finished per day, completed vs problems. Right: my job
   efficiency by job name (existing chart: requested vs peak memory, CPU utilisation).
4. **Two tables, expanded**. Pending: JobID, name, account, CPUs, reason, expected start. Problems: JobID,
   name, state, exit code, elapsed/limit, one-line hint (TIMEOUT → raise `--time`; OUT_OF_MEMORY → raise
   `--mem`; FAILED → check the exit code and the job's output file).

Me shows only the user's own records; no other member's name appears.

## Data

Existing collectors stay (diskusage_report, lfs quota, the robinhood stats files with the login-node copy
for /nearline, sinfo, squeue, sshare, sacct). Two new ones in `collectors.py`:

- `usage_by_month(accounts)`: `sreport cluster AccountUtilizationByUser -t hours -P` per calendar month for
  the last 3 months, once for CPU accounts and once with `--tres=gres/gpu` for GPU accounts. Returns
  `{month: {user: {"cpu": h, "gpu": h}}}`. Measured: 0.2 s.
- `queue_wait(accounts, days)`: `sacct -a -A … -S now-Ndays -X -P -o User,Submit,Start,State` restricted to
  jobs that started; wait = Start − Submit. Returns one list of (start_date, seconds). Measured: 7 s for 30
  days (21 k rows), cached with the job data (60 s).

Refresh as today: storage and sreport every 10 min, jobs every 60 s.

## Code layout

```
firdash/collectors.py   data; + usage_by_month, queue_wait
firdash/charts.py       unchanged (bars, line, columns, radial, legend)
firdash/panels.py       NEW: one function per panel, returns the card HTML; shared by both pages
firdash/lab.py          NEW: gathers data and lays out the Lab page
firdash/me.py           NEW: gathers data and lays out the Me page
firdash/render.py       shell only: top bar, tabs, days select, CSS, static-snapshot variant
firdash/server.py       unchanged apart from passing page/days through
```

`render.py` is 770 lines today; after the split it should be under 250.

## Error handling

Each panel is built inside its own `try`. A collector that fails or times out leaves a card reading
"no data · <one-line reason>"; the rest of the page renders. Today one failing command blanks the page.

## Testing

- Unit tests for the parsers in `collectors.py` against saved real outputs in `tests/fixtures/` (sreport CPU
  and GPU, sacct queue-wait, a robinhood stats JSON, diskusage_report).
- Render test: both pages build from fixture data and contain the expected card titles; a missing collector
  produces the "no data" card and not an exception.
- Manual: on Fir, `bin/firdash --html lab.html` and `--html me.html --page me`, screenshot both at desktop
  and phone widths and check against this spec.

## Rollback

`git checkout v1-thuan-boards` restores the previous layout; no data format changes, the `~/.firdash`
caches stay compatible.
