import base64
import datetime as dt
from pathlib import Path
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

from . import collectors as c
from .charts import (CRIT, GOOD, OTHER, SERIES, bars, columns, count, esc, hatch_defs, legend, nice_step, size,
                     status_fill)

CACHE_TTL = 60
STORAGE_TTL = 600
BAD_STATES = ("FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "CANCELLED")
PAGES = [("overview", "◉", "Overview"), ("storage", "▤", "Storage"), ("compute", "▦", "Compute"), ("jobs", "☰", "Jobs")]
FILESYSTEMS = ["project", "nearline", "home", "scratch"]
_slow = {"t": 0, "data": None}
_lock = threading.Lock()


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
            _slow["data"] = {"groups": gs, "quotas": gq.result() + uq.result(),
                             "breakdown": {k: f.result() for k, f in bd.items()}}
        _slow["t"] = time.time()
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
    return columns(labels, [("completed", SERIES[0], ok), ("problems", CRIT, bad)]) + legend([("completed", SERIES[0]), ("problems", CRIT)])


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
ABOUT = ("A quick view of your Alliance Fir resources: storage quotas and file counts, who uses the "
         "group space, cluster load, fairshare, and the jobs of you and your lab. "
         "Read-only: it only reads Fir's own reports under your account.")


def about():
    img = ""
    if LOGO.is_file():
        img = f'<img src="data:image/png;base64,{base64.b64encode(LOGO.read_bytes()).decode()}" alt="logo">'
    return f'<div class="about">{img}<p>{esc(ABOUT)}</p></div>'


def sidebar(page, groups, sel_groups, sel_fs, days, who="me"):
    nav = "".join(
        f'<a class="nav {"on" if p == page else ""}" href="?{urlencode({"p": p, "g": ",".join(sel_groups), "fs": ",".join(sel_fs), "days": days, "who": who})}">'
        f'<span>{icon}</span>{label}</a>' for p, icon, label in PAGES)
    chips = lambda name, opts, sel: "".join(
        f'<label class="chip"><input type="checkbox" name="{name}" value="{o}" {"checked" if o in sel else ""} onchange="go()">{esc(o)}</label>'
        for o in opts)
    dsel = "".join(f'<option {"selected" if d == days else ""}>{d}</option>' for d in (7, 14, 30, 90))
    return f"""<aside>
<div class="brand">▲ Fir <small>{esc(c.USER)}</small></div>
<nav>{nav}</nav>
<form id="f" method="get">
 <input type="hidden" name="p" value="{page}"><input type="hidden" name="g"><input type="hidden" name="fs">
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
<div class="brand">▲ Fir <small>{esc(c.USER)}</small></div>
<nav><a class="nav on"><span>◉</span>Snapshot</a></nav>
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
</script>"""


def build(params):
    page = params.get("p", "overview")
    page = page if page in {p for p, _, _ in PAGES} else "overview"
    days = int(params.get("days", "7")) if params.get("days", "7").isdigit() else 7
    who = "lab" if params.get("static") else ("lab" if params.get("who") == "lab" else "me")

    fs = c.fairshare()
    accounts = [r[0] for r in fs]
    with ThreadPoolExecutor(7) as ex:
        futs = [ex.submit(storage), ex.submit(c.jobs), ex.submit(c.recent, days), ex.submit(c.nodes),
                ex.submit(c.group_jobs, accounts), ex.submit(c.recent, days, accounts) if who == "lab" else None]
        st, jb, hist, nd, lab, hist_lab = (f.result() if f else None for f in futs)
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
    near_u = card("/nearline by user (TiB)", usage_chart(st["breakdown"], "nearline", sel_groups, colors, True) + legend(users, True)) if "nearline" in sel_fs else ""
    bad_by = {}
    for r in bad:
        bad_by[r[2].split()[0]] = bad_by.get(r[2].split()[0], 0) + 1
    xb = int(nice_step(max(bad_by.values(), default=1)) * 4)
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
    bad_chart = card(f"Problem jobs by state · {scope} · {days} d", bars(
        [(k.replace("_", " ").lower(), [(v, CRIT if k != "CANCELLED" else OTHER, f"{k}: {v}", False, None)], f'<tspan class="strong">{v}</tspan>')
         for k, v in sorted(bad_by.items(), key=lambda kv: -kv[1])] or [("none", [], "0")],
        xb, [x for x in range(0, xb + 1, int(nice_step(xb)))], str, value_w=60))
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

    if page == "storage":
        k = [quota_tile(q) for q in quotas]
        body = space + files + proj_u + near_u
    elif page == "compute":
        my_cores = sum(int(r[6]) for r in running if r[6].isdigit())
        k = [kpi("▦", "Regular CPU", "idle cores", count(reg[1]), "good"),
             kpi("▦", "GPU nodes", "idle cores", count(gpu[1]), "good"),
             kpi("⚖", "Fairshare", "lowest account", f"{min_fs:.2f}"),
             kpi("▶", "My running", "cores in use", my_cores),
             kpi("◷", "My pending", "jobs", len(pending))]
        body = cores + fair
    elif page == "jobs":
        lab_run = sum(1 for r in lab if r[3] == "RUNNING")
        k = [kpi("▶", "Running", f"jobs now · {scope}", lab_run if who == "lab" else len(running), "good"),
             kpi("◷", "Pending", f"jobs now · {scope}", len(lab) - lab_run if who == "lab" else len(pending)),
             kpi("✓", "Completed", f"last {days} d", ok),
             kpi("✗", "Problems", f"last {days} d", len(bad), "crit" if bad else ""),
             kpi("%", "Success rate", f"last {days} d", f"{100 * ok / max(ok + len(bad), 1):.0f}%")]
        body = trend + member_chart + bad_chart + lab_card + tables
    else:
        k = [quota_tile(q) for q in sorted(quotas, key=lambda q: -max(pct(q), pct(q, "files", "fquota")))[:3]] + [
             kpi("▶", "My jobs", "running · pending", f"{len(running)} · {len(pending)}", "good" if running else ""),
             kpi("✗", "My problem jobs", f"last {days} d", len(my_bad), "crit" if my_bad else "")]
        compute = card("Cluster cores now · fairshare", nodes_chart(nd) + legend([("busy", SERIES[0]), ("idle", GOOD), ("down/drained", OTHER)])
                       + '<div style="height:14px"></div>' + fairshare_chart(fs))
        body = space + files + proj_u + near_u + trend + compute + lab_card + tables
        if params.get("static"):
            body = space + files + proj_u + near_u + trend + member_chart + bad_chart + compute + lab_card + tables

    title = dict((p, l) for p, _, l in PAGES)[page]
    alert_html = "".join(f'<span class="alert">⚠ {esc(a)}</span>' for a in alerts)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Fir · {title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{--page:#0b1424;--side:#111d33;--surface:#13213a;--head:#1b3358;--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f97a8;
--grid:#22324f;--track:#1c2c48;--border:rgba(255,255,255,.10);--accent:{SERIES[0]}}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--page);color:var(--ink);display:flex;min-height:100vh}}
aside{{width:250px;flex-shrink:0;background:var(--side);padding:18px 16px;display:flex;flex-direction:column;gap:6px;position:sticky;top:0;height:100vh;overflow:auto}}
.brand{{font-size:20px;font-weight:700;margin-bottom:12px}} .brand small{{font-size:12px;color:var(--muted);font-weight:400;margin-left:6px}}
.nav{{display:flex;gap:10px;align-items:center;padding:9px 12px;border-radius:8px;color:var(--ink2);text-decoration:none;font-size:14px}}
.nav:hover{{background:var(--track)}} .nav.on{{background:var(--head);color:var(--ink);font-weight:600}}
.nav span{{width:18px;text-align:center}}
aside h4{{margin:22px 0 4px;font-size:15px}} .flab{{font-size:12px;color:var(--muted);margin:12px 0 6px}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;background:var(--page);padding:8px;border-radius:8px}}
.chip{{font-size:12.5px;background:var(--track);border-radius:5px;padding:4px 8px;cursor:pointer;color:var(--ink2)}}
.chip:has(input:checked){{background:#1f4f6e;color:var(--ink)}} .chip input{{display:none}}
select{{background:var(--page);color:var(--ink);border:1px solid var(--border);border-radius:6px;padding:6px;width:100%}}
.about{{margin-top:auto;padding-top:18px;border-top:1px solid var(--border)}}
.about img{{display:block;max-width:100%;max-height:90px;margin:0 auto 10px;object-fit:contain}}
.about p{{font-size:12px;color:var(--ink2);line-height:1.5;margin:0 0 12px}}
.foot{{font-size:11px;color:var(--muted);line-height:1.5}}
main{{flex:1;padding:22px 28px;min-width:0}}
.top{{display:flex;align-items:center;justify-content:space-between;border:1px solid var(--border);border-radius:8px;padding:12px 18px;margin-bottom:16px}}
.top h1{{font-size:20px;margin:0}}
.alert{{display:inline-block;background:{CRIT};color:#fff;border-radius:12px;padding:3px 10px;margin-left:6px;font-size:12px;font-weight:600}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:18px}}
.kpi-h{{background:var(--head);border-radius:6px;padding:10px 14px;font-size:14px;margin-bottom:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.kpi-b{{border:1px solid var(--border);border-radius:6px;padding:10px 14px;background:var(--surface)}}
.kpi-b span{{display:block;font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.kpi-b b{{display:block;font-size:30px;font-weight:600;margin-top:4px;white-space:nowrap}}
.kpi-b.crit b{{color:#e66767}}
.meter{{position:relative;height:6px;background:var(--track);border-radius:3px;margin-top:8px}}
.meter div{{height:100%;border-radius:3px}} .meter i{{position:absolute;top:-2px;width:1px;height:10px;background:{CRIT}}} .kpi-b.good b{{color:{GOOD}}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(480px,1fr));gap:16px}}
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
details{{margin:4px 0}} summary{{cursor:pointer;font-size:14px;color:var(--ink2);padding:6px 0}}
table{{border-collapse:collapse;font-size:12px;width:100%;margin:6px 0 10px;font-variant-numeric:tabular-nums}}
td,th{{border-bottom:1px solid var(--grid);padding:4px 6px;text-align:left}} th{{color:var(--muted);font-weight:500}}
</style></head><body>
<svg width="0" height="0" style="position:absolute"><defs>{hatch_defs(list(colors.values()) + [OTHER])}</defs></svg>
{static_sidebar(days) if params.get("static") else sidebar(page, groups, sel_groups, sel_fs, days, who)}
<main>
<div class="top"><h1>{title}</h1><div>{alert_html}</div></div>
<div class="kpis">{"".join(k)}</div>
<div class="grid">{body}</div>
</main>
{SCRIPT}
</body></html>"""
