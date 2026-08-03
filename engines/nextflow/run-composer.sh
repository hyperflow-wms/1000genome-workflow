#!/usr/bin/env bash
# Runs composer.py with the LLM key loaded and a Python that can import
# workflow_composer.
#
#   ./run-composer.sh "Your research question."
#   ./run-composer.sh --dry-run "Your research question."
#
# Optional overrides: CONDA_ENV, CONDA_SH, ENV_FILE.
set -euo pipefail
THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/../.." && pwd)"

: "${CONDA_ENV:=1000genome}"
: "${ENV_FILE:=$REPO_ROOT/.env}"

if [[ -f "$ENV_FILE" ]]; then
    set -a; source "$ENV_FILE"; set +a
else
    echo "[run-composer] no $ENV_FILE -- set GEMINI_API_KEY yourself, or pass --intent-json" >&2
fi

# Prefer a conda environment when one exists, since the optional LLM extras
# usually live there; otherwise the system interpreter is enough as long as
# workflow_composer is importable.
PYTHON="python3"
if [[ -z "${CONDA_SH:-}" ]] && command -v conda >/dev/null 2>&1; then
    CONDA_SH="$(conda info --base)/etc/profile.d/conda.sh"
fi
if [[ -n "${CONDA_SH:-}" && -f "$CONDA_SH" ]]; then
    # shellcheck disable=SC1090
    source "$CONDA_SH"
    if conda activate "$CONDA_ENV" 2>/dev/null; then
        PYTHON="$CONDA_PREFIX/bin/python3"
    fi
fi

if ! "$PYTHON" -c "import workflow_composer" >/dev/null 2>&1; then
    export PYTHONPATH="$REPO_ROOT/workflow-composer/src${PYTHONPATH:+:$PYTHONPATH}"
fi

exec "$PYTHON" "$THIS_DIR/composer.py" "$@"
