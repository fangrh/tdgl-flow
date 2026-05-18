import httpx


class TDGLClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def build_device(self, **params) -> dict:
        resp = httpx.post(f"{self.base_url}/api/device/build", json=params, timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    def build_timing(self, **params) -> dict:
        resp = httpx.post(f"{self.base_url}/api/timing/build", json=params, timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    def submit_simulation(self, *, device_params: dict, timing_params: dict,
                          mesh_data: dict, schedule: dict,
                          solver_options: dict | None = None,
                          resources: dict | None = None) -> dict:
        resp = httpx.post(f"{self.base_url}/api/workflows/submit", json={
            "device_params": device_params,
            "timing_params": timing_params,
            "mesh_data": mesh_data,
            "schedule": schedule,
            "solver_options": solver_options or {},
            "resources": resources or {"cpu_cores": 2, "memory_mib": 2048},
        }, timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    def get_run(self, run_id: str) -> dict:
        resp = httpx.get(f"{self.base_url}/api/runs/{run_id}", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def list_runs(self) -> list[dict]:
        resp = httpx.get(f"{self.base_url}/api/runs", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def get_run_status(self, run_id: str) -> str:
        return self.get_run(run_id)["status"]

    def get_mesh(self, run_id: str) -> dict:
        resp = httpx.get(f"{self.base_url}/api/runs/{run_id}/mesh", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def get_frame(self, run_id: str, frame_index: int) -> dict:
        resp = httpx.get(f"{self.base_url}/api/runs/{run_id}/frames/{frame_index}", timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    def preview_device(self, device_result: dict):
        import plotly.graph_objects as go
        import numpy as np

        sites = np.array(device_result["sites"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=sites[:, 0], y=sites[:, 1],
            mode="markers", marker=dict(size=3),
            name="Mesh sites",
        ))
        fig.update_layout(title=f"Device: {device_result['num_sites']} sites")
        return fig

    def preview_timing(self, timing_result: dict):
        import plotly.graph_objects as go

        steps = timing_result["steps"]
        jes = [s["je_end"] for s in steps]
        times = [s["stable_end"] for s in steps]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=times, y=jes,
            mode="lines+markers",
            name="Je sequence",
        ))
        fig.update_layout(
            title=f"Timing: {timing_result['n_steps']} steps",
            xaxis_title="Time (s)",
            yaxis_title="Je (uA)",
        )
        return fig

    def view_results(self, run_id: str):
        import plotly.graph_objects as go
        import numpy as np

        mesh = self.get_mesh(run_id)
        runs = self.list_runs()
        run = next(r for r in runs if r["run_id"] == run_id)
        total = run.get("total_frames", 0)

        frames = []
        for i in range(total):
            try:
                frames.append(self.get_frame(run_id, i))
            except Exception:
                break

        if not frames:
            print("No frames available yet.")
            return None

        sites = np.array(mesh["sites"])
        last = frames[-1]
        pr = np.array(last["arrays"]["psi_real"])
        pi = np.array(last["arrays"]["psi_imag"])
        mu = np.array(last["arrays"]["mu"])
        psq = pr**2 + pi**2

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=sites[:, 0], y=sites[:, 1],
            mode="markers",
            marker=dict(color=psq, colorscale="Viridis", size=5, showscale=True),
            name="|psi|^2",
        ))
        fig.update_layout(title=f"Run {run_id[:8]} - last frame |psi|^2")
        return fig
