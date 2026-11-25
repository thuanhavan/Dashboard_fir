#!/bin/bash
# firdash-push: build a static snapshot of the Fir dashboard on a compute node and push it to the gaig VM,
# then resubmit itself so the snapshot refreshes every INTERVAL. Alliance clusters have no user cron.
#
# One-time setup on Fir (see README, "Published snapshot"):
#   ssh-keygen -t ed25519 -N '' -f ~/.ssh/gaig-push -C firdash-push      # public key goes to the VM
#   mkdir -p ~/firdash-push/log && cp deploy/firdash-push.sbatch ~/firdash-push/
#   sbatch ~/firdash-push/firdash-push.sbatch
# Stop: scancel -n firdash-push          Check: squeue -u $USER -n firdash-push ; tail ~/firdash-push/log/*
#
#SBATCH --job-name=firdash-push
#SBATCH --account=def-kanj241_cpu
#SBATCH --time=00:15:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=2000M
#SBATCH --output=/home/%u/firdash-push/log/%j.out
set -u
INTERVAL=${FIRDASH_PUSH_INTERVAL:-1hour}
SCRIPT="$HOME/firdash-push/firdash-push.sbatch"
FIRDASH_HTML=/project/def-kanj241/hvt632/fir_interface/bin/firdash-html
TARGET=firpush@fir.gaig.ca
KEY="$HOME/.ssh/gaig-push"

# Resubmit first, so a failed push does not break the chain. Keep exactly one pending copy.
if [ "$(squeue -h -u "$USER" -n firdash-push -t PENDING | wc -l)" -eq 0 ]; then
    sbatch --begin="now+$INTERVAL" "$SCRIPT"
fi
find "$HOME/firdash-push/log" -name '*.out' -mtime +14 -delete 2>/dev/null

echo "$(date -Is) building snapshot on $(hostname)"
"$FIRDASH_HTML" > "$SLURM_TMPDIR/fir.html"
ls -l "$SLURM_TMPDIR/fir.html"
ssh -o BatchMode=yes -o ConnectTimeout=30 -o StrictHostKeyChecking=accept-new -i "$KEY" "$TARGET" < "$SLURM_TMPDIR/fir.html"
