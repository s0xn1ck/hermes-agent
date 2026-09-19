"""Boundary-upgrade regression supplement for #115638 and #115959.

An old writer cannot acquire the new inventory keyword retroactively. This is a
characterization, not permission to infer an unknown obligation from a live row.
Real harmless child processes and the canonical status/fleet readers are used;
only profile enumeration, socket discovery and checkout identity are scoped.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from gateway import status
from hermes_cli import update_cmd_fleet as fleet, update_receipt
from hermes_constants import get_hermes_home


def _outgoing_writer(path, expected_sha):
    # Write body from e19efba7f0c007c616771441e8c435346212d2be,
    # parent of c0aa3ce354 (inventory introduction). Path is injected rather
    # than importing the historical module and its obsolete dependencies.
    lines = [f"started={time.time()}", f"pid={os.getpid()}"]
    if expected_sha:
        lines.append(f"expected_sha={expected_sha}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.spawns_gateway_lookalike
@pytest.mark.parametrize("inventory", [False, True], ids=["outgoing-writer", "owned-inventory-control"])
def test_replaced_gateway_does_not_reconstruct_unknown_inventory(monkeypatch, tmp_path, capsys, inventory):
    home = get_hermes_home()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(update_receipt, "_profile_homes", lambda: [("default", home)])
    monkeypatch.setattr(update_receipt, "_socket_identity", lambda home: None)
    monkeypatch.setattr(update_receipt, "_code_identity", lambda **kw: {"sha": "b" * 40})
    monkeypatch.setattr(fleet, "_current_checkout_sha", lambda: "b" * 40)
    monkeypatch.setattr("hermes_cli.update_cmd._current_checkout_sha", lambda: "b" * 40)
    # This script is only a pipe reader, not Hermes or a service. Its canonical
    # entrypoint-shaped argv exercises the real process identity fallback.
    script = tmp_path / "hermes_cli" / "main.py"
    script.parent.mkdir()
    script.write_text("import sys\nprint('ready', flush=True)\nsys.stdin.read()\n")
    marker = fleet._fleet_restart_pending_marker_path()
    children = []

    def start(sha):
        child = subprocess.Popen(
            [sys.executable, str(script), "gateway", "run"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            env={"HOME": str(tmp_path), "HERMES_HOME": str(home), "PATH": os.defpath},
        )
        children.append(child)
        assert child.stdout.readline().strip() == "ready"
        record = {"kind": "hermes-gateway", "pid": child.pid,
                  "start_time": status.get_process_start_time(child.pid),
                  "hermes_home": str(home), "gateway_state": "running", "code_sha": sha,
                  "argv": [sys.executable, str(script), "gateway", "run"]}
        (home / "gateway_state.json").write_text(json.dumps(record))
        assert status.live_gateway_pid_for_home(home) == child.pid
        return child

    try:
        old = start("a" * 40)
        _outgoing_writer(marker, "b" * 40)
        if inventory:
            fleet._write_fleet_restart_pending_marker(
                expected_sha="b" * 40, runtimes=[{"kind": "gateway", "profile": "default"}])
        before = marker.read_bytes()
        assert update_receipt.collect_fleet_versions()[0]["state"] == "stale"
        assert fleet._pending_fleet_restart_needed()
        old.stdin.close()
        old.wait(timeout=5)
        replacement = start("b" * 40)
        assert replacement.pid != old.pid
        rows = update_receipt.collect_fleet_versions()
        assert rows[0]["state"] == "current"
        assert rows[0]["code_sha"] == "b" * 40
        assert fleet._pending_fleet_restart_needed() is (not inventory)
        fleet._warn_pending_fleet_restart_on_startup()
        assert ("hermes gateway restart" in capsys.readouterr().err) is (not inventory)
        if not inventory:
            assert marker.read_bytes() == before
        else:
            assert not marker.exists()
    finally:
        for child in children:
            if child.poll() is None:
                child.stdin.close()
                child.wait(timeout=5)
            child.stdout.close()
