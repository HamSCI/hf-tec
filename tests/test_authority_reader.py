#!/usr/bin/env python3
"""Unit tests for hf-tec's AuthorityReader + the canonical
timing-provenance block (shared schema across all sigmond clients)."""

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hf_tec.core.authority_reader import (
    AuthorityReader,
    standalone_timing_authority,
)


def _good(**overrides) -> dict:
    base = {
        "schema": "v1",
        "utc_published": "2026-04-23T12:00:00.000000Z",
        "a_level": "A1",
        "t_level_active": "T6",
        "t_level_available": ["T6", "T5"],
        "t_level_witnesses": ["T5"],
        "rtp_to_utc_offset_ns": 4250,
        "sigma_ns": 1000,
        "stations_contributing": [],
        "last_transition_utc": None,
        "disagreement_flags": ["TIMING_DISAGREEMENT"],
        "governor_radiod": "sigma-rx888",
    }
    base.update(overrides)
    return base


class TestAuthorityReader(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "authority.json"
        self.now = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read(self, **overrides):
        with self.path.open("w") as f:
            json.dump(_good(**overrides), f)
        return AuthorityReader(path=self.path, now_fn=lambda: self.now).read()

    def test_offset_usable_and_seconds(self) -> None:
        s = self._read()
        assert s is not None
        self.assertTrue(s.offset_usable)
        self.assertAlmostEqual(s.offset_seconds, 4250 / 1e9)

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(AuthorityReader(path=self.path).read())

    def test_stale_returns_none(self) -> None:
        # published 12:00, now 12:10 with 60 s freshness -> stale
        late = datetime(2026, 4, 23, 12, 10, 0, tzinfo=timezone.utc)
        with self.path.open("w") as f:
            json.dump(_good(), f)
        self.assertIsNone(
            AuthorityReader(path=self.path, now_fn=lambda: late).read()
        )

    def test_no_active_tier_not_usable(self) -> None:
        s = self._read(t_level_active=None, rtp_to_utc_offset_ns=None)
        assert s is not None
        self.assertFalse(s.offset_usable)

    def test_provenance_blocks_share_keys(self) -> None:
        s = self._read()
        assert s is not None
        self.assertEqual(
            set(s.to_timing_authority("r").keys()),
            set(standalone_timing_authority("r").keys()),
        )


if __name__ == "__main__":
    unittest.main()
