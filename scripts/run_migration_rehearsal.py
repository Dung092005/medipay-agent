#!/usr/bin/env python3
"""Run the ordered migrations on a disposable PostgreSQL Docker volume."""

from __future__ import annotations

import argparse
import json
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=ROOT, check=check, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"command failed ({exc.returncode}): {' '.join(args)}\n{detail}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default="medipay-corpus-worker:latest")
    args = parser.parse_args()
    suffix = uuid.uuid4().hex[:10]
    volume = f"medipay_migration_rehearsal_{suffix}"
    container = f"medipay-migration-rehearsal-{suffix}"
    report: dict[str, object] = {"volume": volume, "container": container, "pass": False}
    try:
        run("docker", "volume", "create", volume)
        run(
            "docker", "run", "-d", "--name", container,
            "-e", "POSTGRES_USER=medipay", "-e", "POSTGRES_PASSWORD=medipay-rehearsal-only",
            "-e", "POSTGRES_DB=medipay", "-v", f"{volume}:/var/lib/postgresql/data",
            "postgres:16.4-alpine",
        )
        ready = False
        for _ in range(30):
            probe = run("docker", "exec", container, "pg_isready", "-U", "medipay", "-d", "medipay", check=False)
            if probe.returncode == 0:
                ready = True
                break
        if not ready:
            raise RuntimeError("disposable PostgreSQL did not become ready")
        db_url = "postgresql://medipay:medipay-rehearsal-only@127.0.0.1:5432/medipay"
        command = [
            "docker", "run", "--rm", "--network", f"container:{container}",
            "-v", f"{ROOT}:/workspace", "-w", "/workspace", "--entrypoint", "python", args.image,
            "database/postgres/migrations/runner.py", "--database-url", db_url,
        ]
        first = run(*command)
        second = run(*command)
        report.update({
            "first_apply": [line for line in first.stdout.splitlines() if line.startswith("apply ")],
            "second_apply": [line for line in second.stdout.splitlines() if line.startswith("apply ")],
            "first_skip_count": sum(line.startswith("skip ") for line in first.stdout.splitlines()),
            "second_skip_count": sum(line.startswith("skip ") for line in second.stdout.splitlines()),
            "pass": first.returncode == 0 and second.returncode == 0 and not [line for line in second.stdout.splitlines() if line.startswith("apply ")],
        })
    finally:
        run("docker", "rm", "-f", container, check=False)
        run("docker", "volume", "rm", volume, check=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pass": report["pass"], "output": str(args.output)}))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
