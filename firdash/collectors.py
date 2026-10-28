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


def quotas():
    rows = []
    pat = re.compile(r"^\s*(/\S+ \([^)]*\))\s*(?:->)?\s*([\d.]+\s*\w+)/\s*([\d.]+\s*\w+)\s+([\d.]+\s*\w?)\s*/\s*([\d.]+\s*\w?)")
    for line in run("diskusage_report").splitlines():
        m = pat.match(line)
        if not m:
            continue
        name, su, sq, fu, fq = m.groups()
        rows.append({
            "name": name, "space": f"{su.strip()} / {sq.strip()}", "files": f"{fu.strip()} / {fq.strip()}",
            "space_pct": 100 * to_bytes(su) / max(to_bytes(sq), 1),
            "files_pct": 100 * to_count(fu) / max(to_count(fq), 1),
        })
    return rows


def fairshare():
    out = run("sshare -U -n -P -o Account,NormShares,EffectvUsage,FairShare,LevelFS")
    return [l.split("|") for l in out.splitlines() if l.strip()]


def jobs():
    out = run(f"squeue -u {USER} -h -o '%i|%j|%T|%M|%l|%D|%C|%m|%a|%R'")
    return [l.split("|") for l in out.splitlines() if l.strip()]


def recent(days):
    out = run(f"sacct -u {USER} -S now-{days}days -X -n -P -o JobID,JobName,State,Elapsed,Timelimit,ReqMem,ExitCode,Account,End")
    return [l.split("|") for l in out.splitlines() if l.strip()]
