# Monitoring contract

`prometheus-alerts.yml` is the repository-owned alert baseline for the API's
bounded `/metrics` endpoint. Load it into the external Prometheus/Alertmanager
owned by the deployment platform; the repository does not claim that an
external dashboard or on-call route exists until staging evidence records its
owner.

The rules intentionally use only stable endpoint/provider/outcome labels. Do
not add user IDs, query text, document IDs, authorization values, or release
payloads as labels. Production scrapers must send `Authorization: Bearer
<METRICS_TOKEN>` and must verify the token path over HTTPS.

Required staging checks:

1. Scrape `/metrics` successfully with the production token.
2. Confirm 5xx, readiness, chat latency, provider and retrieval alerts fire in
   a disposable staging test and resolve after recovery.
3. Record dashboard URL, alert receiver, owner and escalation policy in the
   deployment record before go-live.
