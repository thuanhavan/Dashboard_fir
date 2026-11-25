"""Cards and chart bodies shared by the Lab and Me pages. Every function returns an HTML string."""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import collectors as c
from .charts import CRIT, GOOD, OTHER, SERIES, bars, columns, count, esc, legend, line, nice_step, size, status_fill

BAD_STATES = ("FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "CANCELLED")
EMPTY_ST = {"groups": [], "quotas": [], "breakdown": {}, "asof": None}
REASONS = {"Priority": "waiting for higher-priority jobs", "Resources": "next in line, waiting for free nodes",
           "Dependency": "waits for another job", "DependencyNeverSatisfied": "dependency failed: will never start",
           "AssocGrpCPULimit": "account core limit reached", "QOSMaxJobsPerUserLimit": "per-user job limit",
           "ReqNodeNotAvail": "requested nodes unavailable (maintenance?)", "BeginTime": "scheduled for later"}
HINTS = {"TIMEOUT": "ran out of time: raise --time", "OUT_OF_MEMORY": "ran out of memory: raise --mem",
         "FAILED": "exit code ≠ 0: check the job's output file", "NODE_FAIL": "node failed: resubmit",
         "CANCELLED": "cancelled by you or an admin"}
JUPYTER_JOB = "spawner-jupyterhub"
JUPYTER_HINT = "JupyterHub session reached its time limit (normal)"


# ---- robustness ----------------------------------------------------------------------------------------------

def gather(tasks):
    """Run {key: callable} in parallel. A failure is stored as the exception instead of raised."""
    out = {}
    with ThreadPoolExecutor(max(len(tasks), 1)) as ex:
        futs = {k: ex.submit(fn) for k, fn in tasks.items()}
        for k, f in futs.items():
            try:
                out[k] = f.result()
            except Exception as e:  # noqa: BLE001 - one collector must not take the page down
                out[k] = e
    return out


def val(d, key):
    """Value from gather(); re-raises a stored exception (so safe() can show it)."""
    v = d[key]
    if isinstance(v, Exception):
        raise v
    return v


def ok(d, key):
    """Value from gather(), or None if that collector failed."""
    v = d[key]
    return None if isinstance(v, Exception) else v


def safe(title, thunk):
    """Build one card; if it raises, show a 'no data' card with the reason instead of blanking the page."""
    try:
        return thunk()
    except Exception as e:  # noqa: BLE001
        return card(title, f'<p class="muted">no data · {esc(type(e).__name__)}: {esc(str(e)[:120])}</p>')


# ---- building blocks -----------------------------------------------------------------------------------------

def card(title, body, span=1):
    return f'<section class="card" style="grid-column:span {span}"><h3>{esc(title)}</h3>{body}</section>'


def fold(summary, body, open_=False):
    """Full-width collapsible section."""
    return (f'<section class="card" style="grid-column:1/-1"><details{" open" if open_ else ""}>'
            f'<summary>{esc(summary)}</summary>{body}</details></section>')


def table(headers, rows):
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(x)}</td>" for x in r) + "</tr>" for r in rows)
    return f"<table><tr>{h}</tr>{body or '<tr><td colspan=99><i>none</i></td></tr>'}</table>"


def kpi(icon, title, sub, value, cls="", bar=None):
    meter = ""
    if bar is not None:
        meter = (f'<div class="meter"><div style="width:{min(bar, 100):.0f}%;background:{status_fill(bar)}"></div>'
                 f'<i style="left:95%"></i></div>')
    return (f'<div class="kpi"><div class="kpi-h">{icon} {esc(title)}</div>'
            f'<div class="kpi-b {cls}"><span>{esc(sub)}</span><b>{value}</b>{meter}</div></div>')


def level(p):
    """KPI class for a fill percentage: red at 95 %, amber at 85 %."""
    return "crit" if p >= 95 else "warn" if p >= 85 else ""


def pct(q, k="used", qk="quota"):
    return 100 * q[k] / max(q[qk], 1)


def quota_tile(q, title=None, note=None):
    """KPI for one quota row: whichever of space / files is fuller."""
    sp, fp = pct(q), pct(q, "files", "fquota")
    which, p = ("space", sp) if sp >= fp else ("files", fp)
    sub = f"{size(q['used'])} / {size(q['quota'])}" if which == "space" else f"{count(q['files'])} / {count(q['fquota'])} files"
    return kpi("⚠" if p >= 95 else "▤", title or q["label"], f"{which} · {sub}" + (f" · {note}" if note else ""),
               f"{p:.0f}%", level(p), p)


def hours_text(h):
    return f"{h * 60:.0f} min" if h < 1 else f"{h:.1f} h" if h < 48 else f"{h / 24:.1f} d"


def month_label(m):
    return dt.date(int(m[:4]), int(m[5:7]), 1).strftime("%b %Y")


def user_colors(breakdown):
    """Member colours by total storage, the current user first; at most len(SERIES) members, so no colour repeats.
    Members without an entry are drawn as "other"."""
    totals = {}
    for users in breakdown.values():
        for u, v in users.items():
            totals[u] = totals.get(u, 0) + v["disk"] + v["tape"]
    order = [c.USER] + sorted((u for u in totals if u != c.USER), key=lambda u: -totals[u])
    return {u: SERIES[i] for i, u in enumerate(order[:len(SERIES)])}


def extend_colors(colors, names):
    """Same mapping plus an unused colour for new names (members seen in jobs or usage but not in storage),
    while fewer than len(SERIES) members have one."""
    out = dict(colors)
    for u in names:
        if len(out) >= len(SERIES):
            break
        if u not in out:
            out[u] = next(col for col in SERIES if col not in out.values())
    return out


# ---- storage charts ------------------------------------------------------------------------------------------

def quota_chart(quotas, key, qkey, fmt):
    rows = []
    for q in quotas:
        p = pct(q, key, qkey)
        icon = "⚠ " if p >= 85 else ""
        rows.append((q["label"], [(p, status_fill(p), f"{q['label']}: {fmt(q[key])} of {fmt(q[qkey])} ({p:.1f}%)", False, None)],
                     f'<tspan class="strong">{icon}{p:.0f}%</tspan>  {fmt(q[key])} / {fmt(q[qkey])}'))
    return bars(rows, 100, [0, 25, 50, 75, 100], lambda t: f"{t}%", ref=95)


def usage_chart(breakdown, fs, groups, colors, tape):
    """One stacked bar per group: who uses the space (tape-only hatched when tape=True)."""
    keys = sorted(k for k in breakdown if k[0] == fs and k[1] in groups)
    totals = [sum(v["disk"] + v["tape"] for v in breakdown[k].values()) for k in keys]
    xmax = max(totals + [1])
    rows = []
    for k, tot in zip(keys, totals):
        segs, other = [], 0.0
        for u, v in sorted(breakdown[k].items(), key=lambda kv: (kv[0] != c.USER, -(kv[1]["disk"] + kv[1]["tape"]))):
            t = v["disk"] + v["tape"]
            if u not in colors or t < 0.01 * tot:
                other += t
                continue
            sub = f"{u} {t / 2**40:.1f}"
            if tape and v["disk"] > 0:
                segs.append((v["disk"], colors[u], f"{u}: {size(v['disk'])} on disk", False, sub if v["disk"] >= v["tape"] else None))
            if tape and v["tape"] > 0:
                segs.append((v["tape"], colors[u], f"{u}: {size(v['tape'])} tape-only", True, sub if v["tape"] > v["disk"] else None))
            if not tape:
                segs.append((t, colors[u], f"{u}: {size(t)}", False, sub))
        if other:
            segs.append((other, OTHER, f"other users: {size(other)}", False, None))
        rows.append((k[1], segs, f'<tspan class="strong">{size(tot)}</tspan>'))
    step = max(5, round(xmax / 2**40 / 4 / 5) * 5)
    ticks = [t * 2**40 for t in range(0, int(xmax / 2**40) + 1, step)]
    return bars(rows, xmax, ticks, lambda t: f"{t / 2**40:.0f}", sublabels=True, row_h=30, value_w=90)


def my_share_chart(breakdown, quotas):
    """My /project and /nearline usage per group as % of the group quota, with size and file count."""
    rows = []
    for (fs, g), users in sorted(breakdown.items(), key=lambda kv: (kv[0][1], kv[0][0] != "project")):
        q = next((q for q in quotas if q["label"] == f"{g} /{fs}"), None)
        if not q:
            continue
        mine = users.get(c.USER, {"disk": 0.0, "tape": 0.0, "files": 0})
        used = mine["disk"] + mine["tape"]
        p = 100 * used / max(q["quota"], 1)
        rows.append((f"{g} /{fs}", [(p, SERIES[0], f"{c.USER}: {size(used)} of {size(q['quota'])} ({p:.2f}%)", False, None)],
                     f'<tspan class="strong">{size(used)}</tspan> · {p:.2f}% · {mine["files"]:,} files'))
    if not rows:
        return '<p class="muted">No group storage found.</p>'
    xmax = max([r[1][0][0] for r in rows] + [1.0])
    step = nice_step(xmax)
    xmax = step * -(-xmax // step)
    return bars(rows, xmax, [t * step for t in range(int(xmax / step) + 1)], lambda t: f"{t:g}%", value_w=220)


# ---- compute charts ------------------------------------------------------------------------------------------

def nodes_chart(nodes):
    rows = []
    for kind in ("Regular CPU", "Large-mem CPU", "GPU nodes (H100)"):
        if kind not in nodes:
            continue
        a, i, o, t, n = nodes[kind]
        rows.append((kind, [(100 * a / t, SERIES[0], f"{a:,} cores busy", False, None),
                            (100 * i / t, GOOD, f"{i:,} cores idle", False, None),
                            (100 * o / t, OTHER, f"{o:,} cores down/drained", False, None)],
                     f'<tspan class="strong">{100 * i / t:.0f}% idle</tspan>  {count(i)} / {count(t)} cores'))
    return bars(rows, 100, [0, 50, 100], lambda t: f"{t}%")


def fairshare_chart(fs):
    rows = []
    for acct, norm, eff, fair, level in fs:
        f = float(fair or 0)
        rows.append((acct, [(f, SERIES[0], f"FairShare {f:.3f} · LevelFS {level} · usage {float(eff or 0):.3f} of share {float(norm or 0):.3f}", False, None)],
                     f'<tspan class="strong">{f:.2f}</tspan>'))
    return bars(rows, 1, [0, 0.5, 1], lambda t: f"{t:g}", value_w=80)


def usage_month_chart(usage, kind, colors):
    """Stacked columns: hours per calendar month by member. usage = collectors.usage_by_month(); kind 'cpu' | 'gpu'."""
    months = sorted(usage)
    totals = {}
    for m in months:
        for u, v in usage[m].items():
            totals[u] = totals.get(u, 0.0) + v[kind]
    order = [u for u in sorted(totals, key=lambda u: -totals[u]) if round(totals[u]) > 0]
    if not order:
        return '<p class="muted">No usage recorded in this period.</p>'
    top, rest = [u for u in order if u in colors], [u for u in order if u not in colors]
    series = [(u, colors[u], [round(usage[m].get(u, {}).get(kind, 0.0)) for m in months])
              for u in top]
    if rest:
        series.append(("other", OTHER, [round(sum(usage[m].get(u, {}).get(kind, 0.0) for u in rest)) for m in months]))
    return columns([month_label(m) for m in months], series, max_bw=90) + legend([(n, col) for n, col, _ in series])


def queue_wait_chart(waits, days):
    """Median and 90th-percentile queue wait per whole week, in hours. waits = collectors.queue_wait().

    Percentiles are upper nearest-rank (with 1-2 jobs in a week the median equals the max)."""
    if not waits:
        return '<p class="muted">No started jobs in this window.</p>'
    today = dt.date.today()
    n = max(1, days // 7)
    buckets = [[] for _ in range(n)]
    for d, w in waits:
        age = (today - dt.date.fromisoformat(d)).days
        if 0 <= age < 7 * n:
            buckets[n - 1 - age // 7].append(w / 3600)
    q = lambda xs, f: sorted(xs)[min(len(xs) - 1, int(f * len(xs)))] if xs else 0.0
    med = [round(q(b, 0.5), 1) for b in buckets]
    p90 = [round(q(b, 0.9), 1) for b in buckets]
    labels = [f"wk to {(today - dt.timedelta(days=7 * k)).strftime('%b %d')}" for k in range(n - 1, -1, -1)]
    return (line(labels, [("median", SERIES[0], med), ("90th pct", CRIT, p90)])
            + legend([("median wait (h)", SERIES[0]), ("90th percentile (h)", CRIT)])
            + '<p class="muted">Started jobs only, bucketed by start week; a week with no started jobs shows 0.</p>')


def in_window(waits, days):
    """Entries of collectors.queue_wait() whose start date is within the last `days` days."""
    today = dt.date.today()
    return [(d, w) for d, w in waits if 0 <= (today - dt.date.fromisoformat(d)).days < days]


# ---- job charts and tables ----------------------------------------------------------------------------------

def jobs_per_day(hist, days):
    """hist rows = collectors.recent(): [JobID, JobName, State, Elapsed, Timelimit, ReqMem, ExitCode, Account, End, User]."""
    today = dt.date.today()
    dates = [today - dt.timedelta(days=d) for d in range(days - 1, -1, -1)]
    idx = {d.isoformat(): i for i, d in enumerate(dates)}
    good, bad = [0] * days, [0] * days
    for r in hist:
        i = idx.get(r[8][:10])
        if i is None:
            continue
        if r[2].startswith("COMPLETED"):
            good[i] += 1
        elif r[2].partition(" ")[0] in BAD_STATES:
            bad[i] += 1
    labels = [d.strftime("%b %d") for d in dates]
    return line(labels, [("completed", SERIES[0], good), ("problems", CRIT, bad)]) + legend([("completed", SERIES[0]), ("problems", CRIT)])


def member_chart(hist):
    """Jobs by member in the window: completed vs problems."""
    by_user = {}
    for r in hist:
        st_ = r[2].partition(" ")[0]
        d = by_user.setdefault(r[9] if len(r) > 9 and r[9] else c.USER, [0, 0])
        if st_ == "COMPLETED":
            d[0] += 1
        elif st_ in BAD_STATES:
            d[1] += 1
    if not by_user:
        return '<p class="muted">No finished jobs in this window.</p>'
    xu = int(nice_step(max(sum(v) for v in by_user.values())) * 4)
    rows = [(u, [(v[0], SERIES[0], f"{u}: {v[0]} completed", False, None), (v[1], CRIT, f"{u}: {v[1]} problems", False, None)],
             f'<tspan class="strong">{v[0]:,}</tspan> ok · {v[1]:,} problems')
            for u, v in sorted(by_user.items(), key=lambda kv: -sum(kv[1]))]
    return (bars(rows, xu, list(range(0, xu + 1, int(nice_step(xu)))), lambda t: f"{t:,}", value_w=170)
            + legend([("completed", SERIES[0]), ("problems", CRIT)]))


def problems(hist):
    """recent() rows whose state is a problem; rows too short for problems_table are skipped."""
    return [r for r in hist if len(r) > 9 and r[2].partition(" ")[0] in BAD_STATES]


def problems_table(bad, with_user=False):
    """bad = recent() rows in BAD_STATES. Newest first, at most 300."""
    head = ["JobID", "Name", "State", "Exit", "Elapsed / limit", "What to do"] + (["User"] if with_user else [])
    rows = []
    for r in bad[::-1][:300]:
        st_ = r[2].partition(" ")[0]
        rows.append([r[0], r[1], st_.replace("_", " ").lower(), r[6], f"{r[3]} / {r[4]}", JUPYTER_HINT if r[1] == JUPYTER_JOB else HINTS.get(st_, "")] + ([r[9]] if with_user else []))
    return table(head, rows)


def pending_table(pend):
    """pend = collectors.pending_start(): [JobID, Name, Account, CPUs, Reason, Start]."""
    rows = []
    for r in pend[:200]:
        jid, name, acct, cpus, reason, start = (r + [""] * 6)[:6]
        rows.append([jid, name, acct, cpus, f"{reason} · {REASONS.get(reason, '')}".rstrip(" ·"),
                     start.replace("T", " ") if start not in ("N/A", "") else "unknown"])
    return table(["JobID", "Name", "Account", "CPUs", "Reason", "Expected start"], rows)


def efficiency_card(eff, days):
    """eff = collectors.efficiency(). Returns (card html, cpu_eff %, mem_eff %, idle core-hours)."""
    if not eff:
        return card(f"My job efficiency · last {days} d", '<p class="muted">No finished jobs of 2 min or more in this window.</p>'), None, None, 0
    by = {}
    for j in eff:
        d = by.setdefault(j["name"], {"n": 0, "core_s": 0.0, "cpu_s": 0.0, "req": 0.0, "peak": 0.0, "mem_eff": []})
        d["n"] += 1
        d["core_s"] += j["elapsed"] * j["cpus"]
        d["cpu_s"] += j["cpu"]
        d["req"] = max(d["req"], j["req"])
        d["peak"] = max(d["peak"], j["rss"])
        if j["req"] > 0:
            d["mem_eff"].append(j["rss"] / j["req"])
    top = sorted(by.items(), key=lambda kv: -kv[1]["core_s"])[:10]
    xmax = max([d["req"] for _, d in top] + [1])
    rows = []
    for name, d in top:
        ce = 100 * d["cpu_s"] / max(d["core_s"], 1)
        me = 100 * sorted(d["mem_eff"])[len(d["mem_eff"]) // 2] if d["mem_eff"] else 0
        rows.append((name[:20], [(d["peak"], SERIES[0], f"{name}: peak memory {size(d['peak'])} (max over {d['n']} jobs)", False, None),
                                 (max(d["req"] - d["peak"], 0), OTHER, f"{name}: requested {size(d['req'])}, never used {size(max(d['req'] - d['peak'], 0))}", True, None)],
                     f'mem <tspan class="strong">{me:.0f}%</tspan> · CPU <tspan class="strong">{ce:.0f}%</tspan> · {d["n"]} jobs'))
    step = nice_step(xmax / 2**30)
    ticks = [t * 2**30 for t in range(0, int(xmax / 2**30) + 1, max(int(step), 1))]
    chart = bars(rows, xmax, ticks, lambda t: f"{t / 2**30:.0f}", label_w=150, value_w=210)
    core_s = sum(j["elapsed"] * j["cpus"] for j in eff)
    cpu_eff = 100 * sum(j["cpu"] for j in eff) / max(core_s, 1)
    mem = sorted(j["rss"] / j["req"] for j in eff if j["req"] > 0)
    mem_eff = 100 * mem[len(mem) // 2] if mem else None
    idle_h = (core_s - sum(j["cpu"] for j in eff)) / 3600
    body = (chart + legend([("peak memory used", SERIES[0])]).replace("</div>", '<span><i class="hatch"></i>requested, never used</span></div>')
            + '<p class="muted">GiB. Per job name, top 10 by core-hours; mem = median peak / requested; CPU = CPU time / (elapsed × cores).</p>')
    return card(f"My job efficiency · last {days} d · {len(eff)} jobs", body), cpu_eff, mem_eff, idle_h


def folders_table(rows, timeout=30):
    """rows = collectors.my_folders(). A None size means du did not finish within the time budget."""
    if not rows:
        return '<p class="muted">No folders of yours in the top two levels of /project.</p>'
    body = [[label, size(n) if n is not None else f"not measured (> {timeout} s)", mtime] for label, n, mtime in rows]
    return (table(["Folder", "Size of everything inside", "Modified"], body)
            + '<p class="muted">Folders you own (created by you) in the top two levels of /project. Shared folders someone '
              'else created are not listed even if you can write to them. Rescanned once an hour.</p>')

