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
