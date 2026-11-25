import concurrent.futures
import datetime as dt
import json
import os
import re
import subprocess
import time

USER = os.environ.get("USER", "")
UNITS = {"B": 1, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40, "PiB": 2**50}


def run(cmd, timeout=90):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return ""


def run_checked(cmd, timeout=90):
    """Like run(), but a non-zero exit or a timeout raises RuntimeError (so a failure is never mistaken for empty data)."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"timed out after {timeout}s: {cmd.split()[0]}") from None
    if r.returncode != 0:
        raise RuntimeError(f"{cmd.split()[0]} failed ({r.returncode}): {r.stderr.strip()[:120]}")
    return r.stdout


def to_bytes(s):
    m = re.match(r"([\d.]+)\s*([KMGTP]?i?B)", s.strip())
    return float(m.group(1)) * UNITS.get(m.group(2), 1) if m else 0.0


def to_count(s):
    s = s.strip()
    mult = {"K": 1e3, "M": 1e6, "G": 1e9}.get(s[-1:], 1)
    return float(s.rstrip("KMG ") or 0) * mult


def user_quotas():
    rows = []
    pat = re.compile(r"^\s*/(\w+) \(user [^)]*\)\s*(?:->)?\s*([\d.]+\s*\w+)/\s*([\d.]+\s*\w+)\s+([\d.]+\s*\w?)\s*/\s*([\d.]+\s*\w?)")
    for line in run("diskusage_report --home --scratch").splitlines():
        m = pat.match(line)
        if m:
            fs, su, sq, fu, fq = m.groups()
            rows.append({"label": f"{USER} /{fs}", "used": to_bytes(su), "quota": to_bytes(sq),
                         "files": to_count(fu), "fquota": to_count(fq)})
    return rows


def groups():
    return sorted(g for g in run("groups").split() if g.startswith(("def-", "rrg-", "rpp-")))


STATS_CACHE = os.path.join(os.path.expanduser("~"), ".firdash", "stats")


def _quota_row(fs, group):
    """Group quota from lfs on a node where /{fs} is mounted; None otherwise."""
    path = os.path.realpath(f"/{fs}/{group}")
    if not os.path.isdir(path):
        return None
    pid = run(f"lfs project -d {path}", 30).split()
    if not pid or not pid[0].isdigit() or pid[0] == "0":
        return None
    q = run(f"lfs quota -q -p {pid[0]} /{fs}", 30).replace("*", "").split()
    if len(q) < 8:
        return None
    return {"label": f"{group} /{fs}", "used": float(q[1]) * 1024, "quota": float(q[3]) * 1024,
            "files": float(q[5]), "fquota": float(q[7])}


_memo = {}


def fs_stats(fs, group):
    """Memoised for 60 s: the storage layer asks for the same file several times per refresh."""
    hit = _memo.get((fs, group))
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    data = _fs_stats(fs, group)
    _memo[(fs, group)] = (time.time(), data)
    return data


def _fs_stats(fs, group):
    """Quota row and per-user stats for one group filesystem.

    Reads the nightly robinhood file /{fs}/.stats/{group}.json (what diskusage_rbh prints) and the lfs quota.
    /nearline is mounted on login nodes only, so whenever both are readable they are also written to
    ~/.firdash/stats/{fs}-{group}.json; on a node without the mount (compute nodes, where the published
    snapshot is built) that copy is used instead and "asof" carries its date.
    Returns {"quota": row|None, "stats": [...], "asof": "YYYY-MM-DD"|None}.
    """
    live = f"/{fs}/.stats/{group}.json"
    cache = os.path.join(STATS_CACHE, f"{fs}-{group}.json")
    data = {"quota": None, "stats": [], "asof": None}
    try:
        with open(live) as f:
            data["stats"] = json.load(f).get("hsm_size", [])
        data["quota"] = _quota_row(fs, group)
    except OSError:
        pass
    if data["stats"]:
        try:
            os.makedirs(STATS_CACHE, exist_ok=True)
            tmp = cache + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"time": time.strftime("%Y-%m-%d %H:%M"), **data}, f)
            os.replace(tmp, cache)
        except OSError:
            pass
        return data
    try:
        with open(cache) as f:
            d = json.load(f)
        return {"quota": d.get("quota"), "stats": d.get("stats", []), "asof": d.get("time", "")[:10]}
    except (OSError, ValueError):
        return data


def group_quotas():
    rows = []
    for g in groups():
        for fs in ("project", "nearline"):
            row = fs_stats(fs, g)["quota"]
            if row:
                rows.append(row)
    return rows


def user_breakdown(fs, group):
    """{user: {"disk": bytes, "tape": bytes, "files": n}} from the robinhood stats, same rules as diskusage_rbh."""
    users = {}
    for e in fs_stats(fs, group)["stats"]:
        loc = e.get("location", "")
        if e.get("uid") == "root" or (loc == "On disk") != (fs == "project"):
            continue
        n = e.get("size", 0) if loc == "On tape" else e.get("volume", 0)
        d = users.setdefault(e["uid"], {"disk": 0.0, "tape": 0.0, "files": 0})
        d["tape" if loc == "On tape" else "disk"] += float(n)
        d["files"] += int(e.get("count", 0))
    return {u: v for u, v in users.items() if v["disk"] + v["tape"] > 0}


def stats_asof():
    """Oldest cache date in use for /nearline, or None when it was read live."""
    dates = [d for d in (fs_stats("nearline", g)["asof"] for g in groups()) if d]
    return min(dates) if dates else None


def fairshare():
    out = run("sshare -U -n -P -o Account,NormShares,EffectvUsage,FairShare,LevelFS")
    return [l.split("|") for l in out.splitlines() if l.strip()]


def jobs():
    out = run(f"squeue -u {USER} -h -o '%i|%j|%T|%M|%l|%D|%C|%m|%a|%R'")
    return [l.split("|") for l in out.splitlines() if l.strip()]


def recent(days, accounts=None):
    who = f"-a -A {','.join(sorted(set(accounts)))}" if accounts else f"-u {USER}"
    out = run(f"sacct {who} -S now-{days}days -X -n -P -o JobID,JobName,State,Elapsed,Timelimit,ReqMem,ExitCode,Account,End,User", 120)
    return [l.split("|") for l in out.splitlines() if l.strip()]


def nodes():
    """Cores by node type: {type: [alloc, idle, other, total, n_nodes]}."""
    seen, out = set(), {}
    for line in run("sinfo -h -N -o '%N|%m|%G|%C'").splitlines():
        parts = line.split("|")
        if len(parts) != 4 or parts[0] in seen:
            continue
        seen.add(parts[0])
        _, mem, gres, cores = parts
        kind = "GPU nodes (H100)" if "gpu" in gres else "Large-mem CPU" if int(mem) > 2_000_000 else "Regular CPU"
        acc = out.setdefault(kind, [0, 0, 0, 0, 0])
        for k, v in enumerate([int(x) for x in cores.split("/")] + [1]):
            acc[k] += v
    return out


def pending_start():
    """Pending jobs with Slurm's expected start time and reason."""
    out = run(f"squeue -u {USER} --start -h -o '%i|%j|%a|%C|%r|%S'")
    return [l.split("|") for l in out.splitlines() if l.strip()]


def _secs(t):
    t = t.strip()
    if not t or t in ("INVALID", "UNLIMITED"):
        return 0.0
    days, _, hms = t.rpartition("-")
    parts = [float(x) for x in hms.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return (float(days) if days else 0.0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def _mem(s, cpus=1, nodes=1):
    s = s.strip()
    if not s:
        return 0.0
    per = s[-1] if s[-1] in "cn" else ""
    s = s.rstrip("cn")
    m = re.match(r"([\d.]+)([KMGT]?)", s)
    if not m:
        return 0.0
    v = float(m.group(1)) * {"": 1, "K": 2**10, "M": 2**20, "G": 2**30, "T": 2**40}[m.group(2)]
    return v * (cpus if per == "c" else nodes if per == "n" else 1)


def efficiency(days):
    """Finished jobs (>= 2 min) of the last `days` days: CPU efficiency = TotalCPU / (Elapsed x CPUs),
    memory efficiency = peak RSS of any step / requested memory."""
    out = run(f"sacct -u {USER} -S now-{days}days -n -P -o JobID,JobName,State,Elapsed,TotalCPU,AllocCPUS,ReqMem,MaxRSS,Account", 180)
    jobs = {}
    for line in out.splitlines():
        p = [f.strip() for f in line.split("|")]
        if len(p) < 9:
            continue
        jid, step = p[0].split(".", 1) if "." in p[0] else (p[0], None)
        if step is None:
            jobs[jid] = {"name": p[1], "state": p[2].split()[0], "elapsed": _secs(p[3]), "cpu": _secs(p[4]),
                         "cpus": int(p[5] or 0), "req": _mem(p[6], int(p[5] or 1)), "rss": 0.0, "account": p[8]}
        elif jid in jobs:
            jobs[jid]["rss"] = max(jobs[jid]["rss"], _mem(p[7]))
    return [dict(j, id=k) for k, j in jobs.items()
            if j["state"] in ("COMPLETED", "TIMEOUT", "OUT_OF_MEMORY") and j["elapsed"] >= 120 and j["cpus"] > 0]


def _month_starts(n, today=None):
    """First day of the last n calendar months, oldest first, then the first day of next month (end bound)."""
    today = today or dt.date.today()
    starts = [today.replace(day=1)]
    for _ in range(n - 1):
        starts.insert(0, (starts[0] - dt.timedelta(days=1)).replace(day=1))
    return starts + [(starts[-1] + dt.timedelta(days=32)).replace(day=1)]


def parse_sreport(text, tres):
    """{user: hours} from `sreport -P -n cluster AccountUtilizationByUser`. Rows with an empty login are
    account totals and are skipped. With tres (e.g. 'gres/gpu') the row is Account|Login|TRESName|Used."""
    out = {}
    for line in text.splitlines():
        p = [f.strip() for f in line.split("|")]
        if tres:
            if len(p) < 4 or not p[1] or p[2] != tres:
                continue
            user, used = p[1], p[3]
        else:
            if len(p) < 3 or not p[1]:
                continue
            user, used = p[1], p[2]
        try:
            out[user] = out.get(user, 0.0) + float(used)
        except ValueError:
            continue
    return out


def usage_by_month(accounts, months=3, today=None):
    """{'YYYY-MM': {user: {'cpu': core-hours, 'gpu': gpu-hours}}} for the last `months` calendar months.
    One sreport call per month for the *_cpu accounts and one with --tres=gres/gpu for the *_gpu accounts,
    run in parallel. Only accounts ending _cpu / _gpu are used (others are ignored). "cpu" counts the
    core-hours charged to the _cpu accounts only (cores used by GPU jobs are charged to the _gpu accounts
    and are not included). A failed sreport raises RuntimeError."""
    cpu = sorted(a for a in set(accounts) if a.endswith("_cpu"))
    gpu = sorted(a for a in set(accounts) if a.endswith("_gpu"))
    today = today or dt.date.today()
    bounds = _month_starts(months, today)
    out = {}
    jobs = []
    for s, e in zip(bounds, bounds[1:]):
        end = "now" if e > today else str(e)
        key = s.strftime("%Y-%m")
        out[key] = {}
        base = "sreport -t hours -P -n cluster AccountUtilizationByUser"
        if cpu:
            jobs.append((key, "cpu", None, f"{base} accounts={','.join(cpu)} start={s} end={end} "
                                           "format=Account,Login,Used"))
        if gpu:
            jobs.append((key, "gpu", "gres/gpu", f"{base} accounts={','.join(gpu)} start={s} end={end} "
                                                 "--tres=gres/gpu format=Account,Login,TRESName,Used"))
    if not jobs:
        return out
    with concurrent.futures.ThreadPoolExecutor(6) as ex:
        futures = [ex.submit(run_checked, cmd, 30) for _, _, _, cmd in jobs]
        for (key, kind, tres, _), f in zip(jobs, futures):
            for u, h in parse_sreport(f.result(), tres).items():
                out[key].setdefault(u, {"cpu": 0.0, "gpu": 0.0})[kind] += h
    return out


def parse_queue_wait(text):
    """[(start date 'YYYY-MM-DD', wait seconds)] from sacct rows 'Submit|Start|State'. Jobs that have not
    started (Unknown/None start, PENDING) and unparsable lines are skipped; a negative wait is clamped to 0."""
    out = []
    for line in text.splitlines():
        p = [f.strip() for f in line.split("|")]
        if len(p) < 3 or p[1] in ("Unknown", "None", "") or p[2].startswith("PENDING"):
            continue
        try:
            sub, st = dt.datetime.fromisoformat(p[0]), dt.datetime.fromisoformat(p[1])
        except ValueError:
            continue
        out.append((st.date().isoformat(), max(0.0, (st - sub).total_seconds())))
    return out


def queue_wait(accounts, days):
    """[(start date, wait seconds)] for jobs on the accounts. The date is the job's start date; pending jobs are
    excluded. sacct -S also includes jobs that started before the window, so callers bucket by start date.
    A failing or timed-out sacct raises RuntimeError (run_checked) so it is never mistaken for 'no jobs'."""
    if not accounts:
        return []
    return parse_queue_wait(run_checked(f"SLURM_TIME_FORMAT=standard sacct -a -A {','.join(sorted(set(accounts)))} -S now-{days}days -X -n -P -o Submit,Start,State", 180))
