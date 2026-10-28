# fir_interface

Lightweight web dashboard for resource limits on the Alliance **Fir** cluster. Standard library only, so no install is needed.

Shows:
- Storage quotas (space + file count) for /home, /scratch, /project, /nearline, with alerts at ≥90%
- Your SLURM queue, including the reason pending jobs are waiting
- Fairshare per account
- Failed / timed-out / OOM / cancelled jobs from recent history

Data comes from `diskusage_report`, `squeue`, `sshare` and `sacct`.

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

```
bin/firdash            launcher (symlink into ~/bin to put it on PATH)
firdash/collectors.py  runs the cluster commands and parses their output
firdash/render.py      builds the HTML
firdash/server.py      CLI + HTTP server
```
