import marimo

__generated_with = "0.23.5"
app = marimo.App(width="medium")


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
        host=f"{gateway}/argo",
        verify_ssl=False,
        namespace="tdgl",
    )
    minio = boto3.client(
        "s3",
        endpoint_url="http://localhost:9000",
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin123",
        region_name="us-east-1",
    )
    mo.md(
        f"Gateway: `{gateway}` (Argo + MinIO Console)  \n"
        f"MinIO S3: `localhost:9000` (direct)  \n"
        f"Port-forwards: `30080` + `9000`"
    )
    return argo_svc, minio


@app.cell
def _(mo):

    film_width = mo.ui.number(start=1.0, stop=50.0, step=0.5, value=10.0)
    film_height = mo.ui.number(start=0.5, stop=20.0, step=0.5, value=2.0)
    elec_width = mo.ui.number(start=0.1, stop=5.0, step=0.1, value=0.5)
    elec_height = mo.ui.number(start=0.1, stop=5.0, step=0.1, value=1.0)
    elec_y_offset = mo.ui.number(start=-5.0, stop=5.0, step=0.1, value=0.0)
    max_edge_length = mo.ui.number(start=0.1, stop=2.0, step=0.05, value=0.5)
    smooth = mo.ui.number(start=0, stop=500, step=10, value=100)
    probe1_x = mo.ui.number(value=-3.0)
    probe1_y = mo.ui.number(value=0.0)
    probe2_x = mo.ui.number(value=3.0)
    probe2_y = mo.ui.number(value=0.0)

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
        film_width=film_width,
        film_height=film_height,
        elec_width=elec_width,
        elec_height=elec_height,
        elec_y_offset=elec_y_offset,
        max_edge_length=max_edge_length,
        smooth=smooth,
        probe1_x=probe1_x,
        probe1_y=probe1_y,
        probe2_x=probe2_x,
        probe2_y=probe2_y,
    )
    device_form = parameter_batch.form(submit_button_label="Build through Argo")
    device_form
    return (device_form,)


@app.cell
def _(Parameter, WTR, Workflow, argo_svc, device_form, json, mo, uuid):

    submitted_run_id = None
    submitted_wf = None

    if device_form.value is not None:
        vals = device_form.value
        submitted_params = {
            "film_width": vals["film_width"],
            "film_height": vals["film_height"],
            "elec_width": vals["elec_width"],
            "elec_height": vals["elec_height"],
            "elec_y_offset": vals["elec_y_offset"],
            "probe_points": [
                [vals["probe1_x"], vals["probe1_y"]],
                [vals["probe2_x"], vals["probe2_y"]],
            ],
            "max_edge_length": vals["max_edge_length"],
            "smooth": int(vals["smooth"]),
        }
        submitted_run_id = str(uuid.uuid4())
        _wf = Workflow(
            generate_name="rect-device-",
            namespace="tdgl",
            workflow_template_ref=WTR(name="rectangle-device-builder"),
            arguments=[
                Parameter(name="run-id", value=submitted_run_id),
                Parameter(name="device-params-json", value=json.dumps(submitted_params)),
                Parameter(name="image", value="ghcr.io/fangrh/py-tdgl-runner:6262e5a"),
            ],
            workflows_service=argo_svc,
        )
        try:
            _created = _wf.create()
            submitted_wf = _created.metadata.name
            build_status = mo.md(f"Submitted: `{submitted_wf}`  \nRun ID: `{submitted_run_id}`")
        except Exception as e:
            build_status = mo.md(f"**Submit failed**: `{type(e).__name__}: {e}`")
    else:
        build_status = mo.md("Fill in parameters and click **Build through Argo**.")

    build_status
    return submitted_run_id, submitted_wf


@app.cell
def _(mo):
    argo_refresh = mo.ui.refresh(options=[2], default_interval=2, label="Auto-refresh")
    argo_refresh
    return (argo_refresh,)


@app.cell
def _(
    argo_refresh,
    argo_svc,
    httpx,
    io,
    json,
    minio,
    mo,
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


    def _read_artifact_from_minio(run_id):
        """Read mesh artifact directly from MinIO by run-id."""
        key = f"{run_id}/mesh_result.json"
        try:
            resp = minio.get_object(Bucket="argo-artifacts", Key=key)
            raw = resp["Body"].read()
        except Exception:
            return None
        # Argo stores artifacts as tar.gz
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            for member in tar.getmembers():
                f = tar.extractfile(member)
                if f:
                    return json.loads(f.read())
        return None


    def _list_minio_artifacts():
        """List all artifacts in the bucket."""
        resp = minio.list_objects_v2(Bucket="argo-artifacts")
        return [obj["Key"] for obj in resp.get("Contents", [])]


    _ = argo_refresh.value

    minio_mesh_result = None
    if not submitted_wf:
        # Show existing artifacts in MinIO
        _keys = _list_minio_artifacts()
        if _keys:
            workflow_status = mo.md(
                "No workflow submitted.  \n"
                f"MinIO artifacts: {len(_keys)} file(s):\n"
                + "\n".join(f"- `{k}`" for k in _keys)
            )
        else:
            workflow_status = mo.md("No workflow submitted. MinIO bucket is empty.")
    else:
        try:
            _phase = _workflow_phase(str(submitted_wf))

            if _phase == "Succeeded":
                minio_mesh_result = _read_artifact_from_minio(str(submitted_run_id))
                if minio_mesh_result:
                    workflow_status = mo.md(
                        f"`{submitted_wf}` **succeeded** (via MinIO).  \n"
                        f"Sites: {minio_mesh_result.get('num_sites')}, "
                        f"Elements: {minio_mesh_result.get('num_elements')}"
                    )
                else:
                    workflow_status = mo.md(
                        f"`{submitted_wf}` succeeded but artifact not in MinIO yet."
                    )
            elif _phase in {"Failed", "Error"}:
                workflow_status = mo.md(
                    f"`{submitted_wf}` **{_phase}**"
                )
            else:
                _hint = {
                    "Submitted": "Scheduling pod...",
                    "Pending": "Pulling image...",
                    "Running": "Computing mesh...",
                }.get(_phase, "Processing...")
                workflow_status = mo.md(
                    f"`{submitted_wf}` is **{_phase}** — {_hint}"
                )
        except Exception as e:
            workflow_status = mo.md(f"Query error: `{type(e).__name__}: {e}`")

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
        mesh_summary = mo.md("Waiting for MinIO mesh result before plotting.")
    else:
        sites = np.array(mesh_result["sites"])
        elements = np.array(mesh_result["elements"])
        terminals = mesh_result.get("terminals", [])
        probes = mesh_result.get("probe_indices", [])

        mesh_summary = mo.md(
            f"### Mesh (MinIO artifact)\n"
            f"- **Sites**: {mesh_result['num_sites']}  \n"
            f"- **Elements**: {mesh_result['num_elements']}  \n"
            f"- **film_width**: {mesh_result.get('film_width')}  \n"
            f"- **film_height**: {mesh_result.get('film_height')}  \n"
            f"- **Probe indices**: {probes}"
        )
    mesh_summary
    return elements, probes, sites, terminals


@app.cell
def _(elements, mo, probes, sites, terminals):
    import plotly.graph_objects as go

    if len(sites) == 0:
        mesh_plot = mo.md("Waiting for mesh data...")
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

        _ec = {"source": ("#2563eb", "rgba(37,99,235,0.35)"), "drain": ("#dc2626", "rgba(220,38,38,0.35)")}
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
            legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5),
            margin=dict(l=40, r=10, t=35, b=50),
            height=280,
            width=700,
            plot_bgcolor="white",
        )
        mesh_plot = fig
    mesh_plot
    return


if __name__ == "__main__":
    app.run()
