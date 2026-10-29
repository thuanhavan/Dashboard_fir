import os
import re
import subprocess

USER = os.environ.get("USER", "")
UNITS = {"B": 1, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40, "PiB": 2**50}


def run(cmd, timeout=90):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return ""


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


def group_quotas():
    rows = []
    for g in groups():
        for fs in ("project", "nearline"):
            path = os.path.realpath(f"/{fs}/{g}")
            if not os.path.isdir(path):
                continue
            pid = run(f"lfs project -d {path}", 30).split()
            if not pid or not pid[0].isdigit() or pid[0] == "0":
                continue
            q = run(f"lfs quota -q -p {pid[0]} /{fs}", 30).replace("*", "").split()
            if len(q) < 8:
                continue
            rows.append({"label": f"{g} /{fs}", "used": float(q[1]) * 1024, "quota": float(q[3]) * 1024,
                         "files": float(q[5]), "fquota": float(q[7])})
    return rows


def user_breakdown(fs, group):
    """{user: {"disk": bytes, "tape": bytes}} from diskusage_rbh."""
    users = {}
    for line in run(f"diskusage_rbh {fs} {group} --all_users", 120).splitlines():
        m = re.match(r"\s*(\S+)\s+(\d+)\s+([\d.]+) GiB\s+(.+?)\s*$", line)
        if not m or m.group(1) == "Total":
            continue
        u, _, gib, loc = m.groups()
        d = users.setdefault(u, {"disk": 0.0, "tape": 0.0})
        d["tape" if loc == "On tape" else "disk"] += float(gib) * 2**30
    return {u: v for u, v in users.items() if v["disk"] + v["tape"] > 0}


def fairshare():
    out = run("sshare -U -n -P -o Account,NormShares,EffectvUsage,FairShare,LevelFS")
    return [l.split("|") for l in out.splitlines() if l.strip()]


def jobs():
    out = run(f"squeue -u {USER} -h -o '%i|%j|%T|%M|%l|%D|%C|%m|%a|%R'")
    return [l.split("|") for l in out.splitlines() if l.strip()]


def group_jobs(accounts):
    if not accounts:
        return []
    out = run(f"squeue -A {','.join(sorted(set(accounts)))} -h -o '%i|%u|%j|%T|%M|%l|%C|%a|%R'")
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


def filesystems():
    """Whole filesystems (all users): one row per distinct device. /home, /project and /scratch share one Lustre FS."""
    space, inodes, rows, seen = {}, {}, [], {}
    for cmd, d in (("df -P -B1 /home /project /scratch /nearline", space), ("df -Pi /home /project /scratch /nearline", inodes)):
        for line in run(cmd, 30).splitlines()[1:]:
            p = line.split()
            if len(p) >= 6 and p[1].isdigit():
                d[p[5]] = (p[0], int(p[2]) + int(p[3]), int(p[2]))  # used + available, as df's Capacity (excludes reserved)
    for mnt, (dev, total, used) in space.items():
        if (dev.rsplit("/", 1)[0], total) in seen:
            seen[(dev.rsplit("/", 1)[0], total)]["mounts"].append(mnt)
            continue
        ftot, fused = inodes.get(mnt, ("", 0, 0))[1:]
        row = {"mounts": [mnt], "used": float(used), "quota": float(total), "files": float(fused), "fquota": float(ftot)}
        seen[(dev.rsplit("/", 1)[0], total)] = row
        rows.append(row)
    for r in rows:
        r["label"] = " + ".join(r.pop("mounts")) + (" (disk cache)" if "/nearline" in r.get("label", "") else "")
        if r["label"] == "/nearline":
            r["label"] = "/nearline disk cache"
    return rows


def pending_start():
    """Pending jobs with Slurm's expected start time and reason."""
    out = run(f"squeue -u {USER} --start -h -o '%i|%j|%a|%C|%r|%S'")
    return [l.split("|") for l in out.splitlines() if l.strip()]


def _ntasks(spec):
    n = 0
    for part in spec.split("%")[0].split(","):
        a, _, b = part.partition("-")
        n += int(b) - int(a) + 1 if b.isdigit() and a.isdigit() else 1
    return n


def arrays(days=3):
    """{array id: {"name", "end", counts by state}} for the user's array jobs of the last `days` days."""
    out = run(f"sacct -u {USER} -S now-{days}days -X -n -P -o JobID,JobName,State,End", 120)
    arr = {}
    for line in out.splitlines():
        p = line.split("|")
        if len(p) < 4 or "_" not in p[0]:
            continue
        jid, task = p[0].split("_", 1)
        st = p[2].split()[0]
        d = arr.setdefault(jid, {"name": p[1], "end": "", "COMPLETED": 0, "RUNNING": 0, "PENDING": 0, "BAD": 0})
        n = _ntasks(task.strip("[]")) if task.startswith("[") else 1
        key = st if st in ("COMPLETED", "RUNNING", "PENDING") else "BAD"
        d[key] += n
        if p[3] not in ("Unknown", "") and p[3] > d["end"]:
            d["end"] = p[3]
    return arr


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
        p = line.split("|")
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


def reservations(days=30):
    """Maintenance / reservations ending in the future and starting within `days` days."""
    import datetime as dt
    now = dt.datetime.now()
    rows = []
    for block in run("scontrol show reservation -o", 30).splitlines():
        kv = dict(x.split("=", 1) for x in block.split() if "=" in x)
        try:
            s, e = dt.datetime.fromisoformat(kv["StartTime"]), dt.datetime.fromisoformat(kv["EndTime"])
        except (KeyError, ValueError):
            continue
        if e > now and s < now + dt.timedelta(days=days):
            rows.append([kv.get("ReservationName", ""), kv["StartTime"].replace("T", " "), kv["EndTime"].replace("T", " "),
                         kv.get("NodeCnt", ""), kv.get("CoreCnt", ""), kv.get("Flags", ""), "active" if s <= now else "upcoming"])
    return sorted(rows, key=lambda r: r[1])


def pending_lab(accounts):
    """Pending jobs of all members on the given accounts: [jobid, user, name, account, cpus, reason, start, n_tasks]."""
    if not accounts:
        return []
    out = run(f"squeue -A {','.join(sorted(set(accounts)))} -t PD --start -h -o '%i|%u|%j|%a|%C|%r|%S'")
    rows = []
    for l in out.splitlines():
        p = l.split("|")
        if len(p) < 7:
            continue
        task = p[0].split("_", 1)[1] if "_" in p[0] else ""
        p.append(_ntasks(task.strip("[]")) if task.startswith("[") else 1)
        rows.append(p)
    return rows
