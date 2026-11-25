"""Me page: the signed-in member's own quotas, share of group storage, and jobs. No other member appears here."""
from . import cache
from . import collectors as c
from .cache import CACHE_TTL, STORAGE_TTL, cached
from .charts import count, size
from .panels import (EMPTY_ST, JUPYTER_JOB, card, efficiency_card, fold, folders_table, gather, jobs_per_day, kpi, my_share_chart, ok, pct,
                     pending_table, problems, problems_table, quota_chart, quota_tile, safe, val)

SCRATCH_NOTE = "files unused for 60 days are purged"


def my_quotas(st):
    return [q for q in st["quotas"] if q["label"].startswith(c.USER + " ")]


def build_me(days):
    """Returns (kpi_html, grid_html, alerts, colors)."""
    d = gather({"st": lambda: cache.storage(),
                "jb": lambda: cached("jobs", CACHE_TTL, c.jobs),
                "hist": lambda: cached(("my-hist", days), CACHE_TTL, c.recent, days),
                "pend": lambda: cached("pending", CACHE_TTL, c.pending_start),
                "eff": lambda: cached(("eff", days), STORAGE_TTL, c.efficiency, days),
                "dirs": lambda: cached("my-folders", 3600, lambda: c.my_folders(c.groups()))})
    st = ok(d, "st") or EMPTY_ST
    mine = my_quotas(st)
    jb = ok(d, "jb")
    hist = ok(d, "hist")
    running = [r for r in jb if len(r) > 2 and r[2] == "RUNNING"] if jb is not None else []
    pending = [r for r in jb if len(r) > 2 and r[2] == "PENDING"] if jb is not None else []

    shares = []
    for g in st["groups"]:
        q = next((q for q in st["quotas"] if q["label"] == f"{g} /project"), None)
        us = st["breakdown"].get(("project", g))
        if q and us is not None:
            used = us.get(c.USER, {"disk": 0.0, "tape": 0.0})
            used = used["disk"] + used["tape"]
            shares.append((100 * used / max(q["quota"], 1), g, used))
    if shares:
        share, g, used = max(shares)
        proj = kpi("▤", "My /project", f"{g} · {share:.2f}% of the group quota", size(used), "", share)
    else:
        proj = kpi("▤", "My /project", "no data", "—")
    bad = problems(hist) if hist is not None else None
    bad = [r for r in bad if r[1] != JUPYTER_JOB] if bad is not None else None   # session time limits are not problems
    home = next((q for q in mine if q["label"].endswith(" /home")), None)
    scratch = next((q for q in mine if q["label"].endswith(" /scratch")), None)
    k = [quota_tile(home, "/home") if home else kpi("▤", "/home", "no data", "—"),
         quota_tile(scratch, "/scratch", SCRATCH_NOTE) if scratch else kpi("▤", "/scratch", f"no data · {SCRATCH_NOTE}", "—"),
         proj,
         kpi("▶", "My jobs", "running · pending", f"{len(running)} · {len(pending)}" if jb is not None else "—", "good" if running else ""),
         kpi("✗", "My problem jobs", f"last {days} d", len(bad) if bad is not None else "—", "crit" if bad else "")]
    alerts = [f"{q['label']} {name} {p:.0f}%" for q in mine
              for name, p in (("space", pct(q)), ("files", pct(q, "files", "fquota"))) if p >= 95]

    def problem_fold():
        rows = problems(val(d, "hist"))
        return fold(f"Problem jobs, last {days} d ({len(rows)})", problems_table(rows), open_=True)

    grid = "".join([
        safe("My /home and /scratch", lambda: card("My /home and /scratch",
             '<div class="flab">space</div>' + quota_chart(my_quotas(val(d, "st")), "used", "quota", size)
             + '<div class="flab">files</div>' + quota_chart(my_quotas(val(d, "st")), "files", "fquota", count))),
        safe("My share of group storage", lambda: card("My share of group storage",
             my_share_chart(val(d, "st")["breakdown"], val(d, "st")["quotas"]))),
        safe("My folders in /project", lambda: card("My folders in /project", folders_table(val(d, "dirs")))),
        safe("My jobs finished per day", lambda: card(f"My jobs finished per day · last {days} d", jobs_per_day(val(d, "hist"), days))),
        safe("My job efficiency", lambda: efficiency_card(val(d, "eff"), days)[0]),
        safe("Pending jobs", lambda: fold(f"Pending jobs ({len(val(d, 'pend'))})", pending_table(val(d, "pend")), open_=True)),
        safe("Problem jobs", problem_fold),
    ])
    return "".join(k), grid, alerts, {}
