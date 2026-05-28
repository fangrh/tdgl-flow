#!/usr/bin/env python3
"""Benchmark py-tdgl using the quickstart weak-link example device.

This replicates the device from py-tdgl/docs/notebooks/quickstart.ipynb.
"""

import os
import sys
import time

sys.path.insert(0, "../py-tdgl")

os.environ["OPENBLAS_NUM_THREADS"] = "1"

import tdgl
from tdgl.geometry import box, circle
import numpy as np


def make_device():
    """Create the weak-link device from the quickstart example."""
    length_units = "um"
    xi = 0.5
    london_lambda = 2
    d = 0.1
    layer = tdgl.Layer(
        coherence_length=xi,
        london_lambda=london_lambda,
        thickness=d,
        gamma=1,
    )

    total_width = 5
    total_length = 3.5 * total_width
    link_width = total_width / 3

    right_notch = (
        tdgl.Polygon(points=box(total_width))
        .rotate(45)
        .translate(dx=(np.sqrt(2) * total_width + link_width) / 2)
    )
    left_notch = right_notch.scale(xfact=-1)
    film = (
        tdgl.Polygon("film", points=box(total_width, total_length))
        .difference(right_notch, left_notch)
        .resample(401)
        .buffer(0)
    )

    round_hole = (
        tdgl.Polygon("round_hole", points=circle(link_width / 2))
        .translate(dy=total_length / 5)
    )
    square_hole = (
        tdgl.Polygon("square_hole", points=box(link_width))
        .rotate(45)
        .translate(dy=-total_length / 5)
    )

    source = (
        tdgl.Polygon("source", points=box(1.1 * total_width, total_length / 100))
        .translate(dy=total_length / 2)
    )
    drain = source.scale(yfact=-1).set_name("drain")

    device = tdgl.Device(
        "weak_link",
        layer=layer,
        film=film,
        holes=[round_hole, square_hole],
        terminals=[source, drain],
        probe_points=[(0, total_length / 2.5), (0, -total_length / 2.5)],
        length_units=length_units,
    )
    device.make_mesh(max_edge_length=xi / 2, smooth=100)
    return device


def main():
    print("Generating mesh...")
    device = make_device()
    ns = len(device.mesh.sites)
    ne = len(device.mesh.edge_mesh.edges)
    print(f"Device: weak_link, {ns} sites, {ne} edges")

    # Test 1: No screening, solve_time=50 (shortened for benchmark)
    print("\n--- py-tdgl: No screening, solve_time=50 ---")
    options1 = tdgl.SolverOptions(
        solve_time=50,
        dt_init=1e-4,
        dt_max=1e-2,
        adaptive=True,
        adaptive_window=10,
        max_solve_retries=10,
        adaptive_time_step_multiplier=0.25,
        field_units="mT",
        current_units="uA",
        save_every=100,
        include_screening=False,
    )
    start = time.perf_counter()
    solution1 = tdgl.solve(
        device,
        options1,
        terminal_currents=dict(source=12, drain=-12),
    )
    t1 = time.perf_counter() - start
    print(f"Total time: {t1:.3f} s")

    # Test 2: With screening, solve_time=50
    print("\n--- py-tdgl: With screening, solve_time=50 ---")
    options2 = tdgl.SolverOptions(
        solve_time=50,
        dt_init=1e-4,
        dt_max=1e-2,
        adaptive=True,
        adaptive_window=10,
        max_solve_retries=10,
        adaptive_time_step_multiplier=0.25,
        field_units="mT",
        current_units="uA",
        save_every=100,
        include_screening=True,
        screening_step_size=0.1,
        screening_step_drag=0.5,
        screening_tolerance=1e-3,
        max_iterations_per_step=100,
    )
    start = time.perf_counter()
    solution2 = tdgl.solve(
        device,
        options2,
        terminal_currents=dict(source=12, drain=-12),
    )
    t2 = time.perf_counter() - start
    print(f"Total time: {t2:.3f} s")


if __name__ == "__main__":
    main()
