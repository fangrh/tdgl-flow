"""Verify run_tdgl_sim.py is syntactically valid and uses SimulationPipeline SDK."""
import ast
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "notebooks" / "run_tdgl_sim.py"


def test_script_syntax():
    source = SCRIPT.read_text()
    tree = ast.parse(source)
    func_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    # After refactor, script only needs main() function
    expected = {"main"}
    assert expected.issubset(func_names), f"Missing functions: {expected - func_names}"


def test_uses_simulation_pipeline():
    source = SCRIPT.read_text()
    tree = ast.parse(source)

    # Check that SimulationPipeline is imported and used
    imports_simulation_pipeline = False
    calls_simulation_pipeline = False

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module == "tdgl_sdk":
                for alias in node.names:
                    if alias.name == "SimulationPipeline":
                        imports_simulation_pipeline = True
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id == "SimulationPipeline":
                    calls_simulation_pipeline = True

    assert imports_simulation_pipeline, "Script should import SimulationPipeline from tdgl_sdk"
    assert calls_simulation_pipeline, "Script should instantiate SimulationPipeline"


def test_default_params():
    source = SCRIPT.read_text()
    assert '"film_width": 6.0' in source
    assert '"ramp_time": 2.0' in source
    assert '"save_every": 500' in source
