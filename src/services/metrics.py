"""Small dependency-free runtime metrics registry.

Counters and bounded latency samples are exposed in Prometheus text format.
Only trusted call-site labels are used; user input is never a metric label.
"""

from __future__ import annotations

import re
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field

_NAME = re.compile(r"[^a-zA-Z0-9_:]")
_LABEL = re.compile(r"[^a-zA-Z0-9_.-]")


def _safe_name(value: str) -> str:
    return _NAME.sub("_", value).strip("_") or "metric"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    parts = []
    for key, value in sorted(labels.items()):
        safe_key = _LABEL.sub("_", str(key)).strip("_") or "label"
        parts.append(f'{safe_key}="{_escape(str(value))}"')
    return "{" + ",".join(parts) + "}"


@dataclass
class _Histogram:
    values: deque[float] = field(default_factory=lambda: deque(maxlen=2048))


class MetricsRegistry:
    """Bounded counters and observations safe to use from async handlers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: defaultdict[tuple[str, tuple[tuple[str, str], ...]], int] = defaultdict(int)
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], _Histogram] = {}

    @staticmethod
    def _key(name: str, labels: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
        return _safe_name(name), tuple(sorted((str(k), str(v)) for k, v in labels.items()))

    def inc(self, name: str, amount: int = 1, **labels: str) -> None:
        if amount == 0:
            return
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] += int(amount)

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._histograms.setdefault(key, _Histogram()).values.append(float(value))

    def render(self) -> str:
        with self._lock:
            counters = dict(self._counters)
            histograms = {key: tuple(item.values) for key, item in self._histograms.items()}
        lines = [
            "# HELP medipay_metrics_registry_info Runtime metrics from the API process.",
            "# TYPE medipay_metrics_registry_info gauge",
            "medipay_metrics_registry_info 1",
        ]
        for (name, label_items), value in sorted(counters.items()):
            lines.append(f"medipay_{name}{_labels(dict(label_items))} {value}")
        for (name, label_items), values in sorted(histograms.items()):
            if not values:
                continue
            labels_dict = dict(label_items)
            labels = _labels(labels_dict)
            ordered = sorted(values)
            count = len(ordered)
            total = sum(ordered)
            lines.append(f"# TYPE medipay_{name} summary")
            for quantile, fraction in (("0.5", 0.50), ("0.95", 0.95), ("0.99", 0.99)):
                position = min(count - 1, max(0, int(fraction * count) - 1))
                quantile_labels = dict(labels_dict)
                quantile_labels["quantile"] = quantile
                lines.append(
                    f"medipay_{name}{_labels(quantile_labels)} {ordered[position]:.9f}"
                )
            lines.append(f"medipay_{name}_sum{labels} {total:.9f}")
            lines.append(f"medipay_{name}_count{labels} {count}")
        return "\n".join(lines) + "\n"

    def clear(self) -> None:
        with self._lock:
            self._counters.clear()
            self._histograms.clear()


metrics = MetricsRegistry()
