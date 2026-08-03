#!/usr/bin/env bash
# Uruchamia GUI composera: aktywuje conda env, laduje klucz LLM, startuje serwer.
# Konfiguracja (opcjonalna, nadpisywalna zmiennymi srodowiskowymi) — patrz SETUP.md:
#   CONDA_SH   - sciezka do conda.sh (domyslnie miniconda w Homebrew)
#   CONDA_ENV  - nazwa env conda (domyslnie 1000genome)
#   ENV_FILE   - plik z kluczem API (GEMINI_API_KEY), domyslnie ../1000genome-workflow/.env
set -euo pipefail
THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${CONDA_SH:=/opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh}"
: "${CONDA_ENV:=1000genome}"
: "${ENV_FILE:=$THIS_DIR/../1000genome-workflow/.env}"

if [[ ! -f "$CONDA_SH" ]] && command -v conda >/dev/null 2>&1; then
  CONDA_SH="$(conda info --base)/etc/profile.d/conda.sh"
fi
[[ -f "$CONDA_SH" ]] || { echo "Nie znaleziono conda.sh (ustaw CONDA_SH). Patrz SETUP.md"; exit 1; }

# shellcheck disable=SC1090
source "$CONDA_SH"
conda activate "$CONDA_ENV"

if [[ -f "$ENV_FILE" ]]; then set -a; source "$ENV_FILE"; set +a
else echo "UWAGA: brak $ENV_FILE — bez klucza LLM interpretacja intentu nie zadziala."; fi

exec "$CONDA_PREFIX/bin/python3" "$THIS_DIR/gui.py"
