from __future__ import annotations

from scripts.run_dual_read_sampling import run


def test_dual_read_sampling_rejects_empty_sample_size():
    try:
        run("postgresql://unused", sample_size=0)
    except ValueError as exc:
        assert "sample_size" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected sample size validation")
