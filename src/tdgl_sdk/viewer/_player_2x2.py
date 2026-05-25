from tdgl_sdk.viewer._player import RealtimeTDGLWidgetPlayer
from tdgl_sdk.viewer._render import render_frame_2x2


class Player2x2(RealtimeTDGLWidgetPlayer):
    """2x2 grid player: |psi|, mu, V-vs-t, I-V."""

    def _render(self, idx):
        return render_frame_2x2(
            self.h5_path, self._mesh, self.iv_cache, self.mu_vmax, idx,
            debug_log=self._debug, **self._s3_kwds,
        )


def create_player_2x2(
    h5_path: str,
    live: bool = False,
    timing_steps: list | None = None,
    average_time: float | None = None,
    debug: bool = False,
    debug_log=None,
    **s3_kwds,
) -> Player2x2:
    """Create a 2x2 grid widget player for an HDF5 file.

    Same arguments as create_player(). Returns a Player2x2 with
    |psi|, mu, V-vs-t, and I-V panels.
    """
    from tdgl_sdk.viewer._debug import DebugLog
    from tdgl_sdk.viewer._iv import IVCache, _extract_timing_steps_from_terminal_currents, _load_tc_from_file
    from tdgl_sdk.viewer._mesh import _collect_mu_maxes, _load_mesh_from_file, h5open

    if debug_log is None and debug:
        debug_log = DebugLog()

    # Batch all initialization reads into a single HDF5 open to minimize
    # network round-trips over ROS3 (each open costs ~1-3s over port-forward).
    # Previously 5 separate opens → now 1 open.
    with h5open(h5_path, "r", **s3_kwds) as f:
        mesh = _load_mesh_from_file(f)
        total = mesh["total_frames"]
        sample = list(range(total)) if total <= 5 else [0, total // 4, total // 2, 3 * total // 4, total - 1]
        mu_maxes = []
        _collect_mu_maxes(f, sample, mu_maxes)
        mu_vmax = float(max(mu_maxes)) if mu_maxes and max(mu_maxes) > 0 else 1.0
        try:
            tc_fn = _load_tc_from_file(f)
        except Exception:
            tc_fn = None
        auto_timing_steps = _extract_timing_steps_from_terminal_currents(tc_fn)

    iv_cache = IVCache(h5_path, mesh, tc_fn=tc_fn, auto_timing_steps=auto_timing_steps,
                       poll_interval=0.5 if live else 1.0,
                       batch_size=256 if live else 2048,
                       debug_log=debug_log, **s3_kwds)
    if timing_steps is not None:
        iv_cache.set_timing_steps(timing_steps, average_time=average_time)
    iv_cache.ensure(0)
    if not live:
        iv_cache.start_step_average_prefetch()
    else:
        iv_cache.start()
    player = Player2x2(h5_path, mesh, iv_cache, mu_vmax, debug_log=debug_log, **s3_kwds)
    player.live = live
    player.start_status_updates()
    return player
