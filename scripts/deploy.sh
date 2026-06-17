#!/usr/bin/env bash
# hf-tec deploy.sh — refresh the editable install and restart
# running instances.  Idempotent.  Use after pulling new commits.

set -euo pipefail

NAME="hf-tec"
USER="hftec"
GROUP="hftec"
INSTALL_DIR="/opt/${NAME}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
    echo "error: deploy.sh must run as root" >&2
    exit 1
fi

PULL=0
for arg in "$@"; do
    case "$arg" in
        --pull) PULL=1 ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

if [[ "${PULL}" -eq 1 ]]; then
    sudo -u "${USER}" git -C "${REPO_ROOT}" pull --ff-only
fi

_ENSURE_UV_SH="/opt/git/sigmond/sigmond/scripts/install/ensure_uv.sh"
if [[ -r "${_ENSURE_UV_SH}" ]]; then
    # shellcheck source=/dev/null
    source "${_ENSURE_UV_SH}"
else
    _ensure_uv() { command -v uv >/dev/null 2>&1; }
fi
_ensure_uv || { echo "error: uv not found; run install.sh first" >&2; exit 1; }

UV_PROJECT_ENVIRONMENT="${INSTALL_DIR}/venv" uv sync \
    --project "${REPO_ROOT}" \
    --no-dev \
    --quiet
chown -R "${USER}:${GROUP}" "${INSTALL_DIR}/venv"

# Restart any active instances.
mapfile -t INSTANCES < <(systemctl list-units --no-legend --plain --state=active 'hf-tec@*.service' | awk '{print $1}')
if [[ "${#INSTANCES[@]}" -gt 0 ]]; then
    echo "restarting: ${INSTANCES[*]}"
    systemctl restart "${INSTANCES[@]}"
else
    echo "no active hf-tec instances; nothing to restart"
fi
