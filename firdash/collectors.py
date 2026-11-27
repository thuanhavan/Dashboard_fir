import concurrent.futures
import datetime as dt
import json
import os
import pwd
import re
import subprocess
import threading
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
    name = next((w for w in cmd.split() if "=" not in w), cmd[:20])
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"timed out after {timeout}s: {name}") from None
    if r.returncode != 0:
        raise RuntimeError(f"{name} failed ({r.returncode}): {r.stderr.strip()[:120]}")
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


MAIN_GROUPS = ("def-kanj241",)   # listed first everywhere; the rest alphabetically


def group_rank(g):
    return (MAIN_GROUPS.index(g) if g in MAIN_GROUPS else len(MAIN_GROUPS), g)


def groups():
    return sorted((g for g in run("groups").split() if g.startswith(("def-", "rrg-", "rpp-"))), key=group_rank)


# Copies of the /nearline nightly stats for nodes without the mount. The shared folder (inside the member copy of
# the repo, group-writable) serves everyone; the home folder is the fallback when the shared one is not writable.
STATS_DIRS = [os.environ.get("FIRDASH_STATS_DIR")
              or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "var", "stats"),
              os.path.join(os.path.expanduser("~"), ".firdash", "stats")]


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
    {fs}-{group}.json in the first writable STATS_DIRS folder; on a node without the mount (compute nodes, where
    JupyterHub sessions run) the newest copy found there is used instead and "asof" carries its date.
    For /nearline our own scan (nearline_scan) replaces the robinhood entries when available.
    Returns {"quota": row|None, "stats": [...], "asof": "YYYY-MM-DD"|None, "source": "robinhood"|"scan", ["scan": {...}]}.
    """
    live = f"/{fs}/.stats/{group}.json"
    name = f"{fs}-{group}.json"
    data = {"quota": None, "stats": [], "asof": None}
    try:
        with open(live) as f:
            data["stats"] = json.load(f).get("hsm_size", [])
        data["quota"] = _quota_row(fs, group)
    except (OSError, ValueError):
        pass
    if data["stats"]:
        _save_stats(name, {"time": time.strftime("%Y-%m-%d %H:%M"), **data})
    else:
        best = None
        for d in STATS_DIRS:
            try:
                with open(os.path.join(d, name)) as f:
                    c = json.load(f)
            except (OSError, ValueError):
                continue
            if best is None or c.get("time", "") > best.get("time", ""):
                best = c
        if best is not None:
            data = {"quota": best.get("quota"), "stats": best.get("stats", []), "asof": best.get("time", "")[:10]}
    data["source"] = "robinhood"
    if fs == "nearline":
        # Our own per-file scan beats the nightly robinhood numbers whenever one exists
        scan = nearline_scan(group)
        if scan:
            data.update(stats=scan_entries(scan), asof=scan["time"][:10], source="scan", scan=scan)
    return data


# ---- /nearline scan: our own, realtime per-file view (login nodes only, where /nearline is mounted) -------------

HSM_STATES = ("On disk", "Archiving in progress", "On disk and tape", "Modified, will be archived again", "On tape")


def hsm_state(flags):
    """Lustre HSM flag words -> the robinhood location names used everywhere else."""
    f = set(flags.split())
    if "released" in f:
        return "On tape"
    if "dirty" in f:
        return "Modified, will be archived again"
    if "archived" in f:
        return "On disk and tape"
    if "exists" in f:
        return "Archiving in progress"
    return "On disk"


def parse_hsm(out):
    """{path: state} from `lfs hsm_state` lines such as '/p/f: (0x0000000d) released exists archived, archive_id:2'."""
    states = {}
    for line in out.splitlines():
        m = re.match(r"^(.*): \(0x[0-9a-fA-F]+\)(.*)$", line)
        if m:
            states[m.group(1)] = hsm_state(m.group(2).split(",")[0])
    return states


_owners = {}


def _owner(uid):
    if uid not in _owners:
        try:
            _owners[uid] = pwd.getpwuid(uid).pw_name
        except KeyError:
            _owners[uid] = str(uid)
    return _owners[uid]


def summarise_scan(group, recs, unreadable, now=None):
    """Scan dict from (relpath, owner, bytes, state) records: totals per state, per user, and the folders (two
    levels) holding files that are not yet safely on tape."""
    states = {st: {"files": 0, "bytes": 0} for st in HSM_STATES}
    users, pending = {}, {}
    for rel, user, nbytes, state in recs:
        states[state]["files"] += 1
        states[state]["bytes"] += nbytes
        u = users.setdefault(user, {st: [0, 0] for st in HSM_STATES})
        u[state][0] += 1
        u[state][1] += nbytes
        if state not in ("On tape", "On disk and tape"):
            folder = "/".join(rel.split("/")[:-1][:2]) or "."
            d = pending.setdefault((folder, user, state), [0, 0])
            d[0] += 1
            d[1] += nbytes
    return {"time": now or time.strftime("%Y-%m-%d %H:%M"), "group": group, "files": len(recs),
            "bytes": sum(r[2] for r in recs), "states": states, "users": users,
            "pending": sorted(([f, u, st, n, b] for (f, u, st), (n, b) in pending.items()), key=lambda r: (-r[4], r[0])),
            "unreadable": sorted(unreadable)}


def scan_entries(scan):
    """The scan as robinhood-style hsm_size entries, so the by-user code reads both sources alike."""
    return [{"uid": u, "count": n, "size": b, "volume": 0 if st == "On tape" else b, "location": st}
            for u, per in scan["users"].items() for st, (n, b) in per.items() if n]


def scan_nearline(group):
    """Every file under /nearline/<group> with owner, size and HSM state (about a second per group).
    None when /nearline is not mounted on this node."""
    base = os.path.realpath(f"/nearline/{group}")
    if not os.path.isdir(base):
        return None
    found = subprocess.run(["lfs", "find", base, "-type", "f"], capture_output=True, text=True, timeout=600)
    paths = [l for l in found.stdout.splitlines() if l]
    unreadable = {m.group(1)[len(base) + 1:] for m in re.finditer(r"Failed to open '([^']*)'", found.stderr)}
    states = {}
    for i in range(0, len(paths), 500):
        out = subprocess.run(["lfs", "hsm_state", *paths[i:i + 500]], capture_output=True, text=True, timeout=600)
        states.update(parse_hsm(out.stdout))
    recs = []
    for path in paths:
        try:
            st = os.lstat(path)
        except OSError:
            continue
        recs.append((path[len(base) + 1:], _owner(st.st_uid), st.st_size, states.get(path, "On disk")))
    return summarise_scan(group, recs, unreadable)


def nearline_scan(group):
    """Live scan on a login node (saved for the compute nodes), else the newest saved scan; None without either."""
    name = f"nearline-scan-{group}.json"
    try:
        scan = scan_nearline(group)
    except (OSError, subprocess.SubprocessError, ValueError):
        scan = None
    if scan:
        _save_stats(name, scan)
        return scan
    best = None
    for d in STATS_DIRS:
        try:
            with open(os.path.join(d, name)) as f:
                cand = json.load(f)
        except (OSError, ValueError):
            continue
        if best is None or cand.get("time", "") > best.get("time", ""):
            best = cand
    return best


def _save_stats(name, payload):
    """Write the stats copy to the first STATS_DIRS folder that accepts it (group-readable); silent on failure."""
    for d in STATS_DIRS:
        try:
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, name)
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w") as f:
                json.dump(payload, f)
            os.chmod(tmp, 0o664)
            os.replace(tmp, path)
            return path
        except OSError:
            continue
    return None


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
    data = fs_stats(fs, group)
    for e in data["stats"]:
        loc = e.get("location", "")
        if e.get("uid") == "root":
            continue
        # robinhood lists /nearline files that were never archived under "On disk"; diskusage_rbh leaves those out
        if data.get("source") != "scan" and (loc == "On disk") != (fs == "project"):
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
    out = run_checked("sshare -U -n -P -o Account,NormShares,EffectvUsage,FairShare,LevelFS")
    return [l.split("|") for l in out.splitlines() if l.strip()]


def jobs():
    out = run_checked(f"squeue -u {USER} -h -o '%i|%j|%T|%M|%l|%D|%C|%m|%a|%R'")
    return [l.split("|") for l in out.splitlines() if l.strip()]


def recent(days, accounts=None):
    who = f"-a -A {','.join(sorted(set(accounts)))}" if accounts else f"-u {USER}"
    out = run_checked(f"sacct {who} -S now-{days}days -X -n -P -o JobID,JobName,State,Elapsed,Timelimit,ReqMem,ExitCode,Account,End,User", 120)
    return [l.split("|") for l in out.splitlines() if l.strip()]


def nodes():
    """Cores by node type: {type: [alloc, idle, other, total, n_nodes]}."""
    seen, out = set(), {}
    for line in run_checked("sinfo -h -N -o '%N|%m|%G|%C'").splitlines():
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
    out = run_checked(f"squeue -u {USER} --start -h -o '%i|%j|%a|%C|%r|%S'")
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
    out = run_checked(f"sacct -u {USER} -S now-{days}days -n -P -o JobID,JobName,State,Elapsed,TotalCPU,AllocCPUS,ReqMem,MaxRSS,Account", 180)
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


def running_now(accounts):
    """Cores and GPUs allocated right now to running jobs of these accounts: {"jobs", "gpu_jobs", "cpus", "gpus"}."""
    out = {"jobs": 0, "gpu_jobs": 0, "cpus": 0, "gpus": 0}
    for line in run_checked(f"squeue -h -A {','.join(accounts)} -t RUNNING -O tres-alloc:300", 60).splitlines():
        if not line.strip():
            continue
        tres = dict(kv.split("=", 1) for kv in line.split(",") if "=" in kv)
        cpus = int(tres.get("cpu", "0") or 0)
        gpus = int(tres.get("gres/gpu", "0") or 0)
        out["jobs"] += 1
        out["gpu_jobs"] += 1 if gpus else 0
        out["cpus"] += cpus
        out["gpus"] += gpus
    return out


def queue_wait(accounts, days):
    """[(start date, wait seconds)] for jobs on the accounts. The date is the job's start date; pending jobs are
    excluded. sacct -S also includes jobs that started before the window, so callers bucket by start date.
    A failing or timed-out sacct raises RuntimeError (run_checked) so it is never mistaken for 'no jobs'."""
    if not accounts:
        return []
    return parse_queue_wait(run_checked(f"SLURM_TIME_FORMAT=standard sacct -a -A {','.join(sorted(set(accounts)))} -S now-{days}days -X -n -P -o Submit,Start,State", 180))


MYFILES = os.path.join(os.path.expanduser("~"), ".firdash", "myfiles.json")
SIZE_LIMIT = 100_000      # stat sizes only up to this many files of mine; above it the scan reports counts only
SCAN = threading.Event()  # set while scan_my_files() is running


def my_files_cached():
    """Result of the last scan_my_files() run ({"time", "total", "rows": [[folder, files, bytes|None]]}), or None."""
    try:
        with open(MYFILES) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def scan_my_files(groups, workers=6):
    """Walk /project/<group> for files OWNED by the user, wherever they are, with one `lfs find` per top-level
    folder in parallel, and save counts (and sizes, up to SIZE_LIMIT files) per second-level folder to MYFILES.
    Takes minutes on a big tree and loads the metadata servers: run it in a background thread on request,
    never per page view."""
    SCAN.set()
    try:
        jobs = []
        for g in groups:
            base = os.path.realpath(f"/project/{g}")
            if not os.path.isdir(base):
                continue
            jobs.append((g, base, None))
            try:
                # symlinks are skipped: /project/<group>/<group> -> the group dir itself would double every count
                jobs += [(g, base, t) for t in sorted(os.listdir(base))
                         if os.path.isdir(os.path.join(base, t)) and not os.path.islink(os.path.join(base, t))]
            except OSError:
                continue
        agg, total, oversize = {}, 0, False
        with concurrent.futures.ThreadPoolExecutor(max(workers, 1)) as ex:
            for g, base, paths in ex.map(lambda j: (j[0], j[1], _find_mine(j[1], j[2])), jobs):
                for path in paths:
                    parts = path[len(base) + 1:].split("/")
                    key = "/".join([g] + parts[:-1][:2])
                    d = agg.setdefault(key, {"files": 0, "bytes": 0})
                    d["files"] += 1
                    total += 1
                    if total > SIZE_LIMIT:
                        oversize = True
                    elif not oversize:
                        d["bytes"] += _size(path)
        rows = [[k, v["files"], None if oversize else v["bytes"]] for k, v in agg.items()]
        data = {"time": time.strftime("%Y-%m-%d %H:%M"), "total": total,
                "rows": sorted(rows, key=lambda r: (-(r[2] or 0), -r[1], r[0]))}
        os.makedirs(os.path.dirname(MYFILES), exist_ok=True)
        tmp = MYFILES + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, MYFILES)
        return data
    finally:
        SCAN.clear()


def _find_mine(base, top):
    """Paths of the user's files under base/top (top=None: files directly in base)."""
    where = f"'{base}' --maxdepth 1" if top is None else f"'{base}/{top}'"
    return [l for l in run(f"lfs find {where} -type f -user {USER} 2>/dev/null", 3600).splitlines() if l]


def _size(path):
    try:
        return os.lstat(path).st_size
    except OSError:
        return 0

