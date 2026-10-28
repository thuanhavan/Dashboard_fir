# fir_interface

Lightweight web dashboard for resource limits on the Alliance **Fir** cluster. Standard library only, so no install is needed.

Shows:
- Storage quotas (space + file count) for every def-/rrg- group you belong to, plus /home and /scratch
- /project and /nearline usage per user (tape-only hatched)
- Cluster cores busy/idle by node type, and fairshare per account
- Job tiles; queue and problem-job tables are collapsed

Data comes from `diskusage_report`, `df`, `diskusage_rbh`, `sinfo`, `squeue`, `sshare` and `sacct`. Storage refreshes every 10 min, everything else every 60 s.

## Usage

```bash
bin/firdash                      # start server on 127.0.0.1, prints port + tunnel command
bin/firdash --days 30            # longer sacct history
bin/firdash --port 8765          # fixed port
bin/firdash --html report.html   # static snapshot, no server
python -m firdash                # same as bin/firdash
```

Open the page by forwarding the printed port:
- **VS Code Remote**: PORTS tab, forward the port, then click the link.
- **SSH**: `ssh -N -L PORT:localhost:PORT -J USER@fir.alliancecan.ca USER@loginN` (use the login node the dashboard is running on).

Reload the page to refresh (results are cached for 60 s).

## Layout

![data flow](docs/architecture.svg)

```
bin/firdash            launcher (symlink into ~/bin to put it on PATH)
firdash/collectors.py  runs the cluster commands and parses their output
firdash/render.py      builds the HTML
firdash/server.py      CLI + HTTP server
```
