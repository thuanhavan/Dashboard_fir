"""Page shell: top bar with the Lab / Me tabs and the days select, sidebar, CSS. Content comes from lab.py / me.py."""
import base64
import socket
import time
from pathlib import Path

from . import collectors as c
from . import lab, me
from .cache import CACHE_TTL, STORAGE_TTL
from .charts import CRIT, GOOD, OTHER, SERIES, esc, hatch_defs

PAGES = [("lab", "Lab"), ("me", "Me")]
DAYS = (7, 30, 90)
DEFAULT_DAYS = {"lab": 30, "me": 7}
LOGO = Path(__file__).resolve().parent.parent / "docs" / "logo.png"
ABOUT = ("Lab: storage, compute use and jobs of every group you belong to. Me: your own quotas and jobs. "
         "Read-only: it only reads the cluster's own reports under your account.")


def build(params):
    page = params.get("page") if params.get("page") in DEFAULT_DAYS else "lab"
    days = int(params["days"]) if str(params.get("days", "")).isdigit() and int(params["days"]) in DAYS else DEFAULT_DAYS[page]
    kpis, grid, alerts, colors = (me.build_me if page == "me" else lab.build_lab)(days)
    return shell(page, days, kpis, grid, alerts, colors, bool(params.get("static")))


def logo():
    if not LOGO.is_file():
        return ""
    return f'<img class="logo" src="data:image/png;base64,{base64.b64encode(LOGO.read_bytes()).decode()}" alt="logo">'


def tabs(page, days):
    return "".join(f'<a class="tab{" on" if p == page else ""}" href="?page={p}&amp;days={days if p == page else DEFAULT_DAYS[p]}">{label}</a>'
                   for p, label in PAGES)


def controls(page, days, static):
    if static:
        return f'<span class="muted">last {days} d · static snapshot</span>'
    opts = "".join(f'<option value="{d}"{" selected" if d == days else ""}>last {d} d</option>' for d in DAYS)
    return (f'<form method="get" class="days"><input type="hidden" name="page" value="{page}">'
            f'<select name="days" onchange="this.form.submit()">{opts}</select></form>')


def shell(page, days, kpis, grid, alerts, colors, static):
    title = dict(PAGES)[page]
    alert_html = "".join(f'<span class="alert">⚠ {esc(a)}</span>' for a in alerts)
    foot = ("static snapshot: rerun to refresh" if static else f"storage every {STORAGE_TTL // 60} min · jobs every {CACHE_TTL} s")
    fills = list(dict.fromkeys(list(colors.values()) + [OTHER]))   # one <pattern> per distinct colour
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Research Computing Dashboard · {title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{CSS}</style></head><body>
<svg width="0" height="0" style="position:absolute"><defs>{hatch_defs(fills)}</defs></svg>
<aside>
<div class="brand">▲ DRAC <small>{esc(c.USER)}</small></div>
<nav class="tabs">{tabs(page, days)}</nav>
<div class="about"><p>{esc(ABOUT)}</p></div>
<div class="foot">{time.strftime('%Y-%m-%d %H:%M')} · {socket.gethostname().split('.')[0]}<br>{foot}</div>
</aside>
<main>
<div class="top">{logo()}<h1>Research Computing Dashboard<span class="pg">{title} · last {days} d</span></h1>
<div class="ctl"><nav class="tabs">{tabs(page, days)}</nav>{controls(page, days, static)}</div></div>
<div class="alerts">{alert_html}</div>
<div class="kpis">{kpis}</div>
<div class="grid">{grid}</div>
</main>
</body></html>"""


CSS = f"""
:root{{--page:#0b1424;--side:#111d33;--surface:#13213a;--head:#1b3358;--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f97a8;
--grid:#22324f;--track:#1c2c48;--border:rgba(255,255,255,.10);--accent:{SERIES[0]}}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--page);color:var(--ink);display:flex;min-height:100vh}}
aside{{width:260px;flex-shrink:0;background:var(--side);padding:18px 16px;display:flex;flex-direction:column;gap:10px;position:sticky;top:0;height:100vh;overflow:auto}}
.brand{{font-size:28px;font-weight:700}} .brand small{{font-size:16px;color:var(--muted);font-weight:400;margin-left:6px}}
.tabs{{display:flex;gap:6px}} aside .tabs{{flex-direction:column}}
.tab{{display:block;padding:9px 14px;border-radius:8px;color:var(--ink2);text-decoration:none;font-size:16px;font-weight:600}}
.tab:hover{{background:var(--track)}} .tab.on{{background:var(--head);color:var(--ink)}}
.about{{margin-top:auto;padding-top:18px;border-top:1px solid var(--border)}} .about p{{font-size:13px;color:var(--ink2);line-height:1.5;margin:0}}
.foot{{font-size:12px;color:var(--muted);line-height:1.5}}
main{{flex:1;padding:22px 28px;min-width:0}}
.top{{display:flex;align-items:center;justify-content:space-between;gap:16px;border:1px solid var(--border);border-radius:8px;padding:12px 18px;margin-bottom:12px}}
.logo{{flex-shrink:0;height:96px;width:auto;margin:-12px 0 -12px -18px;border-radius:7px 0 0 7px}}
.top h1{{flex:1;font-size:30px;line-height:1.15;margin:0}} .top h1 .pg{{display:block;font-size:14px;font-weight:500;color:var(--muted);margin-top:4px}}
.ctl{{display:flex;align-items:center;gap:12px}} .top .tabs{{display:none}}
select{{background:var(--page);color:var(--ink);border:1px solid var(--border);border-radius:6px;padding:6px 8px;font-size:14px}}
.alerts{{min-height:0;margin-bottom:8px}}
.alert{{display:inline-block;background:{CRIT};color:#fff;border-radius:12px;padding:3px 10px;margin:0 6px 6px 0;font-size:12px;font-weight:600}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(180px,100%),1fr));gap:16px;margin-bottom:18px}}
.kpi-h{{background:var(--head);border-radius:6px;padding:10px 14px;font-size:14px;margin-bottom:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.kpi-b{{border:1px solid var(--border);border-radius:6px;padding:10px 14px;background:var(--surface)}}
.kpi-b span{{display:block;font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.kpi-b b{{display:block;font-size:30px;font-weight:600;margin-top:4px;white-space:nowrap}}
.kpi-b.crit b{{color:#e66767}} .kpi-b.good b{{color:{GOOD}}} .muted{{color:var(--muted);font-size:13px}}
.meter{{position:relative;height:6px;background:var(--track);border-radius:3px;margin-top:8px}}
.meter div{{height:100%;border-radius:3px}} .meter i{{position:absolute;top:-2px;width:1px;height:10px;background:{CRIT}}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(480px,100%),1fr));gap:16px}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px}}
.card h3{{margin:0 0 10px;font-size:15px}} .flab{{font-size:12px;color:var(--muted);margin:12px 0 6px}}
svg text{{font-size:12px;fill:var(--ink2)}} svg .lab{{fill:var(--ink)}} svg .strong{{font-weight:600;fill:var(--ink)}}
svg .tick{{font-size:11px;fill:var(--muted);font-variant-numeric:tabular-nums}} svg .sub{{font-size:10.5px}}
svg .grid{{stroke:var(--grid)}} svg .axis{{stroke:var(--muted)}} svg .ref{{stroke:{CRIT};stroke-width:1.2;stroke-dasharray:3 3}}
svg .track{{fill:var(--track)}} svg rect:hover{{opacity:.85}} svg .dot{{stroke:var(--surface);stroke-width:2}}
svg .hover{{fill:transparent}} svg .hover:hover{{fill:rgba(255,255,255,.05)}}
.legend{{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--ink2);margin-top:6px}}
.legend i{{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;vertical-align:-1px}}
.legend .hatch{{background:repeating-linear-gradient(45deg,var(--muted) 0 2px,transparent 2px 5px);border:1px solid var(--muted)}}
details{{margin:4px 0;overflow-x:auto}} summary{{cursor:pointer;font-size:14px;color:var(--ink2);padding:6px 0}}
table{{border-collapse:collapse;font-size:12px;width:100%;margin:6px 0 10px;font-variant-numeric:tabular-nums}}
td,th{{border-bottom:1px solid var(--grid);padding:4px 6px;text-align:left}} th{{color:var(--muted);font-weight:500}}
@media (max-width:820px){{
 body{{display:block}} aside{{display:none}}
 main{{padding:12px}} .top{{flex-direction:column;align-items:stretch;padding:12px;min-height:0}} .logo{{display:none}}
 .top h1{{font-size:22px}} .top .tabs{{display:flex}} .ctl{{justify-content:space-between}}
 .kpis{{grid-template-columns:repeat(2,1fr);gap:10px}} .kpi-b b{{font-size:24px}} .card{{padding:12px}}
}}
@media (min-width:600px) and (max-width:820px){{ .kpis{{grid-template-columns:repeat(3,1fr)}} }}
"""
