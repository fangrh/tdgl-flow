"""Verify run_tdgl_sim.py is syntactically valid and its functions are importable."""
import ast
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "notebooks" / "run_tdgl_sim.py"


def test_script_syntax():
    source = SCRIPT.read_text()
    tree = ast.parse(source)
    func_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    expected = {
        "create_argo_service",
        "create_store",
        "submit_workflow",
        "poll_workflow",
        "check_manifest",
        "download_result",
        "preview_animation",
        "print_static_summary",
        "main",
    }
    assert expected.issubset(func_names), f"Missing functions: {expected - func_names}"


def test_default_params():
    source = SCRIPT.read_text()
    assert '"film_width": 6.0' in source
    assert '"ramp_time": 2.0' in source
    assert '"save_every": 500' in source
