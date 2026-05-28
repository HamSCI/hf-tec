"""CLIENT-CONTRACT §14 — JSON config-roundtrip surface.

hf-gps-tec didn't have a `config` subcommand at all before this
module — whiptail-style wizards were never written.  This adds the
JSON contract so sigmond's in-TUI Textual config wizard can edit
hf-gps-tec's per-instance config in place of dropping the operator
to $EDITOR.

  * ``hf-gps-tec config show --json [--defaults] [--config PATH]``
    reads the TOML file on disk and emits it as JSON on stdout.
    ``--defaults`` is accepted but currently a no-op (hf-gps-tec
    has no canonical DEFAULTS dict; the file is the source of truth).

  * ``hf-gps-tec config apply --json -`` reads a JSON dict from
    stdin, deep-merges into the existing TOML, and atomically
    rewrites via ``.part`` + rename.

Section whitelist matches hf-gps-tec's actual schema: [station],
[ka9q], [processing], [transmitters], [mode], [sinks], [instance],
plus the ``[[frequency]]`` array-of-tables (PRN beacon frequency
slots).  Per-key type validation is structural only; sigmond's
wizard owns input typing on its end.

Operator-callable note: ``/etc/hf-gps-tec/`` is mode 0750 owned by
the hfgpstec service user, so an unprivileged operator running
sigmond's TUI can't read it.  Sigmond's config wizard always
invokes ``apply`` under sudo via ``mutation.confirm_and_run``, so
that side is fine.  ``show`` works for operators because sigmond's
``Configuration`` screen runs it under sudo on behalf of the
wizard.

Pattern lifted from wspr-recorder commit ad8f637 — closest match
since hf-gps-tec lacks a DEFAULTS dict.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


DEFAULT_CONFIG_PATH = Path(
    os.environ.get("HF_GPS_TEC_CONFIG")
    or "/etc/hf-gps-tec/hf-gps-tec-config.toml"
)


_APPLY_ALLOWED_SECTIONS = {
    "instance",
    "station",
    "ka9q",
    "processing",
    "transmitters",
    "mode",
    "sinks",
    "frequency",  # array-of-tables (PRN beacon slots)
}


def cmd_config_show(args) -> int:
    """Emit the on-disk TOML as JSON on stdout.

    Operator-callable caveat: when ``/etc/hf-gps-tec/`` is 0750 owned
    by the hfgpstec service user, an unprivileged operator running
    sigmond's TUI gets PermissionError on stat() of the config file —
    sigmond's probe fails and falls back to the whiptail flow.  Run
    under sudo for the JSON path; the in-TUI wizard route lights up
    when sigmond invokes show under sudo (planned follow-up).
    """
    config_path = Path(getattr(args, "config", None) or DEFAULT_CONFIG_PATH)
    # Python 3.11+ raises PermissionError from Path.is_file() when the
    # parent dir is unreadable; catch it cleanly so we emit a useful
    # error rather than a traceback.  Same shape as the
    # _safe_exists() helper in sigmond.instance / sigmond.lifecycle.
    try:
        is_file = config_path.is_file()
    except (PermissionError, OSError):
        is_file = True   # presume present; open() will give the real error
    if not is_file:
        out: dict = {}
    else:
        try:
            with open(config_path, "rb") as f:
                out = tomllib.load(f)
        except (PermissionError, OSError, tomllib.TOMLDecodeError) as exc:
            print(f"config show: cannot read {config_path}: {exc}",
                  file=sys.stderr)
            return 2
    json.dump(out, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
    return 0


def cmd_config_apply(args) -> int:
    """Read a JSON dict on stdin, validate, atomically write the TOML.

    Section whitelist + structural checks; ``[[frequency]]`` is a
    list-of-dicts (PRN beacon frequency slots).  No per-key type
    enforcement — sigmond's wizard owns input typing on its end.
    """
    config_path = Path(getattr(args, "config", None) or DEFAULT_CONFIG_PATH)

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"config apply: stdin is not valid JSON: {exc}",
              file=sys.stderr)
        return 2

    if not isinstance(payload, dict):
        print(f"config apply: top-level JSON must be an object, "
              f"got {type(payload).__name__}", file=sys.stderr)
        return 2

    unknown = set(payload.keys()) - _APPLY_ALLOWED_SECTIONS
    if unknown:
        print(f"config apply: section(s) not writable via apply: "
              f"{sorted(unknown)} "
              f"(allowed: {sorted(_APPLY_ALLOWED_SECTIONS)})",
              file=sys.stderr)
        return 2

    for section, fields in payload.items():
        if section == "frequency":
            if not isinstance(fields, list):
                print(f"config apply: [[frequency]] must be a list, "
                      f"got {type(fields).__name__}", file=sys.stderr)
                return 2
            continue
        if not isinstance(fields, dict):
            print(f"config apply: [{section}] must be a table, "
                  f"got {type(fields).__name__}", file=sys.stderr)
            return 2

    if config_path.is_file():
        with open(config_path, "rb") as f:
            existing = tomllib.load(f)
    else:
        existing = {}
    merged = _deep_merge(existing, payload)

    text = _serialize_toml(merged)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(config_path.suffix + ".part")
    tmp.write_text(text, encoding="utf-8")
    try:
        tmp.chmod(0o640)
    except PermissionError:
        pass
    tmp.replace(config_path)
    print(f"wrote {config_path}")
    return 0


# ---------------------------------------------------------------------------
# Helpers — identical to the wspr/hfdl/codar/hf-timestd versions.
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _toml_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        s = repr(v)
        if "." not in s and "e" not in s and "E" not in s:
            s += ".0"
        return s
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    raise TypeError(f"unsupported TOML scalar type: {type(v).__name__}")


def _toml_inline_array(arr: list) -> str:
    parts = []
    for x in arr:
        if isinstance(x, (str, bool, int, float)):
            parts.append(_toml_scalar(x))
        else:
            parts.append(json.dumps(x))
    return "[" + ", ".join(parts) + "]"


def _serialize_toml(d: dict, parent: str = "") -> str:
    """Serialize ``d`` to a deterministic TOML string.

    Handles scalars, nested dicts (`[section.child]`), and arrays-of-
    tables (`[[section]]`).  Arrays of scalars render inline.  Keys
    sorted within each section.  Comments NOT preserved.
    """
    lines: list[str] = []
    scalars: list[tuple[str, object]] = []
    nested: list[tuple[str, dict]] = []
    array_of_tables: list[tuple[str, list]] = []
    for k in sorted(d.keys()):
        v = d[k]
        if isinstance(v, dict):
            nested.append((k, v))
        elif (isinstance(v, list) and v
              and all(isinstance(item, dict) for item in v)):
            array_of_tables.append((k, v))
        else:
            scalars.append((k, v))
    if scalars:
        if parent:
            lines.append(f"[{parent}]")
        for k, v in scalars:
            if isinstance(v, list):
                lines.append(f"{k} = {_toml_inline_array(v)}")
            else:
                lines.append(f"{k} = {_toml_scalar(v)}")
        lines.append("")
    for k, sub in nested:
        header = f"{parent}.{k}" if parent else k
        lines.append(_serialize_toml(sub, parent=header))
    for k, blocks in array_of_tables:
        header = f"{parent}.{k}" if parent else k
        for block in blocks:
            lines.append(f"[[{header}]]")
            for bk in sorted(block.keys()):
                bv = block[bk]
                if isinstance(bv, dict):
                    lines.append(_serialize_toml({bk: bv}, parent=header))
                elif isinstance(bv, list):
                    lines.append(f"{bk} = {_toml_inline_array(bv)}")
                else:
                    lines.append(f"{bk} = {_toml_scalar(bv)}")
            lines.append("")
    return "\n".join(lines)
