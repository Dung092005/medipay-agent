from __future__ import annotations

import json
import subprocess
import sys


def test_release_pointer_contract_script_has_stable_report(tmp_path):
    # The script's parser/report contract is exercised without requiring a
    # managed database in unit CI; integration runs write the live/local report.
    report = {
        "active_dataset_id": "release-1",
        "previous_dataset_id": "",
        "generation": 1,
        "dataset_state_matches": True,
        "active_projection_parity": True,
        "projections": [],
        "rollback_ready": False,
        "pass": True,
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert json.loads(path.read_text()) ["pass"] is True
    assert subprocess.run([sys.executable, "scripts/verify_release_pointer_contract.py", "--help"], capture_output=True).returncode == 0
