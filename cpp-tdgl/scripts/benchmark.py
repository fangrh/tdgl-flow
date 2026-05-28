#!/usr/bin/env python3
"""Benchmark py-tdgl on the reference mesh for comparison with cpp-tdgl."""

import sys
import time

sys.path.insert(0, "../py-tdgl")

import tdgl
import numpy as np


def make_device():
    """Create the same device as generate_test_data.py."""
    from tdgl import Polygon, Layer

    layer = Layer(
        london_lambda=2.0,
        coherence_length=0.5,
        thickness=0.1,
        gamma=10.0,
        u=5.79,
    )

    film = Polygon(name="film", points=[
        (0, 0), (10, 0), (10, 2), (0, 2)
    ])

    source = Polygon(name="source", points=[
        (-0.5, 0.5), (0.5, 0.5), (0.5, 1.5), (-0.5, 1.5)
    ])
    drain = Polygon(name="drain", points=[
        (9.5, 0.5), (10.5, 0.5), (10.5, 1.5), (9.5, 1.5)
    ])

    device = tdgl.Device(
        name="bench_device",
        layer=layer,
        film=film,
        terminals=[source, drain],
    )
    device.make_mesh(max_edge_length=0.5, smooth=10)
    return device


def main():
    device = make_device()
    print(f"Mesh: {len(device.mesh.sites)} sites, {len(device.mesh.edge_mesh.edges)} edges")

    options_no_screen = tdgl.SolverOptions(
        solve_time=0.05,
        dt_init=1e-4,
        dt_max=1e-2,
        adaptive=True,
        adaptive_window=10,
        max_solve_retries=10,
        adaptive_time_step_multiplier=0.25,
        terminal_psi=0.0,
        save_every=2,
        include_screening=False,
        max_iterations_per_step=100,
    )

    # Without screening
    print("\n--- py-tdgl (no screening) ---")
    start = time.perf_counter()
    solution = tdgl.solve(
        device,
        options_no_screen,
        terminal_currents={"source": 1.0, "drain": -1.0},
    )
    elapsed = time.perf_counter() - start
    print(f"Total time: {elapsed:.3f} s")

    # With screening
    options_screen = tdgl.SolverOptions(
        solve_time=0.05,
        dt_init=1e-4,
        dt_max=1e-2,
        adaptive=True,
        adaptive_window=10,
        max_solve_retries=10,
        adaptive_time_step_multiplier=0.25,
        terminal_psi=0.0,
        save_every=2,
        include_screening=True,
        max_iterations_per_step=100,
        screening_step_size=0.1,
        screening_step_drag=0.5,
        screening_tolerance=1e-3,
    )

    print("\n--- py-tdgl (with screening) ---")
    start = time.perf_counter()
    solution = tdgl.solve(
        device,
        options_screen,
        terminal_currents={"source": 1.0, "drain": -1.0},
    )
    elapsed = time.perf_counter() - start
    print(f"Total time: {elapsed:.3f} s")


if __name__ == "__main__":
    main()
