import base64
import datetime as dt
import json
from pathlib import Path
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

from . import collectors as c
from .charts import (CRIT, GOOD, OTHER, SERIES, WARN_FILL, bars, columns, radial, count, esc, hatch_defs, legend, line, nice_step,
                     size, status_fill)

CACHE_TTL = 60
STORAGE_TTL = 600
BAD_STATES = ("FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "CANCELLED")
PAGES = [("overview", "◉", "Overview"), ("storage", "▤", "Storage"), ("compute", "▦", "Compute"), ("jobs", "☰", "Jobs")]
FILESYSTEMS = ["project", "nearline", "home", "scratch"]
_slow = {"t": 0, "data": None}
_lock = threading.Lock()
_ttl_cache = {}
HISTORY = Path.home() / ".firdash" / "history.jsonl"
HISTORY_EVERY = 3300


def cached(key, ttl, fn, *args):
    t, v = _ttl_cache.get(key, (0, None))
    if v is None or time.time() - t > ttl:
        v = fn(*args)
        _ttl_cache[key] = (time.time(), v)
    return v


def record_history(rows):
    """Append one usage snapshot per ~hour to ~/.firdash/history.jsonl (drives trends and 'full in' forecasts)."""
    try:
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        last = 0.0
        if HISTORY.exists():
            with open(HISTORY, "rb") as f:
                f.seek(max(0, HISTORY.stat().st_size - 4096))
                tail = f.read().decode(errors="replace").strip().splitlines()
                last = json.loads(tail[-1])["t"] if tail else 0.0
        if time.time() - last < HISTORY_EVERY:
            return
        snap = {"t": time.time(), "q": {r["label"]: [r["used"], r["quota"], r["files"], r["fquota"]] for r in rows}}
        with open(HISTORY, "a") as f:
            f.write(json.dumps(snap) + "\n")
    except (OSError, ValueError, KeyError):
        pass


def load_history(days=30):
    if not HISTORY.exists():
        return []
    cut = time.time() - days * 86400
    out = []
    for l in HISTORY.read_text(errors="replace").splitlines():
        try:
            d = json.loads(l)
        except ValueError:
            continue
        if d.get("t", 0) >= cut:
            out.append(d)
    return out


def storage():
    with _lock:
        return _storage()


def _storage():
    if _slow["data"] is None or time.time() - _slow["t"] > STORAGE_TTL:
        gs = c.groups()
        with ThreadPoolExecutor(8) as ex:
            gq = ex.submit(c.group_quotas)
            uq = ex.submit(c.user_quotas)
            bd = {(fs, g): ex.submit(c.user_breakdown, fs, g) for fs in ("project", "nearline") for g in gs}
            fsys = ex.submit(c.filesystems)
            _slow["data"] = {"groups": gs, "quotas": gq.result() + uq.result(), "filesystems": fsys.result(),
                             "breakdown": {k: f.result() for k, f in bd.items()}, "asof": c.stats_asof()}
        _slow["t"] = time.time()
        record_history(_slow["data"]["quotas"] + _slow["data"]["filesystems"])
    return _slow["data"]


def pct(q, k="used", qk="quota"):
    return 100 * q[k] / max(q[qk], 1)


def quota_chart(quotas, key, qkey, fmt):
    rows = []
    for q in quotas:
        p = pct(q, key, qkey)
        icon = "⚠ " if p >= 85 else ""
        rows.append((q["label"], [(p, status_fill(p), f"{q['label']}: {fmt(q[key])} of {fmt(q[qkey])} ({p:.1f}%)", False, None)],
                     f'<tspan class="strong">{icon}{p:.0f}%</tspan>  {fmt(q[key])} / {fmt(q[qkey])}'))
    return bars(rows, 100, [0, 25, 50, 75, 100], lambda t: f"{t}%", ref=95)


def user_colors(breakdown):
    totals = {}
    for users in breakdown.values():
        for u, v in users.items():
            totals[u] = totals.get(u, 0) + v["disk"] + v["tape"]
    order = [c.USER] + sorted((u for u in totals if u != c.USER), key=lambda u: -totals[u])
    return {u: SERIES[i] for i, u in enumerate(order[:len(SERIES)])}


def usage_chart(breakdown, fs, groups, colors, tape):
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


def jobs_per_day(hist, days):
    today = dt.date.today()
    dates = [today - dt.timedelta(days=d) for d in range(days - 1, -1, -1)]
    idx = {d.isoformat(): i for i, d in enumerate(dates)}
    ok, bad = [0] * days, [0] * days
    for r in hist:
        i = idx.get(r[8][:10])
        if i is None:
            continue
        if r[2].startswith("COMPLETED"):
            ok[i] += 1
        elif r[2].split()[0] in BAD_STATES:
            bad[i] += 1
    labels = [d.strftime("%b %d") for d in dates]
    return line(labels, [("completed", SERIES[0], ok), ("problems", CRIT, bad)]) + legend([("completed", SERIES[0]), ("problems", CRIT)])


STATE_COLORS = {"FAILED": CRIT, "TIMEOUT": "#ec835a", "OUT_OF_MEMORY": WARN_FILL, "NODE_FAIL": "#9085e9",
                "CANCELLED": OTHER}


def problems_radial(rows, me):
    users, states = {}, []
    for r in rows:
        u = r[9] if len(r) > 9 else me
        st_ = r[2].split()[0]
        users.setdefault(u, {})[st_] = users.setdefault(u, {}).get(st_, 0) + 1
        if st_ not in states:
            states.append(st_)
    if not users:
        return '<p class="muted">No problem jobs.</p>'
    order = sorted(users, key=lambda u: -sum(users[u].values()))
    states = [k for k in STATE_COLORS if k in states]
    series = [(k.replace("_", " ").lower(), STATE_COLORS[k], [users[u].get(k, 0) for u in order]) for k in states]
    return radial(order, series) + legend([(name, col) for name, col, _ in series])


def problems_over_time(rows, days, colors, me):
    today = dt.date.today()
    dates = [today - dt.timedelta(days=d) for d in range(days - 1, -1, -1)]
    idx = {d.isoformat(): i for i, d in enumerate(dates)}
    per = {}
    for r in rows:
        i = idx.get(r[8][:10])
        if i is None:
            continue
        u = r[9] if len(r) > 9 else me
        per.setdefault(u, [0] * days)[i] += 1
    if not per:
        return '<p class="muted">No problem jobs.</p>'
    order = sorted(per, key=lambda u: -sum(per[u]))
    series = [(u, colors.get(u, OTHER), per[u]) for u in order]
    return line([d.strftime("%b %d") for d in dates], series) + legend([(u, col) for u, col, _ in series])


def forecast(hist, labels, key, qkey):
    """{label: (now %, % points per day over the last 7 days, days until 100 % or None)} from history snapshots."""
    i, qi = (0, 1) if key == "used" else (2, 3)
    out = {}
    for lab in labels:
        pts = [(d["t"] / 86400, 100 * d["q"][lab][i] / max(d["q"][lab][qi], 1)) for d in hist if lab in d["q"]]
        pts = [p for p in pts if p[0] >= pts[-1][0] - 7] if pts else []
        if len(pts) < 2 or pts[-1][0] - pts[0][0] < 0.5:
            out[lab] = (pts[-1][1] if pts else None, None, None)
            continue
        n = len(pts)
        mx, my = sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n
        sxx = sum((p[0] - mx) ** 2 for p in pts)
        slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx if sxx else 0.0
        now = pts[-1][1]
        out[lab] = (now, slope, (100 - now) / slope if slope > 1e-6 and now < 100 else None)
    return out


def trend_card(hist, quotas):
    labels = [q["label"] for q in sorted(quotas, key=lambda q: -max(pct(q), pct(q, "files", "fquota")))]
    fc_s, fc_f = forecast(hist, labels, "used", "quota"), forecast(hist, labels, "files", "fquota")
    full = lambda d: "—" if d is None else ("< 1 day" if d < 1 else f"{d:.0f} days" if d < 365 else "> 1 year")
    rate = lambda r: "—" if r is None else f"{7 * r:+.1f} pp/week"
    rows = [[lab, f"{fc_s[lab][0]:.1f}%" if fc_s[lab][0] is not None else "—", rate(fc_s[lab][1]), full(fc_s[lab][2]),
             f"{fc_f[lab][0]:.1f}%" if fc_f[lab][0] is not None else "—", rate(fc_f[lab][1]), full(fc_f[lab][2])] for lab in labels]
    days = sorted({dt.date.fromtimestamp(d["t"]) for d in hist})
    chart = '<p class="muted">Collecting history: one snapshot per hour is kept in ~/.firdash/history.jsonl; trends appear after a day.</p>'
    if len(days) >= 2:
        series = []
        for k, lab in enumerate(labels[:6]):
            last, vals = 0.0, []
            for day in days:
                v = [100 * d["q"][lab][0] / max(d["q"][lab][1], 1) for d in hist if lab in d["q"] and dt.date.fromtimestamp(d["t"]) == day]
                last = v[-1] if v else last
                vals.append(round(last, 1))
            series.append((lab, SERIES[k % len(SERIES)], vals))
        chart = line([d.strftime("%b %d") for d in days], series)
    return card("Usage trend and forecast · space % of quota", chart + table(
        ["Quota", "Space now", "Space trend", "Space full in", "Files now", "Files trend", "Files full in"], rows), 2)


def fs_card(fsys):
    shared = [r["label"] for r in fsys if " + " in r["label"]]
    fsys = [dict(r, label="shared disk" if " + " in r["label"] else r["label"]) for r in fsys]
    files = [dict(r, used=r["files"], quota=r["fquota"]) for r in fsys]
    note = (f'<p class="muted" style="margin:4px 0 0;font-size:12px">shared disk = {esc(shared[0].replace(" + ", ", "))}: '
            'one filesystem for every Fir user</p>') if shared else ""
    return card("Whole filesystems · all users (not your quota)",
                note + '<div class="flab">space</div>' + quota_chart(fsys, "used", "quota", size)
                + '<div class="flab">files (inodes)</div>' + quota_chart(files, "used", "quota", count))


def arrays_card(arr):
    if not arr:
        return card("Array jobs", '<p class="muted">No array jobs in this window.</p>')
    active = lambda d: d["RUNNING"] + d["PENDING"] > 0
    keys = sorted(arr, key=lambda k: (not active(arr[k]), "" if active(arr[k]) else arr[k]["end"]), reverse=False)
    keys = [k for k in keys if active(arr[k])] + sorted((k for k in keys if not active(arr[k])), key=lambda k: arr[k]["end"], reverse=True)
    rows = []
    for k in keys[:12]:
        d = arr[k]
        tot = d["COMPLETED"] + d["RUNNING"] + d["PENDING"] + d["BAD"]
        rows.append((f"{k} {d['name']}"[:22], [(100 * d["COMPLETED"] / tot, SERIES[0], f"{d['COMPLETED']} completed", False, None),
                                              (100 * d["RUNNING"] / tot, GOOD, f"{d['RUNNING']} running", False, None),
                                              (100 * d["PENDING"] / tot, SERIES[1], f"{d['PENDING']} pending", False, None),
                                              (100 * d["BAD"] / tot, CRIT, f"{d['BAD']} failed/cancelled/timeout", False, None)],
                     f'<tspan class="strong">{d["COMPLETED"]}/{tot}</tspan> done' + (f' · {d["BAD"]} bad' if d["BAD"] else "")))
    return card(f"Array jobs · {sum(active(d) for d in arr.values())} active", bars(rows, 100, [0, 50, 100], lambda t: f"{t}%", label_w=170, value_w=150)
                + legend([("completed", SERIES[0]), ("running", GOOD), ("pending", SERIES[1]), ("failed", CRIT)]))


REASONS = {"Priority": "waiting for higher-priority jobs", "Resources": "next in line, waiting for free nodes",
           "Dependency": "waits for another job", "DependencyNeverSatisfied": "dependency failed: will never start",
           "AssocGrpCPULimit": "account core limit reached", "QOSMaxJobsPerUserLimit": "per-user job limit",
           "ReqNodeNotAvail": "requested nodes unavailable (maintenance?)", "BeginTime": "scheduled for later"}


def pending_card(pend):
    rows = []
    for r in pend[:200]:
        jid, name, acct, cpus, reason, start = (r + [""] * 6)[:6]
        rows.append([jid, name, acct, cpus, f"{reason} · {REASONS.get(reason, '')}".rstrip(" ·"),
                     start.replace("T", " ") if start not in ("N/A", "") else "unknown"])
    return (f'<section class="card" style="grid-column:1/-1"><details {"open" if pend else ""}><summary>Pending jobs: expected start and reason ({len(pend)})</summary>'
            f'{table(["JobID", "Name", "Account", "CPUs", "Reason", "Expected start"], rows)}</details></section>')


ANIM = Path(__file__).resolve().parent.parent / "docs" / "data_anim.gif"   # gifer.com animation; internal use only


def _until(start):
    try:
        sec = (dt.datetime.fromisoformat(start) - dt.datetime.now()).total_seconds()
    except ValueError:
        return "unknown"
    if sec <= 60:
        return "now"
    h, m = int(sec // 3600), int(sec % 3600 // 60)
    return f"in {h // 24} d {h % 24} h" if h >= 24 else f"in {h} h {m:02d} m" if h else f"in {m} min"


def pending_panel(lab, plab):
    """Lab summary: per member running / pending jobs (array tasks counted) and the next expected start."""
    users = {}
    for r in lab:
        if r[3] == "RUNNING":
            users.setdefault(r[1], {"run": 0, "pend": 0, "starts": [], "reasons": {}})["run"] += 1
    for r in plab:
        d = users.setdefault(r[1], {"run": 0, "pend": 0, "starts": [], "reasons": {}})
        d["pend"] += r[7]
        if r[6] not in ("N/A", ""):
            d["starts"].append(r[6])
        reason = r[5].split(",")[0]
        d["reasons"][reason] = d["reasons"].get(reason, 0) + r[7]
    if not users:
        return card("Lab jobs · running, pending, expected start", '<p class="muted">No running or pending jobs on your accounts.</p>')
    rows = []
    for u, d in sorted(users.items(), key=lambda kv: -(kv[1]["run"] + kv[1]["pend"])):
        if d["starts"]:
            st = sorted(d["starts"])
            when = f'next {_until(st[0])}' + (f' · median {_until(st[len(st) // 2])}' if len(st) > 2 else "")
        elif d["pend"]:
            top = max(d["reasons"], key=d["reasons"].get)
            when = f'no estimate · {top}'
        else:
            when = "nothing pending"
        tip_r = ", ".join(f"{k} {v}" for k, v in sorted(d["reasons"].items(), key=lambda kv: -kv[1]))
        tot = max(d["run"] + d["pend"], 1)
        rows.append((u, [(100 * d["run"] / tot, GOOD, f"{u}: {d['run']} running", False, None),
                         (100 * d["pend"] / tot, SERIES[1], f"{u}: {d['pend']} pending ({tip_r})", False, None)],
                     f'<tspan class="strong">{d["run"]}</tspan> run · <tspan class="strong">{d["pend"]}</tspan> pend · {esc(when)}'))
    n_run = sum(d["run"] for d in users.values()); n_pend = sum(d["pend"] for d in users.values())
    return card(f"Lab jobs · {n_run} running · {n_pend} pending · expected start",
                bars(rows, 100, [0, 50, 100], lambda t: f"{t}%", label_w=110, value_w=330)
                + legend([("running", GOOD), ("pending (array tasks counted)", SERIES[1])]) + '<p class="muted">Bar = each member\'s share running vs pending; counts on the right.</p>'
                + '<p class="muted">Expected start = Slurm\'s estimate (squeue --start); many pending jobs have none yet.</p>')


def anim_panel():
    """Decorative filler: sized by the page script to fill the empty end of the last grid row."""
    if not ANIM.is_file():
        return ""
    return (f'<section class="card anim-card" data-filler="1"><img class="anim" alt="" '
            f'src="data:image/gif;base64,{base64.b64encode(ANIM.read_bytes()).decode()}"></section>')


def efficiency_card(eff, days):
    if not eff:
        return card(f"Job efficiency · last {days} d", '<p class="muted">No finished jobs of 2 min or more in this window.</p>'), None, None, 0
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
    body = (chart + legend([("peak memory used", SERIES[0])]) .replace("</div>", '<span><i class="hatch"></i>requested, never used</span></div>')
            + '<p class="muted">GiB. Per job name, top 10 by core-hours; mem = median peak / requested; CPU = CPU time / (elapsed × cores).</p>')
    return card(f"Job efficiency · last {days} d · {len(eff)} jobs", body), cpu_eff, mem_eff, idle_h


def kpi(icon, title, sub, value, cls="", bar=None):
    meter = ""
    if bar is not None:
        meter = (f'<div class="meter"><div style="width:{min(bar, 100):.0f}%;background:{status_fill(bar)}"></div>'
                 f'<i style="left:95%"></i></div>')
    return (f'<div class="kpi"><div class="kpi-h">{icon} {esc(title)}</div>'
            f'<div class="kpi-b {cls}"><span>{esc(sub)}</span><b>{value}</b>{meter}</div></div>')


def quota_tile(q):
    sp, fp = pct(q), pct(q, "files", "fquota")
    which, p = ("space", sp) if sp >= fp else ("files", fp)
    sub = f"{size(q['used'])} / {size(q['quota'])}" if which == "space" else f"{count(q['files'])} / {count(q['fquota'])} files"
    return kpi("⚠" if p >= 95 else "▤", q["label"], f"{which} · {sub}", f"{p:.0f}%", "crit" if p >= 95 else "", p)


def card(title, body, span=1):
    return f'<section class="card" style="grid-column:span {span}"><h3>{esc(title)}</h3>{body}</section>'


def table(headers, rows):
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(x)}</td>" for x in r) + "</tr>" for r in rows)
    return f"<table><tr>{h}</tr>{body or '<tr><td colspan=99><i>none</i></td></tr>'}</table>"


LOGO = Path(__file__).resolve().parent.parent / "docs" / "logo.png"
ABOUT = ("A quick view of your DRAC (Fir cluster) resources: storage quotas and file counts, who uses the "
         "group space, cluster load, fairshare, and the jobs of you and your lab. "
         "Read-only: it only reads the cluster's own reports under your account.")


def logo():
    if not LOGO.is_file():
        return ""
    return f'<img class="logo" src="data:image/png;base64,{base64.b64encode(LOGO.read_bytes()).decode()}" alt="logo">'


HERO = Path(__file__).resolve().parent.parent / "docs" / "h100.jpg"   # image: tatacommunications.com (NVIDIA H100); internal use only


def hero():
    if not HERO.is_file():
        return ""
    return f'<img class="hero" src="data:image/jpeg;base64,{base64.b64encode(HERO.read_bytes()).decode()}" alt="">'


def about():
    return f'<div class="about"><p>{esc(ABOUT)}</p></div>'


def sidebar(page, groups, sel_groups, sel_fs, days, who="me"):
    nav = "".join(
        f'<a class="nav {"on" if p == page else ""}" href="?{urlencode({"p": p, "g": ",".join(sel_groups), "fs": ",".join(sel_fs), "days": days, "who": who})}">'
        f'<span>{icon}</span>{label}</a>' for p, icon, label in PAGES)
    chips = lambda name, opts, sel: "".join(
        f'<label class="chip"><input type="checkbox" name="{name}" value="{o}" {"checked" if o in sel else ""} onchange="go()">{esc(o)}</label>'
        for o in opts)
    dsel = "".join(f'<option {"selected" if d == days else ""}>{d}</option>' for d in (7, 14, 30, 90))
    return f"""<aside>
<div class="brand">▲ DRAC <small>{esc(c.USER)}</small></div>
<nav>{nav}</nav>
<h4>Boards</h4><div id="boards" class="boards"></div>
<form id="f" method="get">
 <input type="hidden" name="p" value="{page}"><input type="hidden" name="g"><input type="hidden" name="fs"><input type="hidden" name="b">
 <h4>Filter here</h4>
 <div class="flab">Groups</div><div class="chips" data-for="g">{chips("gx", groups, sel_groups)}</div>
 <div class="flab">Filesystems</div><div class="chips" data-for="fs">{chips("fsx", FILESYSTEMS, sel_fs)}</div>
 <div class="flab">Job history (days)</div><select name="days" onchange="go()">{dsel}</select>
 <div class="flab">Jobs of</div><select name="who" onchange="go()"><option value="me" {"selected" if who == "me" else ""}>me</option><option value="lab" {"selected" if who == "lab" else ""}>all members</option></select>
</form>
{about()}
<div class="foot">{time.strftime('%Y-%m-%d %H:%M')} · {socket.gethostname().split('.')[0]}<br>storage every {STORAGE_TTL // 60} min · jobs every {CACHE_TTL} s</div>
</aside>"""


def static_sidebar(days):
    return f"""<aside>
<div class="brand">▲ DRAC <small>{esc(c.USER)}</small></div>
<nav><a class="nav on"><span>◉</span>Snapshot</a></nav>
<h4>Boards</h4><div id="boards" class="boards"></div>
<div class="flab">All groups · all filesystems · jobs of all members, last {days} d</div>
{about()}
<div class="foot">{time.strftime('%Y-%m-%d %H:%M')} · {socket.gethostname().split('.')[0]}<br>static snapshot: rerun to refresh</div>
</aside>"""


SCRIPT = """<script>
function go(){const f=document.getElementById('f');
 for(const d of f.querySelectorAll('.chips')){
  f.elements[d.dataset.for].value=[...d.querySelectorAll('input:checked')].map(i=>i.value).join(',')||'none';
  d.querySelectorAll('input').forEach(i=>i.disabled=true);}
 f.submit();}
function focusB(key){
 const old=document.getElementById('ov'); if(old) old.remove();
 document.body.classList.toggle('focus',!!key);
 document.querySelectorAll('#boards .bd').forEach(b=>b.classList.toggle('sel',(b.dataset.key||'')===key));
 const f=document.getElementById('f'); if(f&&f.elements.b) f.elements.b.value=key;
 const u=new URL(location.href); u.searchParams.delete('b'); u.hash=key; history.replaceState(null,'',u);
 const s=key&&document.querySelector('.grid>section[data-key="'+key+'"]'); if(!s) return;
 const ov=document.createElement('div'); ov.id='ov';
 const c=s.cloneNode(true); c.style.gridColumn=''; c.querySelectorAll('details').forEach(d=>d.open=true);
 const z=c.querySelector('.zoom'); if(z){z.textContent='\\u2715'; z.title='Close (Esc)';}
 ov.appendChild(c); document.body.appendChild(ov);
 ov.addEventListener('click',e=>{if(e.target.closest('summary,a,input,select,label,table')&&!e.target.closest('.zoom')) return;
  if(window.getSelection&&String(window.getSelection())) return; focusB('');});}
(function(){
 const g=document.querySelector('.grid'), list=document.getElementById('boards'); if(!g||!list) return;
 const all=document.createElement('button'); all.className='bd all'; all.dataset.key=''; all.textContent='\\u25A6 All panels';
 all.onclick=()=>focusB(''); list.appendChild(all);
 [...g.children].filter(s=>!s.dataset.filler).forEach((s,i)=>{
  const h=s.querySelector('h3,summary'); const full=(h?h.textContent:'Board '+(i+1)).trim();
  const name=full.split(/ \\u00B7 |\\(/)[0].trim().replace(/:$/,'');
  let key=name.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')||('b'+i);
  while(g.querySelector('[data-key="'+key+'"]')) key+='-'+i;
  s.dataset.key=key;
  const SHORT=[['space quota','Space'],['file-count','Files'],['whole filesystems','Filesystems'],['/project by user','Project use'],
   ['/nearline by user','Nearline use'],['job efficiency','Efficiency'],['jobs finished per day','Jobs / day'],['jobs by member','Members'],
   ['problem jobs by member','Problems'],['problem jobs per day','Problems / day'],['cluster cores','Cores'],['fairshare','Fairshare'],
   ['lab queue,','Lab queue list'],['lab queue','Lab queue'],['pending jobs','Pending'],['maintenance','Maintenance'],['queue','Queue']];
  const low=full.toLowerCase(); const hit=SHORT.find(p=>low.startsWith(p[0]));
  const b=document.createElement('button'); b.className='bd'; b.dataset.key=key; b.textContent=hit?hit[1]:name.split(' ').slice(0,2).join(' '); b.title=full;
  b.onclick=()=>focusB(key); list.appendChild(b);
  const z=document.createElement('button'); z.className='zoom'; z.title='Enlarge / back to all boards (Esc)'; z.textContent='\\u2922';
  z.onclick=e=>{e.preventDefault();e.stopPropagation();focusB(key);}; s.prepend(z);
  s.addEventListener('click',e=>{
   if(e.target.closest('summary,a,button,input,select,label,table')) return;
   if(window.getSelection&&String(window.getSelection())) return;
   focusB(key);});});
 const want=new URLSearchParams(location.search).get('b')||decodeURIComponent(location.hash.slice(1));
 if(want&&g.querySelector('[data-key="'+want+'"]')) focusB(want);
 document.addEventListener('keydown',e=>{if(e.key==='Escape') focusB('');});})();
function fillGrid(){
 const g=document.querySelector('.grid'); if(!g) return;
 const cols=getComputedStyle(g).gridTemplateColumns.split(' ').filter(Boolean).length||1;
 g.querySelectorAll('[data-filler]').forEach(f=>{
  let used=0;
  for(let e=f.previousElementSibling;e;e=e.previousElementSibling){
   const gc=e.style.gridColumn||'';
   if(gc.indexOf('-1')>=0) break;
   const m=gc.match(/span\\s+(\\d+)/); used+=m?Math.min(+m[1],cols):1;}
  const rem=(cols-used%cols)%cols;
  f.style.display=rem?'':'none'; f.style.gridColumn=rem?'span '+rem:'';});}
fillGrid(); window.addEventListener('resize',fillGrid);
</script>"""


def build(params):
    page = params.get("p", "overview")
    page = page if page in {p for p, _, _ in PAGES} else "overview"
    days = int(params.get("days", "7")) if params.get("days", "7").isdigit() else 7
    who = "lab" if params.get("static") else ("lab" if params.get("who") == "lab" else "me")

    fs = c.fairshare()
    accounts = [r[0] for r in fs]
    with ThreadPoolExecutor(11) as ex:
        futs = [ex.submit(storage), ex.submit(c.jobs), ex.submit(c.recent, days), ex.submit(c.nodes),
                ex.submit(c.group_jobs, accounts), ex.submit(c.recent, days, accounts) if who == "lab" else None,
                ex.submit(c.pending_start), ex.submit(c.pending_lab, accounts),
                ex.submit(cached, ("eff", days), STORAGE_TTL, c.efficiency, days),
                ex.submit(cached, "resv", STORAGE_TTL, c.reservations)]
        st, jb, hist, nd, lab, hist_lab, pend, plab, eff, resv = (f.result() if f else None for f in futs)
    hist_lab = hist_lab if who == "lab" else hist

    groups = st["groups"]
    sel_groups = [g for g in params.get("g", ",".join(groups)).split(",") if g in groups]
    sel_fs = [f for f in params.get("fs", ",".join(FILESYSTEMS)).split(",") if f in FILESYSTEMS]

    order = {"/project": 0, "/nearline": 1, "/home": 2, "/scratch": 3}
    quotas = sorted(
        (q for q in st["quotas"]
         if q["label"].split()[1][1:] in sel_fs and (q["label"].split()[0] in sel_groups or q["label"].startswith(c.USER))),
        key=lambda q: (order.get(q["label"].split()[1], 9), q["label"]))
    colors = user_colors(st["breakdown"])

    running = [r for r in jb if r[2] == "RUNNING"]
    pending = [r for r in jb if r[2] == "PENDING"]
    my_bad = [r for r in hist if r[2].split()[0] in BAD_STATES]
    jh = hist_lab
    bad = [r for r in jh if r[2].split()[0] in BAD_STATES]
    ok = sum(1 for r in jh if r[2].startswith("COMPLETED"))
    scope = "all members" if who == "lab" else "me"
    reg = nd.get("Regular CPU", [0, 0, 0, 1, 0])
    gpu = nd.get("GPU nodes (H100)", [0, 0, 0, 1, 0])
    min_fs = min((float(r[3] or 0) for r in fs), default=0)
    alerts = [f"{q['label']} {name} {p:.0f}%" for q in quotas
              for name, p in (("space", pct(q)), ("files", pct(q, "files", "fquota"))) if p >= 95]

    space = card("Space quota", quota_chart(quotas, "used", "quota", size))
    files = card("File-count quota", quota_chart(quotas, "files", "fquota", count))
    trend = card(f"Jobs finished per day · {scope} · last {days} d", jobs_per_day(jh, days))
    cores = card("Cluster cores now", nodes_chart(nd) + legend([("busy", SERIES[0]), ("idle", GOOD), ("down/drained", OTHER)]))
    fair = card("Fairshare · higher = sooner start", fairshare_chart(fs))
    users = list(colors.items()) + [("other", OTHER)]
    proj_u = card("/project by user (TiB)", usage_chart(st["breakdown"], "project", sel_groups, colors, False) + legend(users)) if "project" in sel_fs else ""
    near_title = "/nearline by user (TiB)" + (f" · login-node copy of {st['asof']}" if st.get("asof") else "")
    near_u = card(near_title, usage_chart(st["breakdown"], "nearline", sel_groups, colors, True) + legend(users, True)) if "nearline" in sel_fs else ""
    by_user = {}
    for r in jh:
        st_ = r[2].split()[0]
        d = by_user.setdefault(r[9] if len(r) > 9 else c.USER, [0, 0])
        if st_ == "COMPLETED":
            d[0] += 1
        elif st_ in BAD_STATES:
            d[1] += 1
    xu = int(nice_step(max([sum(v) for v in by_user.values()] + [1])) * 4)
    member_chart = card(f"Jobs by member · last {days} d", bars(
        [(u, [(v[0], SERIES[0], f"{u}: {v[0]} completed", False, None), (v[1], CRIT, f"{u}: {v[1]} problems", False, None)],
          f'<tspan class="strong">{v[0]:,}</tspan> ok · {v[1]:,} problems')
         for u, v in sorted(by_user.items(), key=lambda kv: -sum(kv[1]))] or [("no jobs", [], "")],
        xu, list(range(0, xu + 1, int(nice_step(xu)))), lambda t: f"{t:,}", value_w=170)
        + legend([("completed", SERIES[0]), ("problems", CRIT)])) if who == "lab" else ""
    bad_chart = card(f"Problem jobs by member and state · {scope} · {days} d", problems_radial(bad, c.USER))
    bad_ts = card(f"Problem jobs per day by member · {scope} · {days} d", problems_over_time(bad, days, colors, c.USER))
    lab_by = {}
    for r in lab:
        d = lab_by.setdefault(r[1], [0, 0])
        d[0 if r[3] == "RUNNING" else 1] += 1
    xl = int(nice_step(max([sum(v) for v in lab_by.values()] + [1])) * 4)
    lab_card = card(f"Lab queue · {len(lab)} jobs on your accounts", bars(
        [(u, [(v[0], SERIES[0], f"{u}: {v[0]} running", False, None), (v[1], SERIES[1], f"{u}: {v[1]} pending", False, None)],
          f'<tspan class="strong">{v[0]}</tspan> run · {v[1]} pend')
         for u, v in sorted(lab_by.items(), key=lambda kv: -sum(kv[1]))] or [("no jobs", [], "")],
        xl, list(range(0, xl + 1, int(nice_step(xl)))), str, value_w=120)
        + legend([("running", SERIES[0]), ("pending", SERIES[1])]))
    tables = (f'<section class="card" style="grid-column:1/-1">'
              f'<details><summary>Queue ({len(jb)})</summary>{table(["JobID","Name","State","Elapsed","Limit","Nodes","CPUs","Mem","Account","Node / Reason"], jb)}</details>'
              f'<details><summary>Lab queue, all members ({len(lab)})</summary>{table(["JobID","User","Name","State","Elapsed","Limit","CPUs","Account","Node / Reason"], lab)}</details>'
              f'<details><summary>Problem jobs, {scope}, last {days} days ({len(bad)})</summary>{table(["JobID","Name","State","Elapsed","Limit","ReqMem","Exit","Account","End","User"], bad[::-1][:300])}</details></section>')

    fsys = st.get("filesystems", [])
    fsc = fs_card(fsys) if fsys else ""
    pend_c = pending_card(pend)
    eff_c, cpu_eff, mem_eff, idle_h = efficiency_card(eff, days)
    pend_p = pending_panel(lab, plab) + anim_panel()
    big = [r for r in resv if r[6] == "upcoming" or (r[3].isdigit() and int(r[3]) >= 50)]
    resv_c = (f'<section class="card" style="grid-column:1/-1"><details {"open" if big else ""}><summary>Maintenance and reservations '
              f'(upcoming or ≥ 50 nodes: {len(big)} · all active: {len(resv)})</summary>'
              f'{table(["Name", "Start", "End", "Nodes", "Cores", "Flags", "State"], big + [r for r in resv if r not in big])}</details></section>')
    starts = sorted(r[5] for r in pend if r[5] not in ("N/A", ""))
    next_start = starts[0][5:16].replace("T", " ") if starts else "—"
    shared = next((r for r in fsys if "/project" in r["label"]), None)

    if page == "storage":
        k = [quota_tile(q) for q in quotas] + ([kpi("◍", "Shared Lustre", "whole FS used · all users", f"{pct(shared):.0f}%",
                                                    "crit" if pct(shared) >= 95 else "", pct(shared))] if shared else [])
        body = space + files + fsc + proj_u + near_u
    elif page == "compute":
        my_cores = sum(int(r[6]) for r in running if r[6].isdigit())
        k = [kpi("▦", "Regular CPU", "idle cores", count(reg[1]), "good"),
             kpi("▦", "GPU nodes", "idle cores", count(gpu[1]), "good"),
             kpi("⚖", "Fairshare", "lowest account", f"{min_fs:.2f}"),
             kpi("▶", "My running", "cores in use", my_cores),
             kpi("◷", "My pending", f"jobs · next start {next_start}", len(pending))]
        body = cores + fair + pend_c + resv_c
    elif page == "jobs":
        lab_run = sum(1 for r in lab if r[3] == "RUNNING")
        k = [kpi("▶", "Running", f"jobs now · {scope}", lab_run if who == "lab" else len(running), "good"),
             kpi("◷", "Pending", f"jobs now · {scope}", len(lab) - lab_run if who == "lab" else len(pending)),
             kpi("✓", "Completed", f"last {days} d", ok),
             kpi("✗", "Problems", f"last {days} d", len(bad), "crit" if bad else ""),
             kpi("%", "Success rate", f"last {days} d", f"{100 * ok / max(ok + len(bad), 1):.0f}%"),
             kpi("⚙", "CPU efficiency", f"my jobs · {idle_h:,.0f} core-h idle", f"{cpu_eff:.0f}%" if cpu_eff is not None else "—",
                 "crit" if cpu_eff is not None and cpu_eff < 50 else ""),
             kpi("▣", "Memory efficiency", "median peak / requested", f"{mem_eff:.0f}%" if mem_eff is not None else "—",
                 "crit" if mem_eff is not None and mem_eff < 30 else "")]
        body = eff_c + trend + member_chart + bad_chart + bad_ts + lab_card + pend_p + pend_c + tables
    else:
        k = [quota_tile(q) for q in sorted(quotas, key=lambda q: -max(pct(q), pct(q, "files", "fquota")))[:3]] + [
             kpi("▶", "My jobs", "running · pending", f"{len(running)} · {len(pending)}", "good" if running else ""),
             kpi("✗", "My problem jobs", f"last {days} d", len(my_bad), "crit" if my_bad else "")]
        compute = card("Cluster cores now · fairshare", nodes_chart(nd) + legend([("busy", SERIES[0]), ("idle", GOOD), ("down/drained", OTHER)])
                       + '<div style="height:14px"></div>' + fairshare_chart(fs))
        body = space + files + fsc + proj_u + near_u + trend + compute + lab_card + pend_p + pend_c + tables
        if params.get("static"):
            body = (space + files + fsc + proj_u + near_u + eff_c + trend + member_chart
                    + bad_chart + compute + lab_card + bad_ts + pend_p + pend_c + resv_c + tables)

    title = dict((p, l) for p, _, l in PAGES)[page]
    alert_html = "".join(f'<span class="alert">⚠ {esc(a)}</span>' for a in alerts)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Research Computing Dashboard · {title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{--page:#0b1424;--side:#111d33;--surface:#13213a;--head:#1b3358;--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f97a8;
--grid:#22324f;--track:#1c2c48;--border:rgba(255,255,255,.10);--accent:{SERIES[0]}}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--page);color:var(--ink);display:flex;min-height:100vh}}
aside{{width:380px;flex-shrink:0;background:var(--side);padding:18px 16px;display:flex;flex-direction:column;gap:6px;position:sticky;top:0;height:100vh;overflow:auto}}
.brand{{font-size:40px;font-weight:700;margin-bottom:12px}} .brand small{{font-size:24px;color:var(--muted);font-weight:400;margin-left:6px}}
.nav{{display:flex;gap:10px;align-items:center;padding:9px 12px;border-radius:8px;color:var(--ink2);text-decoration:none;font-size:28px}}
.nav:hover{{background:var(--track)}} .nav.on{{background:var(--head);color:var(--ink);font-weight:600}}
.nav span{{width:36px;text-align:center}}
aside h4{{margin:22px 0 4px;font-size:30px}} .flab{{font-size:12px;color:var(--muted);margin:12px 0 6px}} aside .flab{{font-size:24px}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;background:var(--page);padding:8px;border-radius:8px}}
.chip{{font-size:25px;background:var(--track);border-radius:5px;padding:4px 8px;cursor:pointer;color:var(--ink2)}}
.chip:has(input:checked){{background:#1f4f6e;color:var(--ink)}} .chip input{{display:none}}
select{{background:var(--page);color:var(--ink);border:1px solid var(--border);border-radius:6px;padding:6px;width:100%;font-size:24px}}
.about{{margin-top:auto;padding-top:18px;border-top:1px solid var(--border)}}
.logo{{position:absolute;left:18px;top:0;bottom:0;height:100%;width:auto;border-radius:0;z-index:2}}
.about p{{font-size:17px;color:var(--ink2);line-height:1.5;margin:0 0 12px}}
.foot{{font-size:22px;color:var(--muted);line-height:1.5}}
main{{flex:1;padding:22px 28px;min-width:0}}
.top{{display:flex;align-items:center;justify-content:space-between;border:1px solid var(--border);border-radius:8px;padding:12px 18px;margin-bottom:16px}}
.top{{position:relative;min-height:110px}} .top h1{{flex:1;text-align:center;font-size:44px;line-height:1.15;margin:0;letter-spacing:.3px}} .top h1 .pg{{display:block;font-size:18px;font-weight:500;color:var(--muted);margin-top:6px;letter-spacing:0}} .top>div:last-child{{position:absolute;right:18px;top:10px;z-index:2}}
.top.hero-on{{position:relative;overflow:hidden;min-height:140px;background:linear-gradient(90deg,var(--page) 0,var(--page) 35%,#0d1d36 100%)}}
.top .hero{{position:absolute;right:0;top:50%;transform:translateY(-50%);height:150%;width:auto;opacity:.8;pointer-events:none;
 -webkit-mask-image:radial-gradient(ellipse 55% 50% at 60% 50%,#000 30%,transparent 72%);mask-image:radial-gradient(ellipse 55% 50% at 60% 50%,#000 30%,transparent 72%)}}
.top.hero-on h1,.top.hero-on>div{{position:relative;z-index:1}}
@media (max-width:820px){{.top .hero{{opacity:.35}}}}
.alert{{display:inline-block;background:{CRIT};color:#fff;border-radius:12px;padding:3px 10px;margin-left:6px;font-size:12px;font-weight:600}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(180px,100%),1fr));gap:16px;margin-bottom:18px}}
.kpi-h{{background:var(--head);border-radius:6px;padding:10px 14px;font-size:14px;margin-bottom:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.kpi-b{{border:1px solid var(--border);border-radius:6px;padding:10px 14px;background:var(--surface)}}
.kpi-b span{{display:block;font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.kpi-b b{{display:block;font-size:30px;font-weight:600;margin-top:4px;white-space:nowrap}}
.kpi-b.crit b{{color:#e66767}} .muted{{color:var(--muted);font-size:13px}}
.meter{{position:relative;height:6px;background:var(--track);border-radius:3px;margin-top:8px}}
.meter div{{height:100%;border-radius:3px}} .meter i{{position:absolute;top:-2px;width:1px;height:10px;background:{CRIT}}} .kpi-b.good b{{color:{GOOD}}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(480px,100%),1fr));gap:16px}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px}}
.card h3{{margin:0 0 10px;font-size:15px}}
svg text{{font-size:12px;fill:var(--ink2)}} svg .lab{{fill:var(--ink)}} svg .strong{{font-weight:600;fill:var(--ink)}}
svg .tick{{font-size:11px;fill:var(--muted);font-variant-numeric:tabular-nums}} svg .sub{{font-size:10.5px}}
svg .grid{{stroke:var(--grid)}} svg .axis{{stroke:var(--muted)}} svg .ref{{stroke:{CRIT};stroke-width:1.2;stroke-dasharray:3 3}}
svg .track{{fill:var(--track)}} svg rect:hover{{opacity:.85}} svg .dot{{stroke:var(--surface);stroke-width:2}}
svg .hover{{fill:transparent}} svg .hover:hover{{fill:rgba(255,255,255,.05)}}
.legend{{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--ink2);margin-top:6px}}
.legend i{{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;vertical-align:-1px}}
.legend .hatch{{background:repeating-linear-gradient(45deg,var(--muted) 0 2px,transparent 2px 5px);border:1px solid var(--muted)}}
.boards{{display:flex;flex-direction:column;gap:1px}}
.bd{{background:none;border:0;color:var(--ink2);text-align:left;padding:3px 8px;border-radius:5px;font-size:24px;cursor:pointer;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.bd:hover{{background:var(--track)}} .bd.sel{{background:var(--head);color:var(--ink);font-weight:600}} .bd.all{{color:var(--ink)}}
.grid>section{{position:relative;cursor:zoom-in}} .grid>section table,.grid>section summary{{cursor:auto}} .grid>section summary{{cursor:pointer}} .card h3{{padding-right:34px}} details>summary{{padding-right:34px}}
.zoom{{position:absolute;top:10px;right:12px;width:26px;height:24px;border:0;border-radius:5px;background:var(--track);color:var(--ink2);
 cursor:pointer;font-size:14px;line-height:24px;padding:0}} .zoom:hover{{background:var(--head);color:var(--ink)}}
#ov{{position:fixed;inset:0;z-index:50;background:rgba(5,10,20,.65);display:flex;align-items:center;justify-content:center;cursor:zoom-out}}
#ov>section{{width:min(max(50vw,640px),94vw);max-height:86vh;overflow:auto;box-shadow:0 12px 48px rgba(0,0,0,.55);cursor:zoom-out}}
#ov>section svg{{max-width:none !important}} #ov .zoom{{background:var(--head);color:var(--ink)}}
.anim-card{{position:relative;overflow:hidden;padding:0;min-height:220px}}
.anim-card .anim{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:.28;filter:saturate(.85);pointer-events:none;
 -webkit-mask-image:radial-gradient(ellipse 80% 75% at 50% 50%,#000 25%,transparent 85%);mask-image:radial-gradient(ellipse 80% 75% at 50% 50%,#000 25%,transparent 85%)}}
.prow{{display:grid;grid-template-columns:minmax(0,1.3fr) auto minmax(0,1.6fr);gap:10px;align-items:baseline;padding:6px 2px;border-bottom:1px solid var(--grid);font-size:13px}}
.prow .pn{{color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}} .prow .pt{{color:{GOOD};font-weight:600;font-variant-numeric:tabular-nums}}
.prow .pr{{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
details{{margin:4px 0}} summary{{cursor:pointer;font-size:14px;color:var(--ink2);padding:6px 0}}
table{{border-collapse:collapse;font-size:12px;width:100%;margin:6px 0 10px;font-variant-numeric:tabular-nums}}
td,th{{border-bottom:1px solid var(--grid);padding:4px 6px;text-align:left}}
details{{overflow-x:auto}}
@media (max-width:820px){{
 body{{display:block}}
 aside{{position:static;width:auto;height:auto;padding:14px}}
 .logo{{position:static;transform:none;height:56px}} .about,.foot{{display:none}}
 main{{padding:12px}} .top{{flex-direction:column;align-items:center;gap:8px}} .top h1{{font-size:28px}} .top>div:last-child{{position:static}}
 .kpis{{grid-template-columns:repeat(2,1fr);gap:10px}} .kpi-b b{{font-size:24px}}
 .card{{padding:12px}}
 nav{{display:flex;flex-wrap:wrap;gap:4px}}
 .boards{{flex-direction:row;flex-wrap:wrap}} .bd{{background:var(--track);font-size:12px;padding:4px 8px}}
}}
@media (min-width:600px) and (max-width:820px){{
 .kpis{{grid-template-columns:repeat(3,1fr)}}
 form{{display:grid;grid-template-columns:1fr 1fr;column-gap:14px}} form h4{{grid-column:1/-1}}
}} th{{color:var(--muted);font-weight:500}}
</style></head><body>
<svg width="0" height="0" style="position:absolute"><defs>{hatch_defs(list(colors.values()) + [OTHER])}</defs></svg>
{static_sidebar(days) if params.get("static") else sidebar(page, groups, sel_groups, sel_fs, days, who)}
<main>
<div class="top{' hero-on' if (page == 'compute' or params.get('static')) and HERO.is_file() else ''}">{hero() if (page == 'compute' or params.get('static')) else ''}{logo()}<h1>Research Computing Dashboard</h1><div>{alert_html}</div></div>
<div class="kpis">{"".join(k)}</div>
<div class="grid">{body}</div>
</main>
{SCRIPT}
</body></html>"""
