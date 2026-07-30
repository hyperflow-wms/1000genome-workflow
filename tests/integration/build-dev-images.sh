#!/bin/bash
# Builds the local development images used by run-research-tests.sh --dev-images:
#   hyperflowwms/hyperflow:dev          - engine, from a local hyperflow checkout
#   hyperflowwms/1000genome-worker:dev  - worker, with the job executor installed
#                                         from a local checkout via 'npm pack'
#
# Source locations (override via environment):
#   HYPERFLOW_SRC  - hyperflow engine checkout (default: $HOME/hyperflow)
#   EXECUTOR_SRC   - hyperflow-job-executor checkout (default: $HOME/hyperflow-job-executor)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

HYPERFLOW_SRC="${HYPERFLOW_SRC:-$HOME/hyperflow}"
EXECUTOR_SRC="${EXECUTOR_SRC:-$HOME/hyperflow-job-executor}"

[ -d "$HYPERFLOW_SRC" ] || { echo "Engine checkout not found: $HYPERFLOW_SRC" >&2; exit 1; }
[ -d "$EXECUTOR_SRC" ] || { echo "Executor checkout not found: $EXECUTOR_SRC" >&2; exit 1; }

echo "Building hyperflowwms/hyperflow:dev from $HYPERFLOW_SRC ..."
docker build -t hyperflowwms/hyperflow:dev "$HYPERFLOW_SRC"

echo "Building hyperflowwms/1000genome-worker:dev with executor from $EXECUTOR_SRC ..."
make -C "$REPO_ROOT/worker-image" image-dev EXECUTOR_SRC="$EXECUTOR_SRC"

echo "Done. Run the tests with: ./run-research-tests.sh --dev-images ..."
