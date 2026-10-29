#!/usr/bin/env bash
# Explicit HTTP launch; does not create tokens or a public tunnel.
set -eo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/.."
source "$HERE/profile.sh"
exec python3 -m serve.serve_api
