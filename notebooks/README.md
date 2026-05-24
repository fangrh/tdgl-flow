# Notebooks

Only the notebooks needed for the py-tdgl workflow are kept in git.

- `e2e_sim_test.py`: main end-to-end test. It submits `py-tdgl-sim`, watches the growing HDF5 in MinIO, and opens the live local viewer.
- `009-native-widget-player.ipynb`: local widget-player experiment for testing the notebook viewer controls.

Device construction is kept as part of the py-tdgl preprocessing path. The main
workflow runs `build_device.py` before timing and simulation, and
`workflows/rectangle-device-builder.yaml` remains available for standalone
device-builder checks.

Generated HDF5 files, downloaded outputs, Jupyter checkpoints, ystore files, Marimo state, and scratch notebooks are local-only and ignored by git.
