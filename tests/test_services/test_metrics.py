from __future__ import annotations

from src.services.metrics import MetricsRegistry


def test_metrics_registry_renders_bounded_counter_and_histogram():
    registry = MetricsRegistry()
    registry.inc("http_requests_total", method="GET", path="/ready", status="200")
    registry.observe("http_request_duration_seconds", 0.25, method="GET", path="/ready")
    rendered = registry.render()
    assert "medipay_http_requests_total" in rendered
    assert 'path="/ready"' in rendered
    assert "medipay_http_request_duration_seconds_count" in rendered
    assert "0.250000000" in rendered


def test_metrics_labels_are_escaped_and_name_is_sanitized():
    registry = MetricsRegistry()
    registry.inc("unsafe name", path='line\n"break')
    rendered = registry.render()
    assert "unsafe_name" in rendered
    assert "\\n" in rendered
    assert '\\"' in rendered
