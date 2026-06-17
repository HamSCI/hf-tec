"""Pin the contract that `inventory --json` returns a parseable
contract payload AND exits 0 when config can't be read.

The motivating bug: an unprivileged operator running `smd config show`
shells out to `hf-tec inventory --json`.  /etc/hf-tec/*.toml
is service-user-owned (mode 0640), so the read raises PermissionError.
If inventory exits non-zero, sigmond's ContractAdapter never reaches
`view.installed = True` and the operator-facing Config view reports
the client as "not installed" — even though it very much is.

Both FileNotFoundError (legacy shared-config absent on a per-instance
host) and PermissionError (service-user-owned config) are covered.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

# Add src/ to path so we can import hf_tec without installing.
_SRC = Path(__file__).resolve().parents[1] / 'src'
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from hf_tec import cli  # noqa: E402


def _make_args() -> argparse.Namespace:
    return argparse.Namespace(config=None, instance=None, stations=None)


class DegradedInventoryTests(unittest.TestCase):

    def _run_inventory_with_load_error(self, exc: Exception) -> tuple[int, dict]:
        buf = io.StringIO()
        with mock.patch(
            "hf_tec.cli.load_config", side_effect=exc,
        ), redirect_stdout(buf):
            rc = cli._handle_inventory(_make_args())
        return rc, json.loads(buf.getvalue())

    def test_permission_error_returns_zero_with_parseable_payload(self):
        """The motivating bug: unprivileged operator can't read
        service-user-owned config.  Inventory must still exit 0 and
        produce contract-shaped JSON so sigmond marks installed=True."""
        rc, payload = self._run_inventory_with_load_error(
            PermissionError(13, "Permission denied",
                            "/etc/hf-tec/hf-tec-config.toml"))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["client"], "hf-tec")
        self.assertEqual(payload["contract_version"], "0.8")
        self.assertEqual(payload["instances"], [])
        self.assertIsNone(payload["config_path"])
        # The degraded state must surface as a fail-severity issue so
        # the operator's UI sees *why* it's degraded.
        self.assertEqual(len(payload["issues"]), 1)
        issue = payload["issues"][0]
        self.assertEqual(issue["severity"], "fail")
        self.assertIn("Permission denied", issue["message"])

    def test_file_not_found_returns_zero_with_parseable_payload(self):
        """Legacy shared-config absent on a per-instance host."""
        rc, payload = self._run_inventory_with_load_error(
            FileNotFoundError(2, "No such file or directory",
                              "/etc/hf-tec/hf-tec-config.toml"))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["client"], "hf-tec")
        self.assertEqual(payload["instances"], [])
        self.assertEqual(len(payload["issues"]), 1)
        self.assertIn("not found", payload["issues"][0]["message"])

    def test_validate_keeps_nonzero_exit_on_config_failure(self):
        """Asymmetric with inventory: validate cannot certify health
        without reading config, so exit nonzero stays the right call."""
        buf = io.StringIO()
        with mock.patch(
            "hf_tec.cli.load_config", side_effect=PermissionError(
                13, "Permission denied", "/etc/hf-tec/x.toml"),
        ), redirect_stdout(buf):
            rc = cli._handle_validate(_make_args())
        self.assertEqual(rc, 1)
        # Same degraded payload printed (so a caller can still inspect
        # why), but the exit code distinguishes inventory from validate.
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["client"], "hf-tec")
        self.assertEqual(payload["issues"][0]["severity"], "fail")

    def test_degraded_payload_carries_all_contract_keys(self):
        """Sigmond's ContractAdapter looks at config_path, log_paths,
        log_level, instances, issues — make sure all top-level keys
        present in a healthy inventory are also present in the
        degraded one, so adapter parsing doesn't blow up downstream
        on a KeyError instead of treating the degraded state cleanly."""
        payload = cli._degraded_inventory_payload("test")
        for key in ("client", "version", "git", "contract_version",
                    "config_path", "log_paths", "log_level",
                    "instances", "deps", "issues"):
            self.assertIn(key, payload, f"missing key: {key}")


if __name__ == "__main__":
    unittest.main()
