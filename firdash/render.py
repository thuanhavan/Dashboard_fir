import html
import socket
import time

from .collectors import USER, fairshare, jobs, quotas, recent

CACHE_TTL = 60


def bar(pct):
    c = "#2e7d32" if pct < 80 else "#ef6c00" if pct < 95 else "#c62828"
    return (f'<div class="bar"><div style="width:{min(pct,100):.0f}%;background:{c}"></div>'
            f'<span>{pct:.0f}%</span></div>')


def table(headers, rows, row_class=None):
    h = "".join(f"<th>{html.escape(x)}</th>" for x in headers)
    body = ""
    for r in rows:
        cls = f' class="{row_class(r)}"' if row_class else ""
        body += f"<tr{cls}>" + "".join(f"<td>{html.escape(str(x))}</td>" for x in r) + "</tr>"
    return f"<table><tr>{h}</tr>{body or '<tr><td colspan=99><i>none</i></td></tr>'}</table>"


def build(days):
    q = quotas()
    qrows = "".join(
        f"<tr><td>{html.escape(r['name'])}</td><td>{r['space']}</td><td>{bar(r['space_pct'])}</td>"
        f"<td>{r['files']}</td><td>{bar(r['files_pct'])}</td></tr>" for r in q)
    alerts = [f"{r['name']}: {k} at {r[k + '_pct']:.0f}%" for r in q for k in ("space", "files") if r[k + "_pct"] >= 90]

    j = jobs()
    states = {}
    for r in j:
        states[r[2]] = states.get(r[2], 0) + 1
    hist = recent(days)
    bad = [r for r in hist if r[2].split()[0] in ("FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "CANCELLED")]
    hstates = {}
    for r in hist:
        s = r[2].split()[0]
        hstates[s] = hstates.get(s, 0) + 1

    alert_html = "".join(f'<div class="alert">⚠ {html.escape(a)}</div>' for a in alerts)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Fir – {USER}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px;color:#222;max-width:1300px}}
h2{{border-bottom:1px solid #ddd;padding-bottom:4px;margin-top:28px}}
table{{border-collapse:collapse;font-size:13px;width:100%}}
td,th{{border:1px solid #e3e3e3;padding:4px 8px;text-align:left}} th{{background:#f5f5f5}}
.bar{{position:relative;width:160px;height:16px;background:#eee;border-radius:3px}}
.bar div{{height:100%;border-radius:3px}} .bar span{{position:absolute;left:6px;top:0;font-size:11px}}
.alert{{background:#ffebee;border-left:4px solid #c62828;padding:6px 10px;margin:4px 0}}
.bad{{background:#fff3f3}} .pend{{background:#fffbe6}} .muted{{color:#888;font-size:12px}}
.pill{{display:inline-block;background:#eef;padding:2px 8px;border-radius:10px;margin-right:6px}}
</style></head><body>
<h1>Fir resource dashboard — {USER}</h1>
<div class="muted">{time.strftime('%Y-%m-%d %H:%M:%S')} on {socket.gethostname()} · cached {CACHE_TTL}s · reload page to refresh</div>
{alert_html}
<h2>Storage quotas</h2>
<table><tr><th>Filesystem</th><th>Space</th><th></th><th>Files (inodes)</th><th></th></tr>{qrows}</table>
<h2>Queue ({len(j)} jobs)</h2>
<div>{''.join(f'<span class="pill">{k}: {v}</span>' for k, v in states.items())}</div><br>
{table(["JobID","Name","State","Elapsed","Limit","Nodes","CPUs","Mem","Account","Node / Reason"], j,
       lambda r: "pend" if r[2] == "PENDING" else "")}
<h2>Fairshare / priority</h2>
<div class="muted">FairShare closer to 1 = higher priority; LevelFS &gt;1 means under-used share.</div>
{table(["Account","NormShares","EffectvUsage","FairShare","LevelFS"], fairshare())}
<h2>Last {days} days: {len(hist)} jobs</h2>
<div>{''.join(f'<span class="pill">{k}: {v}</span>' for k, v in sorted(hstates.items()))}</div><br>
<h3>Problem jobs ({len(bad)})</h3>
{table(["JobID","Name","State","Elapsed","Limit","ReqMem","Exit","Account","End"], bad[-200:], lambda r: "bad")}
</body></html>"""
