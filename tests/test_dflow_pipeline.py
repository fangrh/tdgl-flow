"""Tests for DFlowTritonPipeline."""
import pytest


def test_import_dflow_pipeline():
    from tdgl_sdk._dflow_pipeline import DFlowTritonPipeline
    assert DFlowTritonPipeline is not None


def test_pipeline_init():
    from tdgl_sdk._dflow_pipeline import DFlowTritonPipeline
    pipe = DFlowTritonPipeline(
        argo_url="http://localhost:30080",
        minio_endpoint="http://localhost:30900",
    )
    assert pipe.argo_url == "http://localhost:30080"
    assert pipe.sbatch_options is not None
    assert pipe.sbatch_options["partition"] == "batch-csl"
    assert pipe.sidecar_interval == 5


def test_pipeline_custom_sbatch():
    from tdgl_sdk._dflow_pipeline import DFlowTritonPipeline
    pipe = DFlowTritonPipeline(
        argo_url="http://localhost:30080",
        minio_endpoint="http://localhost:30900",
        sbatch_options={"partition": "gpu", "cpus-per-task": "8"},
        sidecar_interval=10,
    )
    assert pipe.sbatch_options["partition"] == "gpu"
    assert pipe.sbatch_options["cpus-per-task"] == "8"
    assert pipe.sidecar_interval == 10