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
    from tdgl_sdk.viewer._iv import IVCache
    from tdgl_sdk.viewer._mesh import estimate_mu_vmax, load_mesh

    if debug_log is None and debug:
        debug_log = DebugLog()
    mesh = load_mesh(h5_path, **s3_kwds)
    mu_vmax = estimate_mu_vmax(h5_path, mesh["total_frames"], **s3_kwds)
    iv_cache = IVCache(h5_path, mesh, poll_interval=1.0, batch_size=128, debug_log=debug_log, **s3_kwds)
    if timing_steps is not None:
        iv_cache.set_timing_steps(timing_steps, average_time=average_time)
    if not live:
        iv_cache.batch_size = 512
    iv_cache.ensure(0)
    iv_cache.start()
    player = Player2x2(h5_path, mesh, iv_cache, mu_vmax, debug_log=debug_log, **s3_kwds)
    player.live = live
    return player