"""End-to-end integration tests for tdgl_viewer_rust.

Requires MinIO port-forward: kubectl port-forward -n tdgl svc/minio 30900:9000
"""
import pytest

MINIO_URL = "http://localhost:30900"


def test_list_runs():
    from tdgl_viewer_rust.tdgl_viewer_rust import TdglViewer as RustViewer
    v = RustViewer(MINIO_URL)
    runs = v.list_runs()
    assert isinstance(runs, list)


def test_open_and_render():
    from tdgl_viewer_rust.tdgl_viewer_rust import TdglViewer as RustViewer
    v = RustViewer(MINIO_URL)
    runs = v.list_runs()
    if not runs:
        pytest.skip("No runs in MinIO")
    v.open(run_index=0)
    total = v.total_frames()
    assert total > 0, "should have at least 1 frame"
    png = v.render_frame(0)
    assert png[:4] == b"\x89PNG", "should return valid PNG"
    assert len(png) > 1000, "PNG should be at least 1KB"


def test_python_wrapper_import():
    from tdgl_viewer_rust import TdglViewer
    v = TdglViewer(MINIO_URL)
    runs = v.list_runs()
    assert isinstance(runs, list)


def test_render_multiple_frames():
    from tdgl_viewer_rust.tdgl_viewer_rust import TdglViewer as RustViewer
    v = RustViewer(MINIO_URL)
    runs = v.list_runs()
    if not runs:
        pytest.skip("No runs in MinIO")
    v.open(run_index=0)
    total = v.total_frames()
    # Render frame 0, 1, and last frame
    for idx in [0, 1, total - 1]:
        png = v.render_frame(idx)
        assert png[:4] == b"\x89PNG", f"frame {idx} should be valid PNG"


def test_buffer_caching():
    from tdgl_viewer_rust.tdgl_viewer_rust import TdglViewer as RustViewer
    v = RustViewer(MINIO_URL)
    runs = v.list_runs()
    if not runs:
        pytest.skip("No runs in MinIO")
    v.open(run_index=0)
    # First render (populates cache)
    png1 = v.render_frame(0)
    # Second render (should come from cache)
    png2 = v.render_frame(0)
    assert png1 == png2, "cached frame should match"