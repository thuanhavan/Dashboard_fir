# fir_interface

Dark-theme web dashboard for resource limits on the Alliance **Fir** cluster. Standard library only, so no install is needed.

Pages (sidebar): **Overview**, **Storage**, **Compute**, **Jobs**. Sidebar filters: groups, filesystems, job-history window.

Shows:
- Storage quotas (space + file count) for every def-/rrg- group you belong to, plus /home and /scratch
- /project and /nearline usage per user (tape-only hatched)
- Cluster cores busy/idle by node type, and fairshare per account
- Job tiles; queue and problem-job tables are collapsed

Data comes from `diskusage_report`, `lfs quota` (per-group project ID; `df` misreports /nearline file counts), `diskusage_rbh`, `sinfo`, `squeue`, `sshare` and `sacct`. Storage refreshes every 10 min, everything else every 60 s.

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

The page asks for a password. It is generated on first start and kept in `~/.config/firdash/password` (mode 600); change it by editing that file (read at every sign-in, no restart needed) or by setting `FIRDASH_PASSWORD` before starting. `/logout` signs out.

Reload the page to refresh (results are cached for 60 s).

## Layout

![data flow](docs/architecture.svg)

```
bin/firdash            launcher (symlink into ~/bin to put it on PATH)
firdash/collectors.py  runs the cluster commands and parses their output
firdash/charts.py      SVG bar / column charts
firdash/render.py      pages, KPI tiles, layout
firdash/server.py      CLI + HTTP server
```
