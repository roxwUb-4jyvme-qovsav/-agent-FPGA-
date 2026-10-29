#!/usr/bin/env bash
# Called only by the explicitly authorized deployment startup path.
set -eo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/.."
source "$HERE/profile.sh"
exec python3 -m serve.deployment_profile
