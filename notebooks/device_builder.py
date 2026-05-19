import marimo

__generated_with = "0.23.5"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import numpy as np
    import json
    import uuid

    from hera.workflows import Workflow, WorkflowsService, Parameter
    from hera.workflows.models import WorkflowTemplateRef as WTR

    return Parameter, WTR, Workflow, WorkflowsService, json, mo, np, uuid


@app.cell
def _(mo):
    mo.md("""
    # Rectangle Device Builder
    """)
    return


@app.cell
def _():
    pass
    return


@app.cell(hide_code=True)
def argo_config(WorkflowsService, mo):
    # Argo Workflows 连接
    argo_svc = WorkflowsService(
        host="http://localhost:2746",
        verify_ssl=False,
        namespace="tdgl",
    )
    mo.md(f"Argo Server: `{argo_svc.host}`")
    return (argo_svc,)


@app.cell
def _(mo):
    mo.md(r"""
    ## Device 参数
    """)
    return


@app.cell(hide_code=True)
def timing_header():
    pass
    return


@app.cell(hide_code=True)
def timing_params():
    pass
    return


@app.cell
def _(mo):
    # Device 参数控件
    film_width = mo.ui.slider(start=1.0, stop=20.0, step=0.5, value=10.0, label="film_width")
    film_height = mo.ui.slider(start=0.5, stop=10.0, step=0.5, value=2.0, label="film_height")
    elec_width = mo.ui.slider(start=0.1, stop=5.0, step=0.1, value=0.5, label="elec_width")
    elec_height = mo.ui.slider(start=0.1, stop=5.0, step=0.1, value=1.0, label="elec_height")
    elec_y_offset = mo.ui.slider(start=-3.0, stop=3.0, step=0.1, value=0.0, label="elec_y_offset")
    max_edge_length = mo.ui.slider(start=0.1, stop=2.0, step=0.1, value=0.5, label="max_edge_length")
    smooth = mo.ui.slider(start=0, stop=500, step=10, value=100, label="smooth")

    mo.vstack([
        mo.hstack([film_width, film_height]),
        mo.hstack([elec_width, elec_height]),
        mo.hstack([elec_y_offset, max_edge_length, smooth]),
    ])
    return (
        elec_height,
        elec_width,
        elec_y_offset,
        film_height,
        film_width,
        max_edge_length,
        smooth,
    )


@app.cell
def _(mo):
    probe1_x = mo.ui.number(value=-3.0, label="probe1_x")
    probe1_y = mo.ui.number(value=0.0, label="probe1_y")
    probe2_x = mo.ui.number(value=3.0, label="probe2_x")
    probe2_y = mo.ui.number(value=0.0, label="probe2_y")

    mo.hstack([probe1_x, probe1_y, probe2_x, probe2_y])
    return probe1_x, probe1_y, probe2_x, probe2_y


@app.cell
def _(mo):
    build_btn = mo.ui.run_button(label="Build Device")
    build_btn
    return (build_btn,)


@app.cell
def _(
    Parameter,
    WTR,
    Workflow,
    argo_svc,
    build_btn,
    elec_height,
    elec_width,
    elec_y_offset,
    film_height,
    film_width,
    json,
    max_edge_length,
    mo,
    probe1_x,
    probe1_y,
    probe2_x,
    probe2_y,
    smooth,
    uuid,
):
    wf_name = None

    if build_btn.value:
        device_params = {
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

        run_id = str(uuid.uuid4())
        workflow = Workflow(
            generate_name="rect-device-",
            namespace="tdgl",
            workflow_template_ref=WTR(name="rectangle-device-builder"),
            arguments=[
                Parameter(name="run-id", value=run_id),
                Parameter(name="device-params-json", value=json.dumps(device_params)),
            ],
            workflows_service=argo_svc,
        )

        try:
            created = workflow.create()
            wf_name = created.metadata.name
            mo.md(f"Submitted: `{wf_name}`")
        except Exception as e:
            mo.md(f"**Submit failed**: `{e}`")
    else:
        mo.md("点击 **Build Device**")
    return (wf_name,)


@app.cell(hide_code=True)
def submit_header():
    pass
    return


@app.cell(hide_code=True)
def submit_workflow():
    pass
    return


@app.cell(hide_code=True)
def monitor_header(mo):
    mo.md("""
    ## 获取结果
    """)
    return


@app.cell(hide_code=True)
def monitor_workflow(argo_svc, mo, wf_name):
    import time

    mesh_result = None
    if wf_name is not None:
        refresh_btn = mo.ui.run_button(label="Refresh")
        refresh_btn

        if refresh_btn.value:
            wf = argo_svc.get_workflow(name=wf_name, namespace="tdgl")
            phase = getattr(wf.status, "phase", "Unknown") if wf.status else "Unknown"

            if phase == "Succeeded":
                try:
                    artifact_data = argo_svc.get_output_artifact(
                        name=wf_name,
                        namespace="tdgl",
                        node_name=wf_name,
                        artifact_name="mesh",
                    )
                    mesh_result = artifact_data
                    mo.md(f"Mesh ready — **{mesh_result['num_sites']}** sites, **{mesh_result['num_elements']}** elements")
                except Exception as e:
                    mo.md(f"**Artifact fetch failed**: `{e}`")
            else:
                mo.md(f"Status: **{phase}**")
        else:
            mo.md("点击 **Refresh** 获取 mesh 结果")
    else:
        mo.md("提交 Workflow 后可在此获取结果")
    return (mesh_result,)


@app.cell
def _(mesh_result, mo, np):
    if mesh_result is None:
        mo.stop(True)

    sites = np.array(mesh_result["sites"])
    elements = np.array(mesh_result["elements"])
    terminals = mesh_result.get("terminals", [])
    probes = mesh_result.get("probe_indices", [])

    mo.md(
        f"### Mesh\n"
        f"- **Sites**: {mesh_result['num_sites']}  \n"
        f"- **Elements**: {mesh_result['num_elements']}  \n"
        f"- **Terminals**: {len(terminals)}  \n"
        f"- **Probe indices**: {probes}"
    )
    return elements, probes, sites, terminals


@app.cell
def _(elements, mo, probes, sites, terminals):
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.triplot(sites[:, 0], sites[:, 1], elements, linewidth=0.3, color="#94a3b8")

    # 标记 terminals
    colors = ["#2563eb", "#dc2626"]
    for i, t in enumerate(terminals):
        idx = t["site_indices"]
        ax.scatter(sites[idx, 0], sites[idx, 1], s=2, c=colors[i % 2], label=t["name"])

    # 标记 probe points
    ax.scatter(
        sites[probes, 0], sites[probes, 1],
        s=40, c="#16a34a", marker="x", zorder=5, label="probes",
    )

    ax.set_aspect("equal")
    ax.legend(fontsize=8)
    ax.set_title(f"Device mesh ({len(sites)} sites)")
    mo.mpl.InteractiveFigure(fig)
    return


if __name__ == "__main__":
    app.run()
