#!/usr/bin/env python3
"""Verify provider-platform contracts without contacting Render or Vercel.

This is intentionally a structural gate.  It proves that the repository has a
safe Render/Vercel contract, while authenticated project, domain and staging
attestation remain external checks.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("eval/results/platform-contract-current.json"))
    args = parser.parse_args()

    render = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    services = render.get("services") or []
    api = next((item for item in services if item.get("name") == "medipay-api"), {})
    env_vars = {str(item.get("key")): item for item in api.get("envVars") or []}
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    web_dockerfile = (ROOT / "web/Dockerfile").read_text(encoding="utf-8")
    vercel = json.loads((ROOT / "web/vercel.json").read_text(encoding="utf-8"))
    vercel_text = json.dumps(vercel, ensure_ascii=False)

    checks: dict[str, bool] = {
        "render_service_is_docker": api.get("runtime") == "docker",
        "render_branch_is_feat_data": api.get("branch") == "feat/data",
        "render_health_check_is_liveness": api.get("healthCheckPath") == "/health",
        "managed_profile_forces_production": "APP_ENV: production" in compose,
        "render_port_is_injected": all(item.get("key") != "PORT" for item in api.get("envVars") or []),
        "render_backend_secret_vars_are_sync_false": all(
            env_vars.get(key, {}).get("sync") is False
            for key in (
                "DATABASE_URL",
                "RUNTIME_DATABASE_URL",
                "QDRANT_API_KEY",
                "NEO4J_PASSWORD",
                "OPENAI_API_KEY",
                "FIREBASE_SERVICE_ACCOUNT_JSON",
                "METRICS_TOKEN",
            )
        ),
        "backend_reads_render_port": 'os.environ.get("PORT", "8000")' in dockerfile
        or 'os.environ.get("PORT", "8000")' in (ROOT / "src/runtime_entrypoint.py").read_text(encoding="utf-8"),
        "backend_binds_all_interfaces": 'host="0.0.0.0"' in (ROOT / "src/runtime_entrypoint.py").read_text(encoding="utf-8"),
        "backend_healthcheck_uses_port": "os.getenv('PORT', '8000')" in dockerfile,
        "backend_runtime_is_non_root": "distroless/python3-debian12:nonroot" in dockerfile,
        "web_runtime_is_non_root": "cgr.dev/chainguard/node" in web_dockerfile,
        "vercel_is_nextjs": vercel.get("framework") == "nextjs",
        "vercel_has_security_headers": "Content-Security-Policy" in vercel_text
        and "X-Content-Type-Options" in vercel_text,
        "vercel_has_no_admin_secret": "FIREBASE_SERVICE_ACCOUNT_JSON" not in vercel_text,
    }
    report: dict[str, Any] = {
        "structural_pass": all(checks.values()),
        "external_attestation_required": True,
        "checks": checks,
        "external_checks": [
            "Render project/paid plan/region and authenticated staging smoke",
            "Vercel project environments/preview protection and browser E2E",
            "Firebase authorized domains and production secret installation",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"structural_pass": report["structural_pass"], "external_attestation_required": True}))
    return 0 if report["structural_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
