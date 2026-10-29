"""The writer comes from hs_uploader; sigmond paths stay optional in units."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

UNITS = ["systemd/hf-tec@.service"]


def test_writer_comes_from_hs_uploader_without_sigmond(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "sigmond", None)
    monkeypatch.setitem(sys.modules, "sigmond.hamsci_sink", None)
    monkeypatch.setenv("SIGMOND_SQLITE_PATH", str(tmp_path / "sink.db"))
    import hs_uploader.sink as sink

    seen = []

    # _HamsciSinkWriter calls Writer.from_env(table=...) with no `mode`,
    # which the real Writer rejects (TypeError, swallowed, writer disabled).
    # That is an older defect, tracked apart from this import change, so the
    # test records which class the resolver reached instead of building one.
    def spy(cls, *a, **kw):
        seen.append(cls)
        return object()

    monkeypatch.setattr(sink.Writer, "from_env", classmethod(spy))
    from hf_tec.core.output import _HamsciSinkWriter
    out = _HamsciSinkWriter(table="hf_tec.spots")
    assert out._writer is not None
    assert seen and seen[0].__module__ == "hs_uploader.sink.writer"


@pytest.mark.parametrize("unit", UNITS)
def test_unit_marks_sigmond_paths_optional(unit):
    found = 0
    for line in (ROOT / unit).read_text().splitlines():
        m = re.match(r"\s*ReadWritePaths=(.*)", line)
        if not m:
            continue
        for tok in m.group(1).split():
            if "/var/lib/sigmond" in tok or "/var/lib/hs-uploader" in tok:
                found += 1
                assert tok.startswith("-"), f"{unit}: {tok}"
    assert found, f"{unit}: no sigmond/hs-uploader ReadWritePaths entry"

