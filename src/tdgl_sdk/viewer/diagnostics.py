"""Agent-facing diagnostic API for TDGL HDF5 data files.

examine_h5() returns a structured report that lets agents verify data integrity
without rendering anything. This saves tokens — the agent gets a dict instead of
needing to load widgets, plot heatmaps, or visually inspect frames.
"""
import os

import numpy as np

from tdgl_sdk.viewer._mesh import h5open


def examine_h5(h5_path: str, **s3_kwds) -> dict:
    """Examine an HDF5 file and return a structured diagnostic report.

    Returns a dict with:
        file: path, file_size_mb
        mesh: sites, elements, mesh_keys (present/missing)
        frames: total, frame_keys (present across all frames), time_range
        data_quality: per-field shape/dtype/range/nan_count/inf_count for first and last frame
        iv_available: whether normal_current + supercurrent datasets exist
        issues: list of strings describing problems found (empty = healthy)
        healthy: bool — True if no issues found
    """
    issues = []

    if h5_path.startswith(("http://", "https://")):
        file_size_mb = None
    else:
        file_size_mb = round(os.path.getsize(h5_path) / (1024 * 1024), 2)

    with h5open(h5_path, "r", **s3_kwds) as f:
        top_keys = list(f.keys())

        mesh_info = _check_mesh(f, issues)
        frame_info = _check_frames(f, issues)
        quality = _check_data_quality(f, frame_info["total"], issues)
        iv_available = _check_iv_availability(f, frame_info["total"])

    healthy = len(issues) == 0

    return {
        "file": {
            "path": h5_path,
            "size_mb": file_size_mb,
            "top_keys": top_keys,
        },
        "mesh": mesh_info,
        "frames": frame_info,
        "data_quality": quality,
        "iv_available": iv_available,
        "issues": issues,
        "healthy": healthy,
    }


def _check_mesh(f, issues):
    mesh_path = "solution/device/mesh"
    mesh_info = {"present": mesh_path in f}

    if mesh_path not in f:
        issues.append(f"Missing mesh group: {mesh_path}")
        return mesh_info

    mesh_group = f[mesh_path]
    mesh_keys = list(mesh_group.keys())
    mesh_info["keys"] = mesh_keys

    expected_mesh = {"sites", "edge_mesh"}
    missing = expected_mesh - set(mesh_keys)
    if missing:
        issues.append(f"Missing mesh datasets: {missing}")

    if "sites" in mesh_group:
        sites = np.array(mesh_group["sites"])
        mesh_info["num_sites"] = len(sites)
        mesh_info["sites_shape"] = list(sites.shape)
        if np.any(np.isnan(sites)):
            issues.append("Mesh sites contain NaN values")
        if np.any(np.isinf(sites)):
            issues.append("Mesh sites contain Inf values")

    if "edge_mesh" in mesh_group:
        em = mesh_group["edge_mesh"]
        mesh_info["edge_mesh_keys"] = list(em.keys())
        if "edges" in em:
            mesh_info["num_edges"] = len(np.array(em["edges"]))

    return mesh_info


def _check_frames(f, issues):
    frame_info = {}

    if "data" not in f:
        issues.append("Missing 'data' group — no simulation frames")
        frame_info["total"] = 0
        return frame_info

    data_group = f["data"]
    frame_keys = list(data_group.keys())
    total = len(frame_keys)
    frame_info["total"] = total

    if total == 0:
        issues.append("data group is empty — no frames saved")
        return frame_info

    try:
        indices = sorted(int(k) for k in frame_keys)
        frame_info["index_range"] = [indices[0], indices[-1]]
        expected = list(range(indices[0], indices[-1] + 1))
        if indices != expected:
            gaps = set(expected) - set(indices)
            issues.append(f"Non-sequential frame indices, {len(gaps)} gaps: {sorted(gaps)[:5]}...")
    except ValueError:
        issues.append(f"Non-integer frame keys found: {frame_keys[:5]}")

    all_frame_keys = set()
    for k in frame_keys:
        all_frame_keys.update(data_group[k].keys())
    frame_info["frame_keys"] = sorted(all_frame_keys)

    times = []
    for k in frame_keys[:min(total, 10)]:
        t = float(data_group[k].attrs.get("time", -1))
        if t >= 0:
            times.append(t)
    if total > 10:
        last_k = frame_keys[-1]
        t = float(data_group[last_k].attrs.get("time", -1))
        if t >= 0:
            times.append(t)
    if times:
        frame_info["time_range"] = [min(times), max(times)]

    return frame_info


def _check_data_quality(f, total, issues):
    quality = {}

    if total == 0:
        return quality

    fields = ["psi", "mu"]
    check_indices = [0, total - 1] if total > 1 else [0]

    for idx in check_indices:
        frame_key = str(idx)
        if frame_key not in f["data"]:
            continue

        frame = f["data"][frame_key]
        label = "first" if idx == 0 else "last"

        for field in fields:
            if field not in frame:
                if f"{field}_missing" not in quality:
                    issues.append(f"Missing '{field}' in {label} frame (idx={idx})")
                    quality[f"{field}_missing"] = True
                continue

            arr = np.array(frame[field])
            nan_count = int(np.sum(np.isnan(arr)))
            inf_count = int(np.sum(np.isinf(arr)))
            arr_real = np.abs(arr) if np.iscomplexobj(arr) else arr

            entry = {
                "shape": list(arr.shape),
                "dtype": str(arr.dtype),
                "min": float(np.nanmin(arr_real)) if nan_count < len(arr) else None,
                "max": float(np.nanmax(arr_real)) if nan_count < len(arr) else None,
                "mean": float(np.nanmean(arr_real)) if nan_count < len(arr) else None,
                "nan_count": nan_count,
                "inf_count": inf_count,
            }

            quality[f"{label}_{field}"] = entry

            if nan_count > 0:
                issues.append(f"'{field}' in {label} frame has {nan_count} NaN values")
            if inf_count > 0:
                issues.append(f"'{field}' in {label} frame has {inf_count} Inf values")

    return quality


def _check_iv_availability(f, total):
    if total == 0:
        return False

    first_frame = f["data/0"]
    return "normal_current" in first_frame and "supercurrent" in first_frame


def format_report(report: dict) -> str:
    """Format the diagnostic report as a human/agent-readable string."""
    lines = []
    lines.append(f"HDF5 Diagnostic: {report['file']['path']}")
    lines.append(f"  Size: {report['file']['size_mb']:.2f} MB")
    lines.append(f"  Top keys: {report['file']['top_keys']}")

    mesh = report["mesh"]
    if mesh.get("present"):
        lines.append(f"  Mesh: {mesh.get('num_sites', '?')} sites, {mesh.get('num_edges', '?')} edges")
    else:
        lines.append("  Mesh: MISSING")

    frames = report["frames"]
    lines.append(f"  Frames: {frames.get('total', 0)}")
    if "time_range" in frames:
        lines.append(f"  Time: {frames['time_range'][0]:.4f} .. {frames['time_range'][1]:.4f}")
    if "frame_keys" in frames:
        lines.append(f"  Frame datasets: {frames['frame_keys']}")

    for key, val in report.get("data_quality", {}).items():
        if isinstance(val, dict) and "shape" in val:
            vmin = f"{val['min']:.4f}" if val["min"] is not None else "N/A"
            vmax = f"{val['max']:.4f}" if val["max"] is not None else "N/A"
            lines.append(f"  {key}: shape={val['shape']}, range=[{vmin}, {vmax}], "
                        f"nan={val['nan_count']}, inf={val['inf_count']}")

    lines.append(f"  I-V available: {report.get('iv_available', False)}")

    issues = report.get("issues", [])
    if issues:
        lines.append(f"  Issues ({len(issues)}):")
        for issue in issues:
            lines.append(f"    - {issue}")
    else:
        lines.append("  Issues: none")

    lines.append(f"  Healthy: {report['healthy']}")
    return "\n".join(lines)
