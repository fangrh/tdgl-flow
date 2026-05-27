#!/bin/bash
#SBATCH --job-name=tdgl-sim
#SBATCH --output=%j.out
#SBATCH --error=%j.err
# SBATCH options are overridden by sbatch flags passed from the runner

RUN_ID="$1"
SIDE_CAR_INTERVAL="${2:-500}"

source /scratch/work/fangr1/miniforge3/etc/profile.d/conda.sh
conda activate tdgl

SCRIPT_DIR="/scratch/work/fangr1/tdgl-runner"
python "$SCRIPT_DIR/slurm_runner.py" "$RUN_ID" --sidecar-interval "$SIDE_CAR_INTERVAL"
