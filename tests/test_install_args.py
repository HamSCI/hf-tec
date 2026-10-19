"""scripts/install.sh accepts --yes.

sigmond's align and manifest restore run every component's install.sh with
--yes (bin/smd _align_run_install), as bring-up does.  This installer never
prompts, so --yes changes nothing, but rejecting it made a restore or an
align that moved hf-tec fail (exit 2).
"""
import os
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install.sh"


@unittest.skipIf(os.geteuid() == 0, "needs a non-root run to stop at the root check")
class InstallArgsTests(unittest.TestCase):

    def run_it(self, *args):
        return subprocess.run(["bash", str(SCRIPT), *args],
                              capture_output=True, text=True)

    def test_yes_is_accepted(self):
        r = self.run_it("--yes")
        self.assertNotIn("unknown arg", r.stderr)
        self.assertEqual(r.returncode, 1, r.stderr)   # the root check, after the args

    def test_an_unknown_argument_still_fails(self):
        r = self.run_it("--bogus")
        self.assertEqual(r.returncode, 2)
