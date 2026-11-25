#!/bin/sh
#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#
# Run this example with nothing installed but Podman or Docker.
#
#   ./run.sh            stages 1-4
#   ./run.sh --agent    + stage 5 (export API_BASE, API_KEY, MODEL first)
#   ./run.sh --keep     leave this run's records in the live systems
#
# It starts the live systems and runs the example inside a container; see
# ../systems/README.md (or the agenticharvest-systems repo) for what that does.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
# By name, not by folder: a standalone clone's folder is the repository name.
example=order_fulfillment
# A .env next to this script (API_BASE, API_KEY, MODEL) is read by the runner.
export AGENTICHARVEST_CALLER_DIR="$here"

if [ -x "$here/../systems/run.sh" ]; then
  exec "$here/../systems/run.sh" "$example" "$@"
fi

# Cloned on its own: fetch agenticharvest, which carries the stack, and run from it.
cache="$here/.agenticharvest"
if [ ! -x "$cache/examples/systems/run.sh" ]; then
  command -v git >/dev/null 2>&1 || { echo "git is needed to fetch agenticharvest." >&2; exit 1; }
  repo="${AGENTICHARVEST_REPO:-https://github.com/IBM/agenticharvest.git}"
  echo "Fetching agenticharvest ($repo, ${AGENTICHARVEST_REF:-main}) into $cache ..."
  git clone --depth 1 --recurse-submodules --shallow-submodules \
    --branch "${AGENTICHARVEST_REF:-main}" "$repo" "$cache"
fi
exec "$cache/examples/systems/run.sh" "$example" "$@"
