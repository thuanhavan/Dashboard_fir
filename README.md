# fir_interface

Dark-theme web dashboard for resource limits on the Alliance **Fir** cluster. Standard library only, so no install is needed.

Pages (sidebar): **Overview**, **Storage**, **Compute**, **Jobs**. Sidebar filters: groups, filesystems, job-history window.

Shows:
- Storage quotas (space + file count) for every def-/rrg- group you belong to, plus /home and /scratch
- /project and /nearline usage per user (tape-only hatched)
- Cluster cores busy/idle by node type, and fairshare per account
- Job tiles; queue and problem-job tables are collapsed
- Whole-filesystem fill (all users): /home + /project + /scratch share one Lustre FS; /nearline disk cache
- Usage trend and "full in N days" forecast per quota (hourly snapshots in `~/.firdash/history.jsonl`; trends appear after a day)
- Array-job progress (completed / running / pending / failed per array)
- Pending jobs with Slurm's expected start time and the reason in plain words
- Job efficiency: peak memory vs requested and CPU efficiency per job name, idle core-hours
- Maintenance windows and reservations

Compute-page banner `docs/h100.jpg`: NVIDIA H100 illustration from
[tatacommunications.com](https://www.tatacommunications.com/knowledge-base/cloud-ai/nvidia-h100-gpu), third-party
image kept for internal lab use only; replace it before sharing the dashboard publicly.

Data comes from `diskusage_report`, `lfs quota` (per-group project ID; `df` misreports /nearline file counts), the nightly robinhood stats behind `diskusage_rbh` (`/project/.stats`, `/nearline/.stats`), `sinfo`, `squeue`, `sshare` and `sacct`. Storage refreshes every 10 min, everything else every 60 s.

## For lab members

Everyone runs it under their own account and sees their own quotas, jobs and fairshare, plus the per-user
storage of the groups they belong to. The shared copy for **def-kanj241** members is
`/project/def-kanj241/A01_GAIG_data_repo/C01_pipelines/fir-dashboard`.

**Web link:** open <https://fir.gaig.ca> and click **Open dashboard**. It signs in with your Fir account + Duo and
starts a small JupyterHub session (1 core, 2 GB, 1 h, `def-kanj241_cpu`; no Server Options form); the dashboard
opens by itself in 30–60 s. The session ends after an hour; if one is already running it is reused.

One-time setup, on Fir (SSH, or a Terminal in JupyterLab):

```bash
/project/def-kanj241/A01_GAIG_data_repo/C01_pipelines/fir-dashboard/bin/firdash-jupyter-setup
```

then stop any running JupyterHub server once (File → Hub Control Panel → Stop My Server). It adds a "Fir dashboard"
launcher to your own sessions, keeps the Alliance launchers, never overwrites a `~/.jupyter/jupyter_server_config.py`
it did not write, and is undone with `--remove`. Without it the button ends in "404 Not Found".

Short link: <https://fir.gaig.ca/direct>. After the click the address bar shows jupyterhub.fir.alliancecan.ca: the
dashboard runs there, and Fir JupyterHub cannot be embedded in or proxied through another site. fir.gaig.ca is a static
page on the gaig VM (nginx, `/var/www/fir-gaig`, `/etc/nginx/sites-available/fir-redirect`); it holds no data.

JupyterHub sessions run on compute nodes, where /nearline is not mounted. The dashboard then uses the copy of the
nightly /nearline stats that a login-node run leaves in `~/.firdash/stats/` (the /nearline card says "login-node copy
of <date>"); run `bin/firdash --html /dev/null` on a login node now and then to refresh it.

## Usage

```bash
bin/firdash                      # default: private socket ~/.firdash/dash.sock (only you can open it)
bin/firdash --port 8765          # alternative: 127.0.0.1:8765 with a password page
bin/firdash --days 30            # default job-history window
bin/firdash --html report.html   # static snapshot, no server
```

**Socket mode (default).** No password: the socket file is mode 600, so other users on the login node
cannot connect, and the only way in from your PC is an SSH tunnel, which needs your Duo login:

```bash
ssh -N -L 18765:/home/$USER/.firdash/dash.sock $USER@login1.fir.alliancecan.ca   # use the node it runs on
```

then open http://localhost:18765. Pick any local port Windows allows (8765 can be reserved on Windows/WSL).

**Port mode (`--port`).** Works with the VS Code PORTS tab. The page asks for a password, generated on first
start in `~/.config/firdash/password` (mode 600); edit that file to change it (read at every sign-in) or set
`FIRDASH_PASSWORD` before starting. `/logout` signs out.

Reload the page to refresh (storage is cached 10 min, everything else 60 s).

## Layout

![data flow](docs/architecture.svg)

```
bin/firdash            launcher (symlink into ~/bin to put it on PATH)
firdash/collectors.py  runs the cluster commands and parses their output
firdash/charts.py      SVG bar / column charts
firdash/render.py      pages, KPI tiles, layout
firdash/server.py      CLI + HTTP server
```
