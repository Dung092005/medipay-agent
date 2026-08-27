# Observability and alert ownership

Track these time series by release fingerprint and provider: HTTP 5xx/401/429,
readiness state, stage p50/p95/p99, TTFT, total latency, token/cost, cache hit,
provider timeout/circuit state, queue depth, citation-verifier downgrade rate,
quality drift and conversation persistence errors.

The API exposes a bounded Prometheus-compatible scrape at `/metrics`. In local
development it is unauthenticated; production must set `METRICS_TOKEN` and the
scraper sends `Authorization: Bearer <token>`. The endpoint never labels by
user, query, token or document ID, and stores only bounded in-process latency
samples. Export or scrape it externally before relying on it for alerting.

Page on sustained readiness failure, mixed-release/parity mismatch, high-risk
unsupported claim, cross-user access, 5xx/429 budget breach or cost kill-switch
activation. Keep a dashboard owner and an on-call owner in the deployment record;
the repository cannot infer external ownership.
