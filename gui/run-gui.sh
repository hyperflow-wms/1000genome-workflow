#!/usr/bin/env bash
# Starts the composer GUI: loads the LLM key, resolves a Python that can import
# workflow_composer, and serves on port 8765.
#
# Optional environment overrides:
#   CONDA_ENV  conda environment to activate, if conda is present
#   CONDA_SH   path to conda.sh, when it is not on the default search path
#   ENV_FILE   file holding GEMINI_API_KEY (default: repository root .env)
#   GUI_HOST   bind address; set to 0.0.0.0 to reach the GUI from a Windows
#              browser under WSL when localhost forwarding is not in play
set -euo pipefail
THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/.." && pwd)"

: "${CONDA_ENV:=1000genome}"
: "${ENV_FILE:=$REPO_ROOT/.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
else
  echo "note: no $ENV_FILE -- intent interpretation needs an LLM key to run."
fi

# Prefer a conda environment when one is available, since that is where the
# optional LLM extras usually live. Fall back to the system interpreter, which
# is enough whenever workflow_composer is importable.
PYTHON="python3"
if [[ -z "${CONDA_SH:-}" ]] && command -v conda >/dev/null 2>&1; then
  CONDA_SH="$(conda info --base)/etc/profile.d/conda.sh"
fi
if [[ -n "${CONDA_SH:-}" && -f "$CONDA_SH" ]]; then
  # shellcheck disable=SC1090
  source "$CONDA_SH"
  if conda activate "$CONDA_ENV" 2>/dev/null; then
    PYTHON="$CONDA_PREFIX/bin/python3"
  else
    echo "note: conda env '$CONDA_ENV' not found -- using $PYTHON"
  fi
fi

# Let an editable checkout work without installing the package.
if ! "$PYTHON" -c "import workflow_composer" >/dev/null 2>&1; then
  export PYTHONPATH="$REPO_ROOT/workflow-composer/src${PYTHONPATH:+:$PYTHONPATH}"
fi

echo "GUI: http://localhost:8765"
exec "$PYTHON" "$THIS_DIR/gui.py"
