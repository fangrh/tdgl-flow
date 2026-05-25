import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tdgl_sdk.viewer._iv import _extract_timing_steps_from_terminal_currents


def test_extract_timing_steps_from_terminal_currents_closure():
    steps = [
        {
            "je_start": 0.0,
            "je_end": 0.2,
            "ramp_start": 0.0,
            "ramp_end": 100.0,
            "stable_end": 300.0,
        },
        {
            "je_start": 0.2,
            "je_end": 0.4,
            "ramp_start": 300.0,
            "ramp_end": 400.0,
            "stable_end": 600.0,
        },
    ]

    def terminal_currents(_t):
        return steps

    extracted = _extract_timing_steps_from_terminal_currents(terminal_currents)

    assert extracted == steps
    assert extracted is not steps
    assert extracted[0] is not steps[0]


def test_extract_timing_steps_ignores_unrelated_closure():
    value = [{"not": "a timing step"}]

    def terminal_currents(_t):
        return value

    assert _extract_timing_steps_from_terminal_currents(terminal_currents) is None
