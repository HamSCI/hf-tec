#!/usr/bin/env bash
# hf-tec install.sh — first-run bootstrap (Pattern A).
#
# Idempotent.  Creates the service user, builds the venv via uv (sourced
# from sigmond's shared ensure_uv helper), installs the venv at
# /opt/hf-tec/venv, links the CLI shim and systemd unit, and
# renders config templates if they are not already present.

set -euo pipefail

NAME="hf-tec"
USER="hftec"
GROUP="hftec"
INSTALL_DIR="/opt/${NAME}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF_DIR="/etc/${NAME}"
DATA_DIR="/var/lib/${NAME}"
LOG_DIR="/var/log/${NAME}"

BUILD_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --build-only) BUILD_ONLY=1 ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

if [[ "${EUID}" -ne 0 ]]; then
    echo "error: install.sh must run as root" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. Service user
# ---------------------------------------------------------------------------

if ! getent group "${GROUP}" >/dev/null; then
    groupadd --system "${GROUP}"
fi
if ! id "${USER}" >/dev/null 2>&1; then
    useradd --system --gid "${GROUP}" --home-dir "${INSTALL_DIR}" \
            --shell /usr/sbin/nologin --comment "hf-tec" "${USER}"
fi

# ---------------------------------------------------------------------------
# 2. uv (via sigmond's shared helper, with inline fallback)
# ---------------------------------------------------------------------------

_ENSURE_UV_SH="/opt/git/sigmond/sigmond/scripts/install/ensure_uv.sh"
if [[ -r "${_ENSURE_UV_SH}" ]]; then
    # shellcheck source=/dev/null
    source "${_ENSURE_UV_SH}"
else
    _ensure_uv() {
        if command -v uv >/dev/null 2>&1; then return 0; fi
        echo "installing uv via Astral installer..."
        curl -fsSL https://astral.sh/uv/install.sh | env INSTALLER_NO_MODIFY_PATH=1 sh
        export PATH="/usr/local/bin:/root/.local/bin:${PATH}"
        command -v uv >/dev/null 2>&1
    }
fi
_ensure_uv || { echo "error: could not provision uv" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 3. venv at /opt/<name>/venv (canonical Pattern A location)
# ---------------------------------------------------------------------------

install -d -m 0755 -o "${USER}" -g "${GROUP}" "${INSTALL_DIR}"
if [[ ! -x "${INSTALL_DIR}/venv/bin/python3" ]]; then
    uv venv "${INSTALL_DIR}/venv" --python 3.11 --seed --quiet
    chown -R "${USER}:${GROUP}" "${INSTALL_DIR}/venv"
fi

# uv sync reads pyproject.toml + uv.lock + [tool.uv.sources] (siblings),
# producing the editable install in one shot.  --frozen would require a
# committed uv.lock; we omit it during scaffolding so the first install
# resolves freely.
UV_PROJECT_ENVIRONMENT="${INSTALL_DIR}/venv" uv sync \
    --project "${REPO_ROOT}" \
    --no-dev \
    --quiet
chown -R "${USER}:${GROUP}" "${INSTALL_DIR}/venv"

# Optional: editable install of sigmond (orchestrator), matching the rest
# of the suite.  Silent no-op if sigmond isn't cloned alongside.
if [[ -d /opt/git/sigmond/sigmond ]]; then
    uv pip install --quiet --python "${INSTALL_DIR}/venv/bin/python3" \
        -e /opt/git/sigmond/sigmond || true
fi

if [[ "${BUILD_ONLY}" -eq 1 ]]; then
    echo "build complete: ${INSTALL_DIR}/venv/bin/${NAME}"
    exit 0
fi

# ---------------------------------------------------------------------------
# 4. CLI shim
# ---------------------------------------------------------------------------

if [[ -e /usr/local/bin/${NAME} && ! -L /usr/local/bin/${NAME} ]]; then
    echo "warning: /usr/local/bin/${NAME} exists and is not a symlink; leaving in place" >&2
else
    ln -sfn "${INSTALL_DIR}/venv/bin/${NAME}" "/usr/local/bin/${NAME}"
fi

# ---------------------------------------------------------------------------
# 5. Config + data directories
# ---------------------------------------------------------------------------

install -d -m 0755 -o "${USER}" -g "${GROUP}" "${CONF_DIR}"
install -d -m 0755 -o "${USER}" -g "${GROUP}" "${DATA_DIR}"
install -d -m 0755 -o "${USER}" -g "${GROUP}" "${LOG_DIR}"

# CONF_DIR + the rendered config must be world-readable so the
# sigmond TUI (which runs as the operator's user, not as the
# client's service user) can list /etc/<client>/ and read every
# per-instance *.toml.  Matches the convention of every other
# sigmond client (hf-timestd, wspr-recorder, psk-recorder, …).
if [[ ! -e "${CONF_DIR}/hf-tec-config.toml" ]]; then
    install -m 0644 -o "${USER}" -g "${GROUP}" \
        "${REPO_ROOT}/config/hf-tec-config.toml.template" \
        "${CONF_DIR}/hf-tec-config.toml"
fi
# stations.toml lives in a subdirectory so sigmond's per-instance
# scanner (lifecycle.py globs /etc/<client>/*.toml) does not misread
# it as a "stations" instance.
install -d -m 0755 -o "${USER}" -g "${GROUP}" "${CONF_DIR}/data"
if [[ ! -e "${CONF_DIR}/data/stations.toml" ]]; then
    install -m 0644 -o "${USER}" -g "${GROUP}" \
        "${REPO_ROOT}/data/stations.toml" \
        "${CONF_DIR}/data/stations.toml"
fi
# Migrate any legacy top-level stations.toml — pre-fix installs left
# it where the instance scanner picks it up.  Move it under data/ if
# the new location doesn't already exist.
if [[ -f "${CONF_DIR}/stations.toml" && ! -e "${CONF_DIR}/data/stations.toml" ]]; then
    mv "${CONF_DIR}/stations.toml" "${CONF_DIR}/data/stations.toml"
fi

# ---------------------------------------------------------------------------
# 6. systemd unit
# ---------------------------------------------------------------------------

ln -sfn "${REPO_ROOT}/systemd/hf-tec@.service" \
        /etc/systemd/system/hf-tec@.service

# QA snapshot timer (every 15 min): periodic `hf-tec qa --json`
# capture for trend monitoring.  Templated per-instance just like the
# recorder; PartOf the recorder so disabling the recorder also halts
# snapshots.  Operator enables manually per instance — same pattern as
# the recorder, no auto-enable.
install -d -m 0755 -o "${USER}" -g "${GROUP}" "${INSTALL_DIR}/bin"
install -m 0755 -o "${USER}" -g "${GROUP}" \
    "${REPO_ROOT}/scripts/qa-snapshot.sh" \
    "${INSTALL_DIR}/bin/qa-snapshot.sh"
ln -sfn "${REPO_ROOT}/systemd/hf-tec-qa@.service" \
        /etc/systemd/system/hf-tec-qa@.service
ln -sfn "${REPO_ROOT}/systemd/hf-tec-qa@.timer" \
        /etc/systemd/system/hf-tec-qa@.timer

systemctl daemon-reload

echo
echo "hf-tec installed."
echo "  venv:      ${INSTALL_DIR}/venv"
echo "  cli:       /usr/local/bin/${NAME}"
echo "  config:    ${CONF_DIR}/hf-tec-config.toml"
echo "  data:      ${DATA_DIR}"
echo "  unit:      /etc/systemd/system/hf-tec@.service"
echo "  qa unit:   /etc/systemd/system/hf-tec-qa@.service"
echo "  qa timer:  /etc/systemd/system/hf-tec-qa@.timer"
echo
echo "next:"
echo "  1. edit ${CONF_DIR}/hf-tec-config.toml"
echo "  2. sudo -u ${USER} ${NAME} validate --json"
echo "  3. sudo systemctl start hf-tec@<radiod-id>"
echo "  4. sudo systemctl enable --now hf-tec-qa@<radiod-id>.timer"
