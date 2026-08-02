#!/usr/bin/env bash
# Wrapper: conda env (workflow_composer + litellm), klucz Gemini, composer.py
# Uzycie:
#   ./run-composer.sh "Twoje pytanie badawcze"
#   ./run-composer.sh --dry-run "Twoje pytanie"
set -euo pipefail

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$THIS_DIR/../1000genome-workflow/.env"

source /opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh
conda activate 1000genome

if [[ -f "$ENV_FILE" ]]; then
    set -a; source "$ENV_FILE"; set +a
else
    echo "[run-composer] BRAK $ENV_FILE — ustaw GEMINI_API_KEY recznie" >&2
fi

"$CONDA_PREFIX/bin/python3" "$THIS_DIR/composer.py" "$@"
