import html
import socket
import time
from concurrent.futures import ThreadPoolExecutor

from . import collectors as c

CACHE_TTL = 60
STORAGE_TTL = 600
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
OTHER = "#898781"
CRIT, WARN, GOOD = "#d03b3b", "#fab219", "#0ca30c"
BAD_STATES = ("FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "CANCELLED")
_slow = {"t": 0, "data": None}


def esc(s):
    return html.escape(str(s))


def size(b):
    return f"{b / 2**40:.1f} TiB" if b >= 2**40 else f"{b / 2**30:.0f} GiB"


def count(n):
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.0f}K" if n >= 1e4 else f"{n:,.0f}"


def storage():
    if _slow["data"] is None or time.time() - _slow["t"] > STORAGE_TTL:
        gs = c.groups()
        with ThreadPoolExecutor(8) as ex:
            gq = ex.submit(c.group_quotas)
            uq = ex.submit(c.user_quotas)
            bd = {(fs, g): ex.submit(c.user_breakdown, fs, g) for fs in ("project", "nearline") for g in gs}
            _slow["data"] = {"quotas": gq.result() + uq.result(),
                             "breakdown": {k: f.result() for k, f in bd.items()}}
        _slow["t"] = time.time()
    return _slow["data"]


def svg_bars(rows, xmax, ticks, tick_fmt, ref=None, label_w=150, value_w=190, row_h=34, bar_h=20,
             sublabels=False, width=620):
    """rows: [(label, [(value, fill, tooltip, hatched, sublabel)], right_text)]"""
    plot_w = width - label_w - value_w
    gap = 18 if sublabels else 0
    h = len(rows) * (row_h + gap) + 28
    sx = lambda v: label_w + plot_w * min(v, xmax) / xmax
    out = [f'<svg viewBox="0 0 {width} {h}" width="100%" role="img">']
    for t in ticks:
        x = sx(t)
        out.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{h - 22}" class="grid"/>'
                   f'<text x="{x:.1f}" y="{h - 6}" class="tick" text-anchor="middle">{tick_fmt(t)}</text>')
    for i, (label, segs, right) in enumerate(rows):
        y = i * (row_h + gap) + (row_h - bar_h) / 2
        out.append(f'<text x="{label_w - 10}" y="{y + bar_h / 2 + 4:.1f}" class="lab" text-anchor="end">{esc(label)}</text>')
        out.append(f'<rect x="{label_w}" y="{y:.1f}" width="{plot_w}" height="{bar_h}" rx="4" class="track"/>')
        x0 = 0.0
        for v, fill, tip, hatched, sub in segs:
            x1, x2 = sx(x0), sx(x0 + v)
            w = max(x2 - x1 - 2, 1.5)
            pat = f'url(#h{fill[1:]})' if hatched else fill
            out.append(f'<rect x="{x1:.1f}" y="{y:.1f}" width="{w:.1f}" height="{bar_h}" rx="4" fill="{pat}">'
                       f'<title>{esc(tip)}</title></rect>')
            if sublabels and sub and w > 38:
                out.append(f'<text x="{x1 + w / 2:.1f}" y="{y + bar_h + 13:.1f}" class="sub" text-anchor="middle">{esc(sub)}</text>')
            x0 += v
        out.append(f'<text x="{label_w + plot_w + 10}" y="{y + bar_h / 2 + 4:.1f}" class="val">{right}</text>')
    if ref is not None:
        x = sx(ref)
        out.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{h - 22}" class="ref"/>')
    out.append("</svg>")
    return "".join(out)


def status_fill(pct):
    return CRIT if pct >= 95 else WARN if pct >= 85 else SERIES[0]


def quota_chart(quotas, key, qkey, fmt):
    rows = []
    for q in quotas:
        pct = 100 * q[key] / max(q[qkey], 1)
        icon = "⚠ " if pct >= 85 else ""
        tip = f"{q['label']}: {fmt(q[key])} of {fmt(q[qkey])} ({pct:.1f}%)"
        rows.append((q["label"], [(pct, status_fill(pct), tip, False, None)],
                     f'<tspan class="strong">{icon}{pct:.0f}%</tspan>  {fmt(q[key])} / {fmt(q[qkey])}'))
    return svg_bars(rows, 100, [0, 25, 50, 75, 100], lambda t: f"{t}%", ref=95)


def user_colors(breakdown):
    totals = {}
    for users in breakdown.values():
        for u, v in users.items():
            totals[u] = totals.get(u, 0) + v["disk"] + v["tape"]
    order = [c.USER] + sorted((u for u in totals if u != c.USER), key=lambda u: -totals[u])
    return {u: SERIES[i] for i, u in enumerate(order[:len(SERIES)])}


def usage_chart(breakdown, fs, colors, tape):
    keys = sorted(k for k in breakdown if k[0] == fs)
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
    return svg_bars(rows, xmax, ticks, lambda t: f"{t / 2**40:.0f}", sublabels=True, row_h=30, value_w=90)


def legend(colors, tape=False):
    items = [f'<span><i style="background:{col}"></i>{esc(u)}</span>' for u, col in colors.items()]
    items.append(f'<span><i style="background:{OTHER}"></i>other</span>')
    if tape:
        items.append('<span><i class="hatch"></i>tape-only</span>')
    return f'<div class="legend">{"".join(items)}</div>'


def nodes_chart(nodes):
    rows = []
    for kind in ("Regular CPU", "Large-mem CPU", "GPU nodes (H100)"):
        if kind not in nodes:
            continue
        a, i, o, t, n = nodes[kind]
        pa, pi, po = (100 * x / max(t, 1) for x in (a, i, o))
        rows.append((kind, [(pa, SERIES[0], f"{a:,} cores busy", False, None),
                            (pi, GOOD, f"{i:,} cores idle", False, None),
                            (po, OTHER, f"{o:,} cores down/drained", False, None)],
                     f'<tspan class="strong">{100 * i / max(t, 1):.0f}% idle</tspan>  {count(i)} / {count(t)} cores'))
    return svg_bars(rows, 100, [0, 50, 100], lambda t: f"{t}%")


def fairshare_chart(fs):
    rows = []
    for acct, norm, eff, fair, level in fs:
        f = float(fair or 0)
        rows.append((acct, [(f, SERIES[0], f"FairShare {f:.3f} · LevelFS {level} · usage {float(eff or 0):.3f} of share {float(norm or 0):.3f}", False, None)],
                     f'<tspan class="strong">{f:.2f}</tspan>'))
    return svg_bars(rows, 1, [0, 0.5, 1], lambda t: f"{t:g}", value_w=80)


def tile(value, label, cls=""):
    return f'<div class="tile {cls}"><b>{value}</b><span>{esc(label)}</span></div>'


def table(headers, rows):
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(x)}</td>" for x in r) + "</tr>" for r in rows)
    return f"<table><tr>{h}</tr>{body or '<tr><td colspan=99><i>none</i></td></tr>'}</table>"


def build(days):
    with ThreadPoolExecutor(5) as ex:
        st, fs, jb, hist, nd = (f.result() for f in [
            ex.submit(storage), ex.submit(c.fairshare), ex.submit(c.jobs),
            ex.submit(c.recent, days), ex.submit(c.nodes)])

    fs_order = {"/project": 0, "/nearline": 1, "/home": 2, "/scratch": 3}
    quotas = sorted(st["quotas"], key=lambda q: (fs_order.get(q["label"].split()[1], 9), q["label"]))
    alerts = []
    for q in quotas:
        for k, qk, name in (("used", "quota", "space"), ("files", "fquota", "files")):
            pct = 100 * q[k] / max(q[qk], 1)
            if pct >= 95:
                alerts.append(f"{q['label']} {name} {pct:.0f}%")
    colors = user_colors(st["breakdown"])
    hatch = "".join(
        f'<pattern id="h{col[1:]}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        f'<rect width="6" height="6" fill="{col}" opacity="0.45"/><line x1="0" y1="0" x2="0" y2="6" stroke="{col}" stroke-width="2.5"/></pattern>'
        for col in list(colors.values()) + [OTHER])

    running = sum(1 for r in jb if r[2] == "RUNNING")
    pending = sum(1 for r in jb if r[2] == "PENDING")
    bad = [r for r in hist if r[2].split()[0] in BAD_STATES]
    ok = sum(1 for r in hist if r[2].startswith("COMPLETED"))
    bad_by = {}
    for r in bad:
        s = r[2].split()[0]
        bad_by[s] = bad_by.get(s, 0) + 1
    bad_tiles = "".join(tile(v, k.replace("_", " ").lower(), "crit" if k != "CANCELLED" else "") for k, v in sorted(bad_by.items()))

    alert_html = "".join(f'<span class="alert">⚠ {esc(a)}</span>' for a in alerts)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Fir · {c.USER}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--track:#f0efec;--border:rgba(11,11,11,.10)}}
@media (prefers-color-scheme:dark){{:root{{--surface:#1a1a19;--page:#0d0d0d;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--track:#262624;--border:rgba(255,255,255,.10)}}}}
body{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;padding:20px 28px;background:var(--page);color:var(--ink)}}
h1{{font-size:20px;margin:0 0 2px}} .ts{{color:var(--muted);font-size:12px;margin-bottom:12px}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:16px;margin-bottom:16px}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px}}
.card h2{{font-size:14px;margin:0 0 8px}}
svg text{{font-size:12px;fill:var(--ink2)}} svg .lab{{fill:var(--ink)}} svg .strong{{font-weight:600;fill:var(--ink)}}
svg .tick{{font-size:11px;fill:var(--muted);font-variant-numeric:tabular-nums}} svg .sub{{font-size:10.5px;fill:var(--ink2)}}
svg .grid{{stroke:var(--grid);stroke-width:1}} svg .ref{{stroke:{CRIT};stroke-width:1.2;stroke-dasharray:3 3}}
svg .track{{fill:var(--track)}} svg rect:hover{{opacity:.8}}
.legend{{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--ink2);margin-top:4px}}
.legend i{{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;vertical-align:-1px}}
.legend .hatch{{background:repeating-linear-gradient(45deg,var(--muted) 0 2px,transparent 2px 5px);border:1px solid var(--muted)}}
.alert{{display:inline-block;background:{CRIT};color:#fff;border-radius:12px;padding:3px 10px;margin:0 6px 6px 0;font-size:12px;font-weight:600}}
.tiles{{display:flex;flex-wrap:wrap;gap:10px}}
.tile{{min-width:92px;padding:8px 12px;border-radius:8px;background:var(--track)}}
.tile b{{display:block;font-size:24px}} .tile span{{font-size:12px;color:var(--ink2)}}
.tile.crit b{{color:{CRIT}}} .tile.good b{{color:{GOOD}}}
details{{margin-top:10px}} summary{{cursor:pointer;font-size:13px;color:var(--ink2)}}
table{{border-collapse:collapse;font-size:12px;width:100%;margin-top:6px;font-variant-numeric:tabular-nums}}
td,th{{border-bottom:1px solid var(--grid);padding:3px 6px;text-align:left}} th{{color:var(--muted);font-weight:500}}
</style></head><body>
<svg width="0" height="0" style="position:absolute"><defs>{hatch}</defs></svg>
<h1>Fir · {c.USER}</h1>
<div class="ts">{time.strftime('%Y-%m-%d %H:%M')} · {socket.gethostname().split('.')[0]} · storage refreshed every {STORAGE_TTL // 60} min · hover bars for detail</div>
<div>{alert_html}</div>

<div class="grid2">
 <div class="card"><h2>Space quota</h2>{quota_chart(quotas, "used", "quota", size)}</div>
 <div class="card"><h2>File-count quota</h2>{quota_chart(quotas, "files", "fquota", count)}</div>
 <div class="card"><h2>/project by user (TiB)</h2>{usage_chart(st["breakdown"], "project", colors, False)}{legend(colors)}</div>
 <div class="card"><h2>/nearline by user (TiB)</h2>{usage_chart(st["breakdown"], "nearline", colors, True)}{legend(colors, True)}</div>
 <div class="card"><h2>Cluster cores now</h2>{nodes_chart(nd)}
   <div class="legend"><span><i style="background:{SERIES[0]}"></i>busy</span><span><i style="background:{GOOD}"></i>idle</span><span><i style="background:{OTHER}"></i>down/drained</span></div></div>
 <div class="card"><h2>Fairshare (higher = sooner start)</h2>{fairshare_chart(fs)}</div>
</div>

<div class="card"><h2>My jobs</h2>
 <div class="tiles">{tile(running, "running", "good")}{tile(pending, "pending")}{tile(ok, f"completed {days}d")}{bad_tiles or tile(0, f"problems {days}d")}</div>
 <details><summary>Queue ({len(jb)})</summary>
  {table(["JobID","Name","State","Elapsed","Limit","Nodes","CPUs","Mem","Account","Node / Reason"], jb)}</details>
 <details><summary>Problem jobs, last {days} days ({len(bad)})</summary>
  {table(["JobID","Name","State","Elapsed","Limit","ReqMem","Exit","Account","End"], bad[::-1][:300])}</details>
</div>
</body></html>"""
