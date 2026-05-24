# Notebooks

This directory keeps checked-in notebooks and notebook-style scripts that are useful for TDGL development and demos.

## Main notebooks

- `e2e_sim_test.py`: end-to-end py-tdgl workflow submission, live MinIO viewer, verification, and static playback checks.
- `010-minio-run-viewer.ipynb`: MinIO-backed run viewer.
- `device_builder.ipynb` / `device_builder.py`: device construction workflow.
- `timing_sim.ipynb`: timing schedule exploration.
- `local_tdgl_sim.ipynb`: local TDGL simulation exploration.
- `tdgl_demo.ipynb`: compact TDGL demo.
- `frame_player.ipynb`: frame playback checks.
- `test_heatmap_viewer.ipynb`: heatmap viewer experiment.
- `argo_workflow_demo.ipynb` / `argo_workflow_demo.py`: Argo workflow demo.
- `run_tdgl_sim.py`: simple script entry point for simulation runs.

## Local-only folders

The following folders are intentionally ignored by git:

- `artifacts/`: generated HDF5 files, HTML players, downloaded outputs.
- `experimental/`: exploratory notebooks that are not part of the stable workflow.
- `scratch/`: one-off helper scripts and temporary diagnostics.

Jupyter, Marimo, Python cache, and ystore files are also ignored.
