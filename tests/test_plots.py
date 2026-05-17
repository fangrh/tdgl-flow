import base64


def test_render_mesh_plot_returns_base64_png():
    from tdgl_workflow.plots import render_mesh_plot

    mesh_data = {
        "sites": [[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]],
        "elements": [[0, 1, 2]],
        "probe_indices": [0, 1],
        "num_sites": 3,
        "num_elements": 1,
        "film_width": 1.0,
        "film_height": 1.0,
        "elec_width": 0.5,
        "elec_height": 0.5,
        "elec_y_offset": 0.0,
        "probe_points": [(0.0, 0.0), (1.0, 0.0)],
    }

    result = render_mesh_plot(mesh_data)
    decoded = base64.b64decode(result)
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_timing_plot_returns_base64_png():
    from tdgl_workflow.plots import render_timing_plot

    timing_data = {
        "steps": [
            {"je_start": 0.0, "je_end": 1.0, "ramp_start": 0.0, "ramp_end": 0.5, "stable_end": 2.5, "save_start": 1.0, "save_end": 2.0},
            {"je_start": 1.0, "je_end": 2.0, "ramp_start": 2.5, "ramp_end": 3.0, "stable_end": 5.5, "save_start": 3.5, "save_end": 4.5},
        ],
        "ramp_down_steps": [],
        "solve_time": 5.5,
        "n_steps": 2,
    }

    result = render_timing_plot(timing_data)
    decoded = base64.b64decode(result)
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"