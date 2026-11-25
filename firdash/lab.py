"""Lab page: the whole picture of every group the user belongs to. Storage pressure and compute use, equally."""
from . import collectors as c
from . import cache
from .cache import CACHE_TTL, STORAGE_TTL, cached
from .charts import GOOD, OTHER, SERIES, count, legend, size
from .panels import (section, stats_lag_note, EMPTY_ST, card, extend_colors, fairshare_chart, fold, gather, hours_text, jobs_per_day, kpi, level,
                     member_chart, month_label, nodes_chart, ok, pct, problems, problems_table, quota_chart, queue_wait_chart,
                     in_window, safe, usage_chart, usage_month_chart, user_colors, val)


def group_quotas(st):
    """Group rows only (not the user's /home and /scratch), /project before /nearline."""
    order = {"/project": 0, "/nearline": 1}
    rows = [q for q in st["quotas"] if not q["label"].startswith(c.USER + " ")]
    return sorted(rows, key=lambda q: (order.get(q["label"].split()[1], 9), q["label"]))


def quota_kpi(icon, title, q, key, qkey, fmt):
    p = pct(q, key, qkey)
    return kpi("⚠" if p >= 95 else icon, title, f"{q['label'].split()[0]} · {fmt(q[key])} / {fmt(q[qkey])}",
               f"{p:.0f}%", level(p), p)


def median(xs):
    """Upper nearest-rank median (same rule as panels.queue_wait_chart)."""
    return sorted(xs)[min(len(xs) - 1, int(0.5 * len(xs)))] if xs else 0.0


def build_lab(days):
    """Returns (kpi_html, grid_html, alerts, colors)."""
    d = gather({"fs": lambda: cached("fairshare", CACHE_TTL, c.fairshare)})
    accounts = [r[0] for r in (ok(d, "fs") or [])]
    accts = tuple(sorted(accounts))
    tasks = {"st": lambda: cache.storage(),   # looked up at call time so tests can patch cache.storage
             "nd": lambda: cached("nodes", CACHE_TTL, c.nodes)}
    if accts:
        # Only usage and waits use the storage TTL (10 min): slow-changing, one slurmdbd query each.
        tasks.update({"hist": lambda: cached(("lab-hist", days, accts), STORAGE_TTL, c.recent, days, accounts),
                      "usage": lambda: cached(("usage", accts), STORAGE_TTL, c.usage_by_month, accounts),
                      "waits": lambda: cached(("wait", days, accts), STORAGE_TTL, c.queue_wait, accounts, days)})
    d.update(gather(tasks))
    if not accts:
        d["hist"] = d["usage"] = d["waits"] = RuntimeError("no Slurm accounts: sshare failed or returned nothing")
    st = ok(d, "st") or EMPTY_ST
    gq = group_quotas(st)
    hist = ok(d, "hist") or []
    usage = ok(d, "usage") or {}
    waits = in_window(ok(d, "waits") or [], days)

    colors = user_colors(st["breakdown"])
    colors = extend_colors(colors, sorted({r[9] for r in hist if len(r) > 9 and r[9]}))
    colors = extend_colors(colors, sorted({u for m in usage.values() for u in m}))
    users = list(colors.items()) + [("other", OTHER)]

    # KPI row: /project space, /project files, nearline total, this month's CPU and GPU hours, median queue wait
    proj = [q for q in gq if q["label"].endswith(" /project")]
    k = []
    q = max(proj, key=lambda q: pct(q), default=None)
    k.append(quota_kpi("▤", "/project space", q, "used", "quota", size) if q else kpi("▤", "/project space", "no data", "—"))
    q = max(proj, key=lambda q: pct(q, "files", "fquota"), default=None)
    k.append(quota_kpi("☰", "/project files", q, "files", "fquota", count) if q else kpi("☰", "/project files", "no data", "—"))
    near = sum(v["disk"] + v["tape"] for (fs_, _), us in st["breakdown"].items() if fs_ == "nearline" for v in us.values())
    k.append(kpi("◫", "/nearline", "disk + tape · all groups" + (f" · as of {st['asof']}" if st.get("asof") else ""), size(near) if st["breakdown"] else "—"))
    month = max(usage) if usage else None
    mlabel = month_label(month) if month else "no data"
    k.append(kpi("▦", "CPU core-hours", mlabel, f"{sum(v['cpu'] for v in usage[month].values()):,.0f}" if month else "—"))
    k.append(kpi("▣", "GPU-hours", mlabel, f"{sum(v['gpu'] for v in usage[month].values()):,.0f}" if month else "—"))
    k.append(kpi("◷", "Queue wait", f"median · last {days} d · {len(waits):,} jobs", hours_text(median([w for _, w in waits]) / 3600) if waits else "—"))

    alerts = [f"{q['label']} {name} {p:.0f}%" for q in gq
              for name, p in (("space", pct(q)), ("files", pct(q, "files", "fquota"))) if p >= 95]

    def problem_fold():
        bad = problems(val(d, "hist"))
        return fold(f"Problem jobs, all members, last {days} d ({len(bad)})", problems_table(bad, with_user=True))

    near_title = "/nearline by user (TiB) · nightly stats" + (f" · login-node copy of {st['asof']}" if st.get("asof") else "")
    grid = "".join([
        section("Storage"),
        safe("/project by user (TiB)", lambda: card("/project by user (TiB) · nightly stats, may lag recent moves",
             usage_chart(val(d, "st")["breakdown"], "project", val(d, "st")["groups"], colors, False) + legend(users)
             + stats_lag_note(val(d, "st")["breakdown"], val(d, "st")["quotas"], "project"))),
        safe(near_title, lambda: card(near_title,
             usage_chart(val(d, "st")["breakdown"], "nearline", val(d, "st")["groups"], colors, True) + legend(users, True))),
        safe("Space quota", lambda: card("Space quota", quota_chart(group_quotas(val(d, "st")), "used", "quota", size))),
        safe("File-count quota", lambda: card("File-count quota", quota_chart(group_quotas(val(d, "st")), "files", "fquota", count))),
        section("Compute"),
        safe("CPU core-hours per month by member", lambda: card("CPU core-hours per month by member",
             usage_month_chart(val(d, "usage"), "cpu", colors))),
        safe("GPU-hours per month by member", lambda: card("GPU-hours per month by member",
             usage_month_chart(val(d, "usage"), "gpu", colors))),
        safe("Queue wait", lambda: card(f"Queue wait per {'day' if days < 14 else 'week'} · hours · last {days} d",
             queue_wait_chart(val(d, "waits"), days))),
        safe("Fairshare · cluster cores now", lambda: card("Fairshare · cluster cores now",
             fairshare_chart(val(d, "fs")) + '<div style="height:14px"></div>' + nodes_chart(val(d, "nd"))
             + legend([("busy", SERIES[0]), ("idle", GOOD), ("down/drained", OTHER)]))),
        section("Jobs"),
        safe("Jobs finished per day", lambda: card(f"Jobs finished per day · all members · last {days} d", jobs_per_day(val(d, "hist"), days))),
        safe("Jobs by member", lambda: card(f"Jobs by member · last {days} d", member_chart(val(d, "hist")))),
        safe("Problem jobs", problem_fold),
    ])
    return "".join(k), grid, alerts, colors
