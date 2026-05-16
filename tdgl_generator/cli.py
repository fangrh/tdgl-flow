"""Standalone CLI entry point for running generation as a K8s Job."""

import argparse
import asyncio
import sys

import httpx

from tdgl_data.synthetic import generate_synthetic_run


async def run(
    data_service_url: str,
    je_min: float,
    je_max: float,
    je_count: int,
    frames_per_je: int,
    delay_seconds: float,
    grid_y: int,
    grid_x: int,
) -> None:
    base_url = data_service_url.rstrip("/")
    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
        run_resp = await client.post("/api/runs", json={
            "solver_type": "synthetic",
            "grid_shape": [grid_y, grid_x],
        })
        run_resp.raise_for_status()
        run_id = run_resp.json()["run_id"]
        print(f"Created run {run_id[:8]}")

        frame_index = 0
        for je_i in range(je_count):
            je = je_min + (je_max - je_min) * je_i / max(je_count - 1, 1)
            for sf in generate_synthetic_run(
                frames_per_je,
                (grid_y, grid_x),
                seed=je_i,
            ):
                frame_resp = await client.post(
                    f"/api/runs/{run_id}/frames",
                    json={
                        "frame_index": frame_index,
                        "time_value": sf.time_value,
                        "je": sf.je,
                        "voltage": sf.voltage,
                        "psi_real": sf.psi_real.tolist(),
                        "psi_imag": sf.psi_imag.tolist(),
                        "mu": sf.mu.tolist(),
                    },
                )
                frame_resp.raise_for_status()
                frame_index += 1

            print(f"Batch {je_i + 1}/{je_count}: Je={je:.3f}, {frame_index} frames total")

            if je_i < je_count - 1:
                await asyncio.sleep(delay_seconds)

        print(f"Done. {frame_index} frames generated.")


def main() -> None:
    parser = argparse.ArgumentParser(description="TDGL data generator")
    parser.add_argument("--data-service-url", default="", help="Data service base URL")
    parser.add_argument("--je-min", type=float, default=None)
    parser.add_argument("--je-max", type=float, default=None)
    parser.add_argument("--je-count", type=int, default=None)
    parser.add_argument("--frames-per-je", type=int, default=None)
    parser.add_argument("--delay", type=float, default=None, help="Delay between batches in seconds")
    parser.add_argument("--grid-y", type=int, default=None)
    parser.add_argument("--grid-x", type=int, default=None)
    args = parser.parse_args()

    import os
    data_service_url = args.data_service_url or os.environ.get("TDGL_DATA_SERVICE_URL", "")
    if not data_service_url:
        print("Error: --data-service-url or TDGL_DATA_SERVICE_URL is required", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run(
        data_service_url=data_service_url,
        je_min=args.je_min if args.je_min is not None else float(os.environ.get("TDGL_JE_MIN", "-1.0")),
        je_max=args.je_max if args.je_max is not None else float(os.environ.get("TDGL_JE_MAX", "1.0")),
        je_count=args.je_count if args.je_count is not None else int(os.environ.get("TDGL_JE_COUNT", "10")),
        frames_per_je=args.frames_per_je if args.frames_per_je is not None else int(os.environ.get("TDGL_FRAMES_PER_JE", "5")),
        delay_seconds=args.delay if args.delay is not None else float(os.environ.get("TDGL_DELAY", "2.0")),
        grid_y=args.grid_y if args.grid_y is not None else int(os.environ.get("TDGL_GRID_Y", "72")),
        grid_x=args.grid_x if args.grid_x is not None else int(os.environ.get("TDGL_GRID_X", "72")),
    ))


if __name__ == "__main__":
    main()
