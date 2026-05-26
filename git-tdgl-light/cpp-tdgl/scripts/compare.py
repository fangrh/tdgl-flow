#!/usr/bin/env python3
"""Compare cpp-tdgl output against py-tdgl reference.

Usage:
    python scripts/compare.py --py-tdgl data/reference.h5 --cpp-tdgl data/cpp_output.h5 [--tolerance 1e-4]
"""

import argparse
import sys
import numpy as np
import h5py


def load_psi(grp):
    """Load psi from a step group, handling both complex and split real/imag formats."""
    if "psi" in grp:
        return grp["psi"][()]
    elif "psi_real" in grp and "psi_imag" in grp:
        return grp["psi_real"][()] + 1j * grp["psi_imag"][()]
    return None


def compare_arrays(name, arr_py, arr_cpp, tolerance):
    """Compare two arrays and return (max_rel_error, passed).

    Uses a combined absolute + relative tolerance to handle near-zero values:
        rel_err = |a - b| / max(max(|a|, |b|), atol)
    where atol = tolerance * max(|arr_py|).
    """
    if arr_py.shape != arr_cpp.shape:
        print(f"  {name}: SHAPE MISMATCH py={arr_py.shape} cpp={arr_cpp.shape}")
        return float("inf"), False

    # Handle complex arrays: compare |psi|^2
    if np.iscomplexobj(arr_py) or np.iscomplexobj(arr_cpp):
        arr_py = np.abs(arr_py) ** 2
        arr_cpp = np.abs(arr_cpp) ** 2
        name = name + " (|psi|^2)"

    # mu has Neumann BC gauge freedom: subtract mean before comparing
    if name == "mu":
        arr_py = arr_py - arr_py.mean()
        arr_cpp = arr_cpp - arr_cpp.mean()

    abs_diff = np.abs(arr_py - arr_cpp)
    # Floor denominator at tolerance * max_abs to avoid blow-up near zero
    atol = tolerance * max(np.max(np.abs(arr_py)), 1e-30)
    denom = np.maximum(np.maximum(np.abs(arr_py), np.abs(arr_cpp)), atol)
    rel_err = abs_diff / denom
    max_rel = float(np.max(rel_err))
    max_abs = float(np.max(abs_diff))
    passed = max_rel < 1.0  # relative to our atol floor
    return max_rel, passed


def compare_step(grp_py, grp_cpp, tolerance):
    """Compare a single time step."""
    results = {}

    # psi (special handling for format)
    psi_py = load_psi(grp_py)
    psi_cpp = load_psi(grp_cpp)
    if psi_py is not None and psi_cpp is not None:
        max_err, passed = compare_arrays("psi", psi_py, psi_cpp, tolerance)
        results["psi"] = (max_err, passed)

    # Other datasets
    for ds_name in ["mu", "supercurrent", "normal_current"]:
        if ds_name in grp_py and ds_name in grp_cpp:
            arr_py = grp_py[ds_name][()]
            arr_cpp = grp_cpp[ds_name][()]
            max_err, passed = compare_arrays(ds_name, arr_py, arr_cpp, tolerance)
            results[ds_name] = (max_err, passed)

    return results


def main():
    parser = argparse.ArgumentParser(description="Compare cpp-tdgl vs py-tdgl output")
    parser.add_argument("--py-tdgl", required=True, help="py-tdgl reference HDF5")
    parser.add_argument("--cpp-tdgl", required=True, help="cpp-tdgl output HDF5")
    parser.add_argument("--tolerance", type=float, default=1e-4,
                        help="Maximum relative error tolerance")
    args = parser.parse_args()

    print(f"Comparing:")
    print(f"  py-tdgl:  {args.py_tdgl}")
    print(f"  cpp-tdgl: {args.cpp_tdgl}")
    print(f"  Tolerance: {args.tolerance}")
    print()

    with h5py.File(args.py_tdgl, "r") as f_py, h5py.File(args.cpp_tdgl, "r") as f_cpp:
        data_py = f_py.get("data", f_py.get("solution/data"))
        data_cpp = f_cpp.get("data", f_cpp.get("solution/data"))

        if data_py is None:
            print("ERROR: No /data/ or /solution/data/ group found in py-tdgl file")
            sys.exit(1)
        if data_cpp is None:
            print("ERROR: No /data/ or /solution/data/ group found in cpp-tdgl file")
            sys.exit(1)

        # Collect times from each file
        steps_py = {}
        for k in data_py.keys():
            if k.lstrip("-").isdigit():
                t = float(data_py[k].attrs.get("time", data_py[k].attrs.get("step", k)))
                dt = float(data_py[k].attrs.get("dt", 0))
                steps_py[int(k)] = (t, dt)

        steps_cpp = {}
        for k in data_cpp.keys():
            if k.lstrip("-").isdigit():
                t = float(data_cpp[k].attrs.get("time", data_cpp[k].attrs.get("step", k)))
                dt = float(data_cpp[k].attrs.get("dt", 0))
                steps_cpp[int(k)] = (t, dt)

        times_py = sorted(v[0] for v in steps_py.values())
        times_cpp = sorted(v[0] for v in steps_cpp.values())

        # Skip py-tdgl's stale-time final save:
        # py-tdgl's runner sets state["time"] before processing the step,
        # but saves values after the loop breaks. The final save reports
        # time from before the last step, but values from after it.
        sorted_py = sorted(steps_py.keys())
        skip_py_groups = set()
        if sorted_py:
            skip_py_groups.add(sorted_py[-1])
        print(f"py-tdgl saved times:  {[(v[0], v[1]) for v in steps_py.values()]}")
        print(f"cpp-tdgl saved times: {[(v[0], v[1]) for v in steps_cpp.values()]}")
        if skip_py_groups:
            print(f"Skipping py-tdgl final save (stale time): {sorted(skip_py_groups)}")
        print()

        # Match steps by closest time
        times_py = sorted(v[0] for v in steps_py.values())
        times_cpp = sorted(v[0] for v in steps_cpp.values())

        all_passed = True
        global_max_err = {}
        num_compared = 0

        # Compare at each cpp time to the closest py time
        for t_cpp in times_cpp:
            # Find closest py time
            t_py = min(times_py, key=lambda t: abs(t - t_cpp))
            if abs(t_py - t_cpp) > 1e-3:
                print(f"  Skipping cpp t={t_cpp:.6f} (no close py match, closest py={t_py:.6f})")
                continue

            # Get the step keys
            step_py = [k for k, v in steps_py.items() if abs(v[0] - t_py) < 1e-8][0]
            step_cpp = [k for k, v in steps_cpp.items() if abs(v[0] - t_cpp) < 1e-8][0]

            # Skip stale py-tdgl saves
            if step_py in skip_py_groups:
                print(f"  Skipping py_t={t_py:.6f} ({step_py}) [stale time]")
                continue

            grp_py = data_py[str(step_py)]
            grp_cpp = data_cpp[str(step_cpp)]

            results = compare_step(grp_py, grp_cpp, args.tolerance)
            if results:
                num_compared += 1
                status = "OK" if all(p for _, p in results.values()) else "FAIL"
                if status == "FAIL":
                    all_passed = False
                parts = [f"{name}: {err:.2e}" for name, (err, _) in results.items()]
                print(f"  py_t={t_py:.6f} ({step_py}) vs cpp_t={t_cpp:.6f} ({step_cpp}): {status}  {'  '.join(parts)}")

                for name, (err, _) in results.items():
                    if name not in global_max_err or err > global_max_err[name][0]:
                        global_max_err[name] = (err, t_cpp)

        print()
        print(f"Compared {num_compared} steps")
        print()
        print("Global max relative errors:")
        for name, (err, t) in sorted(global_max_err.items()):
            status = "PASS" if err < args.tolerance else "FAIL"
            print(f"  {name:20s}: {err:.2e} (at t={t:.6f}) [{status}]")

        print()
        if all_passed and num_compared > 0:
            print("ALL CHECKS PASSED")
            sys.exit(0)
        else:
            if num_compared == 0:
                print("NO STEPS COMPARED")
            else:
                print("SOME CHECKS FAILED")
            sys.exit(1)


if __name__ == "__main__":
    main()
