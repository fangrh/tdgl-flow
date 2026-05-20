import marimo

__generated_with = "0.23.5"
app = marimo.App(width="medium", sql_output="native")


@app.cell
def _():
    import marimo as mo
    import numpy as np
    import json
    import uuid
    import httpx
    import boto3
    import tarfile
    import io

    from hera.workflows import Workflow, WorkflowsService, Parameter
    from hera.workflows.models import WorkflowTemplateRef as WTR


    return (
        Parameter,
        WTR,
        Workflow,
        WorkflowsService,
        boto3,
        httpx,
        io,
        json,
        mo,
        np,
        tarfile,
        uuid,
    )


@app.cell
def _(mo):
    mo.md("""
    # Rectangle Device Builder
    """)
    return


@app.cell(hide_code=True)
def connections(WorkflowsService, boto3, mo):
    gateway = "http://localhost:30080"
    argo_svc = WorkflowsService(
        host=gateway,
        verify_ssl=False,
        namespace="tdgl",
    )
    minio = boto3.client(
        "s3",
        endpoint_url="http://localhost:30900",
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin123",
        region_name="us-east-1",
    )
    mo.md(
        f"Gateway: `{gateway}`  —  "
        f"MinIO Console: [`/minio-ui/`]({gateway}/minio-ui/)"
    )
    return argo_svc, minio


@app.cell
def _(mo):
    get_build, set_build = mo.state({"done": False, "mesh": None})
    get_loaded_key, set_loaded_key = mo.state(None)
    return get_build, get_loaded_key, set_build, set_loaded_key


@app.cell
def _(mo):
    auto_delete = mo.ui.switch(label="Auto-delete artifact after plot", value=False)
    auto_delete
    return (auto_delete,)


@app.cell
def _(json, mo):
    from pathlib import Path

    _pf = Path("/tmp/device_params.json")
    _d = {}
    if _pf.exists():
        try:
            _d = json.loads(_pf.read_text())
        except Exception:
            pass

    film_width = mo.ui.number(step=0.5, value=_d.get("film_width", 10.0))
    film_height = mo.ui.number(step=0.5, value=_d.get("film_height", 2.0))
    elec_width = mo.ui.number(step=0.1, value=_d.get("elec_width", 0.5))
    elec_height = mo.ui.number(step=0.1, value=_d.get("elec_height", 1.0))
    elec_y_offset = mo.ui.number(step=0.1, value=_d.get("elec_y_offset", 0.0))
    max_edge_length = mo.ui.number(step=0.05, value=_d.get("max_edge_length", 0.5))
    smooth = mo.ui.number(step=10, value=_d.get("smooth", 100))
    probe1_x = mo.ui.number(value=_d.get("probe1_x", -3.0))
    probe1_y = mo.ui.number(value=_d.get("probe1_y", 0.0))
    probe2_x = mo.ui.number(value=_d.get("probe2_x", 3.0))
    probe2_y = mo.ui.number(value=_d.get("probe2_y", 0.0))

    parameter_batch = mo.md("""
    ### Device parameters

    | | width | height | y offset |
    |---|---:|---:|---:|
    | film | {film_width} | {film_height} | |
    | electrode | {elec_width} | {elec_height} | {elec_y_offset} |

    | | value |
    |---|---:|
    | max edge length | {max_edge_length} |
    | smooth | {smooth} |

    | | x | y |
    |---|---:|---:|
    | probe 1 | {probe1_x} | {probe1_y} |
    | probe 2 | {probe2_x} | {probe2_y} |
    """).batch(
        film_width=film_width, film_height=film_height,
        elec_width=elec_width, elec_height=elec_height,
        elec_y_offset=elec_y_offset,
        max_edge_length=max_edge_length, smooth=smooth,
        probe1_x=probe1_x, probe1_y=probe1_y,
        probe2_x=probe2_x, probe2_y=probe2_y,
    )

    parameter_batch
    return (
        elec_height,
        elec_width,
        elec_y_offset,
        film_height,
        film_width,
        max_edge_length,
        probe1_x,
        probe1_y,
        probe2_x,
        probe2_y,
        smooth,
    )


@app.cell(hide_code=True)
def wf_state(mo):
    wf_name, set_wf_name = mo.state(None)
    wf_run_id, set_wf_run_id = mo.state(None)
    return set_wf_name, set_wf_run_id, wf_name, wf_run_id


@app.cell(hide_code=True)
def build_stop_ui(get_build, get_loaded_key, mo, wf_name):
    build_btn = mo.ui.button(label="Build through Argo", on_click=lambda v: (v or 0) + 1)
    stop_btn = mo.ui.button(label="Stop", on_click=lambda v: (v or 0) + 1)

    _current_wf = wf_name()
    _cached = get_build()
    _loaded = get_loaded_key()
    _is_running = _current_wf is not None and not _cached["done"] and _loaded is None

    if _is_running:
        build_stop_ui = mo.hstack([stop_btn, mo.md(f"Running: `{_current_wf}`")])
    else:
        build_stop_ui = build_btn

    build_stop_ui
    return build_btn, stop_btn


@app.cell(hide_code=True)
def build_stop_handler(
    Parameter,
    WTR,
    Workflow,
    argo_svc,
    build_btn,
    del_rev,
    elec_height,
    elec_width,
    elec_y_offset,
    film_height,
    film_width,
    json,
    max_edge_length,
    minio,
    mo,
    probe1_x,
    probe1_y,
    probe2_x,
    probe2_y,
    set_build,
    set_del_rev,
    set_loaded_key,
    set_wf_name,
    set_wf_run_id,
    smooth,
    stop_btn,
    uuid,
    wf_name,
    wf_run_id,
):
    submitted_wf = wf_name()
    submitted_run_id = wf_run_id()

    _click_state, _set_click_state = mo.state({"build": 0, "stop": 0})
    _prev = _click_state()

    _cur_build = build_btn.value or 0
    _cur_stop = stop_btn.value or 0

    build_status = mo.md("")

    # Handle Build
    if _cur_build > _prev["build"]:
        submitted_params = {
            "film_width": film_width.value,
            "film_height": film_height.value,
            "elec_width": elec_width.value,
            "elec_height": elec_height.value,
            "elec_y_offset": elec_y_offset.value,
            "probe_points": [
                [probe1_x.value, probe1_y.value],
                [probe2_x.value, probe2_y.value],
            ],
            "max_edge_length": max_edge_length.value,
            "smooth": int(smooth.value),
        }
        try:
            with open("/tmp/device_params.json", "w") as _f:
                _f.write(json.dumps(submitted_params))
        except Exception:
            pass

        _run_id = str(uuid.uuid4())
        _wf = Workflow(
            generate_name="rect-device-",
            namespace="tdgl",
            workflow_template_ref=WTR(name="rectangle-device-builder"),
            arguments=[
                Parameter(name="run-id", value=_run_id),
                Parameter(name="device-params-json", value=json.dumps(submitted_params)),
                Parameter(name="image", value="ghcr.io/fangrh/py-tdgl-runner:6262e5a"),
            ],
            workflows_service=argo_svc,
        )
        try:
            _created = _wf.create()
            _wf_name = _created.metadata.name
            set_wf_name(_wf_name)
            set_wf_run_id(_run_id)
            set_build({"done": False, "mesh": None})
            set_loaded_key(None)
            submitted_wf = _wf_name
            submitted_run_id = _run_id
            build_status = mo.md(f"Submitted: `{_wf_name}`  \nRun ID: `{_run_id}`")
        except Exception as e:
            build_status = mo.md(f"**Submit failed**: `{type(e).__name__}: {e}`")

    # Handle Stop
    if _cur_stop > _prev["stop"]:
        import subprocess
        _msg_parts = []
        if submitted_wf is not None:
            try:
                subprocess.run(
                    ["kubectl", "delete", "workflow", str(submitted_wf), "-n", "tdgl"],
                    capture_output=True, text=True, timeout=30,
                )
                _msg_parts.append(f"Stopped `{submitted_wf}`")
            except Exception as e:
                _msg_parts.append(f"Stop error: {e}")
        if submitted_run_id is not None:
            try:
                minio.delete_object(
                    Bucket="argo-artifacts",
                    Key=f"{submitted_run_id}/mesh_result.json",
                )
                _msg_parts.append("Cleaned up partial data")
            except Exception:
                pass
        set_build({"done": True, "mesh": None})
        set_wf_name(None)
        set_wf_run_id(None)
        set_del_rev(del_rev() + 1)
        submitted_wf = None
        submitted_run_id = None
        build_status = mo.md(" / ".join(_msg_parts)) if _msg_parts else mo.md("Stopped")

    _set_click_state({"build": _cur_build, "stop": _cur_stop})

    build_status
    return submitted_run_id, submitted_wf


@app.cell
def _(get_build, get_loaded_key, mo, submitted_wf):
    _cached = get_build()
    _loaded_key = get_loaded_key()
    if submitted_wf is not None and not _cached["done"] and _loaded_key is None:
        argo_poll = mo.ui.refresh(options=[2], default_interval=2, label="Polling...")
    else:
        argo_poll = mo.md("")
    argo_poll
    return (argo_poll,)


@app.cell
def _(
    argo_poll,
    argo_svc,
    auto_delete,
    del_rev,
    get_build,
    get_loaded_key,
    httpx,
    io,
    json,
    minio,
    mo,
    set_build,
    set_del_rev,
    submitted_run_id,
    submitted_wf,
    tarfile,
):
    def _workflow_phase(name):
        url = f"{argo_svc.host}/api/v1/workflows/tdgl/{name}"
        resp = httpx.get(url, verify=False, timeout=10)
        resp.raise_for_status()
        wf = resp.json()
        return (wf.get("status") or {}).get("phase") or "Unknown"

    def _read_artifact(run_id):
        key = f"{run_id}/mesh_result.json"
        try:
            resp = minio.get_object(Bucket="argo-artifacts", Key=key)
            raw = resp["Body"].read()
        except Exception:
            return None
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            for member in tar.getmembers():
                f = tar.extractfile(member)
                if f:
                    return json.loads(f.read())
        return None

    def _list_artifacts():
        resp = minio.list_objects_v2(Bucket="argo-artifacts")
        return [obj["Key"] for obj in resp.get("Contents", [])]

    def _fetch_logs(workflow_name):
        import subprocess
        try:
            r = subprocess.run(
                ["kubectl", "logs", "-n", "tdgl",
                 "-l", f"workflows.argoproj.io/workflow={workflow_name}",
                 "--tail=30", "--prefix"],
                capture_output=True, text=True, timeout=10,
            )
            return r.stdout.strip()
        except Exception:
            return ""

    # Consume refresh tick
    try:
        _ = argo_poll.value
    except Exception:
        pass

    # Check cached result
    _cached = get_build()
    minio_mesh_result = None

    if _cached["mesh"] is not None:
        minio_mesh_result = _cached["mesh"]
        workflow_status = mo.md(
            f"**Succeeded** — "
            f"Sites: {minio_mesh_result['num_sites']}, "
            f"Elements: {minio_mesh_result['num_elements']}"
        )
    elif submitted_wf is not None and get_loaded_key() is None:
        # Active build — poll Argo
        try:
            _phase = _workflow_phase(str(submitted_wf))
            if _phase == "Succeeded":
                minio_mesh_result = _read_artifact(str(submitted_run_id))
                if minio_mesh_result:
                    set_build({"done": True, "mesh": minio_mesh_result})
                    # Refresh artifact table to show new artifact
                    set_del_rev(del_rev() + 1)
                    if auto_delete.value:
                        minio.delete_object(
                            Bucket="argo-artifacts",
                            Key=f"{submitted_run_id}/mesh_result.json",
                        )
                workflow_status = mo.md(
                    f"`{submitted_wf}` **succeeded** — "
                    f"Sites: {minio_mesh_result.get('num_sites')}, "
                    f"Elements: {minio_mesh_result.get('num_elements')}"
                ) if minio_mesh_result else mo.md("Succeeded but artifact not in MinIO yet.")
            elif _phase in {"Failed", "Error"}:
                set_build({"done": True, "mesh": None})
                _logs = _fetch_logs(str(submitted_wf))
                _log_block = f"\n\n```\n{_logs}\n```" if _logs else ""
                workflow_status = mo.md(f"`{submitted_wf}` **{_phase}**{_log_block}")
            else:
                _hint = {"Submitted": "Scheduling...", "Pending": "Pulling image...",
                         "Running": "Computing mesh..."}.get(_phase, "Processing...")
                _logs = _fetch_logs(str(submitted_wf))
                _log_block = f"\n\n```\n{_logs}\n```" if _logs else ""
                workflow_status = mo.md(f"`{submitted_wf}` **{_phase}** — {_hint}{_log_block}")
        except Exception as e:
            workflow_status = mo.md(f"Query error: `{type(e).__name__}: {e}`")
    else:
        # Idle — list existing artifacts
        _keys = _list_artifacts()
        if _keys:
            workflow_status = mo.md(
                "MinIO artifacts:\n"
                + "\n".join(f"- `{k}`" for k in _keys)
            )
        else:
            workflow_status = mo.md("No workflow submitted. MinIO bucket is empty.")

    workflow_status
    return (minio_mesh_result,)


@app.cell
def _(minio_mesh_result, mo, np):
    mesh_result = minio_mesh_result

    if mesh_result is None:
        sites = np.empty((0, 2))
        elements = np.empty((0, 3), dtype=int)
        terminals = []
        probes = []
        mesh_summary = mo.md("")
    else:
        sites = np.array(mesh_result["sites"])
        elements = np.array(mesh_result["elements"])
        terminals = mesh_result.get("terminals", [])
        probes = mesh_result.get("probe_indices", [])
        mesh_summary = mo.md(
            f"### Mesh\n"
            f"- **Sites**: {mesh_result['num_sites']}  \n"
            f"- **Elements**: {mesh_result['num_elements']}  \n"
            f"- **Film**: {mesh_result.get('film_width')} x {mesh_result.get('film_height')}  \n"
            f"- **Probes**: {probes}"
        )
    mesh_summary
    return elements, probes, sites, terminals


@app.cell
def _(elements, mo, probes, sites, terminals):
    import plotly.graph_objects as go

    if len(sites) == 0:
        mesh_plot = mo.md("")
    else:
        _mx, _my = [], []
        for _tri in elements:
            for _j in range(3):
                _p0, _p1 = sites[_tri[_j]], sites[_tri[(_j+1)%3]]
                _mx += [_p0[0], _p1[0], None]
                _my += [_p0[1], _p1[1], None]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=_mx, y=_my, mode="lines",
            line=dict(width=0.3, color="#94a3b8"),
            hoverinfo="skip", showlegend=False,
        ))

        _ec = {"source": ("#2563eb", "rgba(37,99,235,0.35)"),
               "drain": ("#dc2626", "rgba(220,38,38,0.35)")}
        for _t in terminals:
            _idx = _t["site_indices"]
            _x0, _x1 = sites[_idx,0].min(), sites[_idx,0].max()
            _y0, _y1 = sites[_idx,1].min(), sites[_idx,1].max()
            _pad = 0.15
            _lc, _fc = _ec.get(_t["name"], ("#888", "rgba(136,136,136,0.35)"))
            fig.add_trace(go.Scatter(
                x=[_x0-_pad, _x1+_pad, _x1+_pad, _x0-_pad, _x0-_pad],
                y=[_y0-_pad, _y0-_pad, _y1+_pad, _y1+_pad, _y0-_pad],
                mode="lines", line=dict(width=1.5, color=_lc), name=_t["name"],
                fill="toself", fillcolor=_fc,
            ))

        fig.add_trace(go.Scatter(
            x=sites[probes,0], y=sites[probes,1],
            mode="markers+text",
            marker=dict(size=8, symbol="x", color="#16a34a", line_width=2),
            text=[f"P{i+1}" for i in range(len(probes))],
            textposition="top center", name="probes",
        ))

        _xmin, _xmax = sites[:,0].min(), sites[:,0].max()
        _ymin, _ymax = sites[:,1].min(), sites[:,1].max()
        _m = 0.3
        fig.update_layout(
            title=f"Device ({len(sites)} sites, {len(elements)} elements)",
            xaxis=dict(range=[_xmin-_m, _xmax+_m],
                       showline=True, linewidth=1, linecolor="black",
                       mirror=True, ticks="outside"),
            yaxis=dict(scaleanchor="x", scaleratio=1,
                       range=[_ymin-_m, _ymax+_m],
                       showline=True, linewidth=1, linecolor="black",
                       mirror=True, ticks="outside"),
            legend=dict(orientation="h", yanchor="bottom", y=-0.25,
                        xanchor="center", x=0.5),
            margin=dict(l=40, r=10, t=35, b=50),
            height=280, width=700, plot_bgcolor="white",
        )
        mesh_plot = fig
    mesh_plot
    return


@app.cell(hide_code=True)
def del_state(mo):
    del_rev, set_del_rev = mo.state(0)
    return del_rev, set_del_rev


@app.cell
def _(del_rev, minio, mo):
    rev_val = del_rev()

    refresh_btn = mo.ui.button(label="Refresh", on_click=lambda v: (v or 0) + 1)

    resp = minio.list_objects_v2(Bucket="argo-artifacts")
    items = resp.get("Contents", [])

    artifact_table = None
    delete_btn = mo.ui.button(label="Delete", on_click=lambda v: (v or 0) + 1)
    load_btn = mo.ui.button(label="Load", on_click=lambda v: (v or 0) + 1)

    if not items:
        artifact_manager_ui = mo.md("### Artifacts\nNo artifacts in MinIO bucket. Submit a workflow to generate one.")
    else:
        data = [
            {"artifact": obj["Key"], "size": f"{obj['Size']/1024:.1f} KB"}
            for obj in items
        ]
        artifact_table = mo.ui.table(data=data, selection="single")
        artifact_manager_ui = mo.vstack([
            artifact_table,
            mo.hstack([load_btn, delete_btn, refresh_btn]),
        ])

    artifact_manager_ui
    return artifact_table, delete_btn, load_btn, refresh_btn


@app.cell(hide_code=True)
def artifact_actions(
    artifact_table,
    del_rev,
    delete_btn,
    io,
    json,
    load_btn,
    minio,
    mo,
    refresh_btn,
    set_build,
    set_del_rev,
    set_loaded_key,
    tarfile,
):
    _click_state, _set_click_state = mo.state({"load": 0, "delete": 0, "refresh": 0})
    _prev = _click_state()

    _cur_load = load_btn.value or 0
    _cur_del = delete_btn.value or 0
    _cur_refresh = refresh_btn.value or 0

    _selected = None
    _rows = (artifact_table.value or []) if artifact_table else []
    if _rows:
        _selected = _rows[0].get("artifact")

    _action_msg = ""

    if _cur_load > _prev["load"] and _selected:
        try:
            r = minio.get_object(Bucket="argo-artifacts", Key=_selected)
            raw = r["Body"].read()
            mesh_data = None
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
                for member in tar.getmembers():
                    f = tar.extractfile(member)
                    if f:
                        mesh_data = json.loads(f.read())
                        break
            if mesh_data:
                set_build({"done": True, "mesh": mesh_data})
                _key = _selected.rstrip("/mesh_result.json").split("/")[-1] \
                    if "/mesh_result.json" in _selected else _selected
                set_loaded_key(_key)
                _action_msg = f"Loaded `{_selected}`"
            else:
                _action_msg = f"Artifact `{_selected}` has no mesh data"
        except Exception as e:
            _action_msg = f"Load failed: {e}"

    if _cur_del > _prev["delete"] and _selected:
        try:
            minio.delete_object(Bucket="argo-artifacts", Key=_selected)
            _action_msg = f"Deleted `{_selected}`"
            set_del_rev(del_rev() + 1)
        except Exception as e:
            _action_msg = f"Delete failed: {e}"

    if _cur_refresh > _prev["refresh"]:
        set_del_rev(del_rev() + 1)
        _action_msg = "Refreshed"

    _set_click_state({"load": _cur_load, "delete": _cur_del, "refresh": _cur_refresh})

    mo.md(_action_msg) if _action_msg else mo.md("")
    return


if __name__ == "__main__":
    app.run()
