#!/usr/bin/env python3
"""Verify the reproducible local Docker/deploy contract and emit a JSON report.

This is a structural gate, not a substitute for managed Render/Vercel smoke or
load testing. It intentionally reports sizes and booleans only, never env values.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERN = re.compile(r"(?i)(?:BEGIN PRIVATE KEY|sk-[a-z0-9]{16,})")
FORBIDDEN_CONTEXT = ("outsource", "data/clean", "database/backups", "eval/results", ".env")


def command(*args: str) -> str:
    result = subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True)
    return result.stdout


def image_size(image: str) -> int | None:
    try:
        return int(command("docker", "image", "inspect", image, "--format", "{{.Size}}").strip())
    except (subprocess.CalledProcessError, ValueError):
        return None


def volume_sizes(profile: str) -> dict[str, int | None]:
    """Return byte sizes for named Compose volumes without reading their contents."""
    try:
        payload = json.loads(command("docker", "compose", "--profile", profile, "config", "--format", "json"))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}
    volumes = payload.get("volumes", {})
    result: dict[str, int | None] = {}
    for logical_name, definition in volumes.items():
        volume_name = str(definition.get("name") or logical_name)
        try:
            mountpoint = command("docker", "volume", "inspect", volume_name, "--format", "{{.Mountpoint}}").strip()
            try:
                result[volume_name] = int(command("du", "-sb", mountpoint).split()[0])
            except (subprocess.CalledProcessError, ValueError, IndexError):
                # Docker Desktop keeps the mountpoint inside its VM; measure it
                # through a short-lived read-only container instead.
                measured = command(
                    "docker", "run", "--rm", "-v", f"{volume_name}:/data:ro",
                    "postgres:16.4-alpine", "du", "-sb", "/data",
                )
                result[volume_name] = int(measured.split()[0])
        except (subprocess.CalledProcessError, ValueError, IndexError):
            result[volume_name] = None
    return result


def context_bytes(paths: tuple[str, ...]) -> int:
    total = 0
    for relative in paths:
        path = ROOT / relative
        if not path.exists():
            continue
        if path.is_file():
            total += path.stat().st_size
            continue
        total += sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    return total


def sarif_result_count(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return sum(len(run.get("results", [])) for run in payload.get("runs", []))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def inspect_profile(profile: str) -> dict[str, Any]:
    payload = json.loads(command("docker", "compose", "--profile", profile, "config", "--format", "json"))
    services = payload.get("services", {})
    violations: list[str] = []
    for name, service in services.items():
        if name in {"postgres", "qdrant", "neo4j", "redis"}:
            for port in service.get("ports", []):
                host_ip = str(port.get("host_ip", "")) if isinstance(port, dict) else ""
                if host_ip not in {"127.0.0.1", "::1", "localhost"} and name != "redis":
                    violations.append(f"{name}: data service port is not loopback-bound in {profile}")
        if name in {"api-local", "web-local", "migrate"} and not service.get("read_only"):
            violations.append(f"{name}: read_only contract missing")
    return {"service_count": len(services), "services": sorted(services), "violations": violations}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("eval/results/deploy-contract.json"))
    args = parser.parse_args()
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").casefold()
    tracked = command("git", "ls-files").splitlines()
    scanner_path = Path(__file__).resolve().relative_to(ROOT).as_posix()
    secret_hits = [
        path for path in tracked
        if path != scanner_path
        if (ROOT / path).is_file()
        and SECRET_PATTERN.search((ROOT / path).read_text(encoding="utf-8", errors="ignore"))
    ]
    report = {
        "profiles": {profile: inspect_profile(profile) for profile in ("local-full", "managed-production")},
        "context": {
            "backend_allowlist_bytes": context_bytes(("Dockerfile", "requirements/runtime.lock", "src", "database/postgres/migrations")),
            "web_allowlist_bytes": context_bytes(("web/Dockerfile", "web/package.json", "web/package-lock.json", "web/app", "web/components", "web/lib", "web/public")),
            "forbidden_patterns_present": [
                pattern for pattern in FORBIDDEN_CONTEXT
                if not any(token in dockerignore for token in (pattern.casefold(), pattern.split("/")[0].casefold()))
            ],
        },
        "images": {
            name: image_size(name)
            for name in (
                "medipay-api-local:latest",
                "medipay-web-local:latest",
                "medipay-migrate:latest",
                "medipay-corpus-worker:latest",
            )
        },
        "volumes": volume_sizes("local-full"),
        "tracked_secret_pattern_hits": secret_hits,
        "sbom_reports": {
            image: str(ROOT / f"eval/results/sbom-{image}.json")
            for image in ("api", "web", "migrate", "pipeline")
        },
        "high_critical_cve_reports": {
            image: sarif_result_count(ROOT / f"eval/results/cves-{image}-high-critical.sarif")
            for image in ("api", "web", "migrate", "pipeline")
        },
    }
    report["security_gate_pass"] = all(
        value == 0 for value in report["high_critical_cve_reports"].values()
        if value is not None
    ) and all(Path(path).exists() for path in report["sbom_reports"].values())
    report["pass"] = not (
        report["tracked_secret_pattern_hits"]
        or report["context"]["forbidden_patterns_present"]
        or any(item["violations"] for item in report["profiles"].values())
        or any(value is None for value in report["images"].values())
        or any(value is None for value in report["volumes"].values())
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pass": report["pass"], "output": str(args.output), "images": report["images"]}))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
