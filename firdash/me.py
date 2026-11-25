"""Me page: the signed-in member's own quotas, share of group storage, and jobs. No other member appears here."""
from . import cache
from . import collectors as c
from .cache import CACHE_TTL, STORAGE_TTL, cached
from .charts import count, size
from .panels import (BAD_STATES, card, efficiency_card, fold, gather, jobs_per_day, kpi, my_share_chart, ok,
                     pending_table, problems_table, quota_chart, quota_tile, safe, val)

EMPTY_ST = {"groups": [], "quotas": [], "breakdown": {}, "asof": None}
SCRATCH_NOTE = "files unused for 60 days are purged"


def my_quotas(st):
    return [q for q in st["quotas"] if q["label"].startswith(c.USER + " ")]


def problems(hist):
    return [r for r in hist if len(r) > 2 and r[2].split()[0] in BAD_STATES]


def build_me(days):
    """Returns (kpi_html, grid_html, alerts, colors)."""
    d = gather({"st": lambda: cache.storage(),
                "jb": lambda: cached("jobs", CACHE_TTL, c.jobs),
                "hist": lambda: cached(("my-hist", days), CACHE_TTL, c.recent, days),
                "pend": lambda: cached("pending", CACHE_TTL, c.pending_start),
                "eff": lambda: cached(("eff", days), STORAGE_TTL, c.efficiency, days)})
    st = ok(d, "st") or EMPTY_ST
    mine = my_quotas(st)
    jb = ok(d, "jb")
    hist = ok(d, "hist")
    running = [r for r in jb if len(r) > 2 and r[2] == "RUNNING"] if jb is not None else []
    pending = [r for r in jb if len(r) > 2 and r[2] == "PENDING"] if jb is not None else []

    empty = {"disk": 0.0, "tape": 0.0}
    my_proj = sum(us.get(c.USER, empty)["disk"] + us.get(c.USER, empty)["tape"]
                  for (fs_, _), us in st["breakdown"].items() if fs_ == "project")
    proj_quota = sum(q["quota"] for q in st["quotas"] if q["label"].endswith(" /project") and not q["label"].startswith(c.USER + " "))
    home = next((q for q in mine if q["label"].endswith(" /home")), None)
    scratch = next((q for q in mine if q["label"].endswith(" /scratch")), None)
    k = [quota_tile(home, "/home") if home else kpi("▤", "/home", "no data", "—"),
         quota_tile(scratch, "/scratch", SCRATCH_NOTE) if scratch else kpi("▤", "/scratch", f"no data · {SCRATCH_NOTE}", "—"),
         kpi("▤", "My /project", f"{100 * my_proj / max(proj_quota, 1):.2f}% of the group quota" if proj_quota else "no data",
             size(my_proj) if st["breakdown"] else "—"),
         kpi("▶", "My jobs", "running · pending", f"{len(running)} · {len(pending)}" if jb is not None else "—", "good" if running else ""),
         kpi("✗", "My problem jobs", f"last {days} d", len(problems(hist)) if hist is not None else "—",
             "crit" if hist and problems(hist) else "")]
    alerts = [f"{q['label']} {name} {p:.0f}%" for q in mine
              for name, p in (("space", 100 * q["used"] / max(q["quota"], 1)), ("files", 100 * q["files"] / max(q["fquota"], 1))) if p >= 95]

    def problem_fold():
        bad = problems(val(d, "hist"))
        return fold(f"Problem jobs, last {days} d ({len(bad)})", problems_table(bad), open_=True)

    grid = "".join([
        safe("My /home and /scratch", lambda: card("My /home and /scratch",
             '<div class="flab">space</div>' + quota_chart(my_quotas(val(d, "st")), "used", "quota", size)
             + '<div class="flab">files</div>' + quota_chart(my_quotas(val(d, "st")), "files", "fquota", count))),
        safe("My share of group storage", lambda: card("My share of group storage",
             my_share_chart(val(d, "st")["breakdown"], val(d, "st")["quotas"]))),
        safe("My jobs finished per day", lambda: card(f"My jobs finished per day · last {days} d", jobs_per_day(val(d, "hist"), days))),
        safe("My job efficiency", lambda: efficiency_card(val(d, "eff"), days)[0]),
        safe("Pending jobs", lambda: fold(f"Pending jobs ({len(val(d, 'pend'))})", pending_table(val(d, "pend")), open_=True)),
        safe("Problem jobs", problem_fold),
    ])
    return "".join(k), grid, alerts, {}
