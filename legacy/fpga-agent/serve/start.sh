#!/usr/bin/env bash
# The sole resident-inference startup entry. HTTP transport is started separately.
# No `set -u`: vendor settings scripts can reference unset variables.
set -eo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/.."
source "$HERE/profile.sh"
exec python3 -m serve.startup
