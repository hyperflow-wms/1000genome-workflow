#!/usr/bin/env bash
# Bootstrap srodowiska dla GUI/composera 1000genome (Nextflow + HyperFlow).
# Buduje lokalny obraz worker-nf, pobiera obrazy HyperFlow, sprawdza narzedzia.
# Uruchom RAZ po sklonowaniu: bash setup.sh   (potem: ./run-gui.sh)
set -uo pipefail
THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ok(){ echo "  [OK] $*"; }
warn(){ echo "  [!!] $*"; }

echo "== 1. Narzedzia =="
command -v docker  >/dev/null && ok "docker: $(docker --version 2>/dev/null)" || warn "brak docker — zainstaluj Docker Desktop"
command -v conda   >/dev/null && ok "conda: $(conda --version 2>/dev/null)"   || warn "brak conda — zainstaluj Miniconda"
[ -x "$THIS_DIR/../nextflow-experiments/bin/nextflow" ] && ok "nextflow wrapper obecny" || warn "brak ../nextflow-experiments/bin/nextflow (NXF_VER=25.10.2)"
if [ "$(uname)" = "Darwin" ]; then
  [ -d /opt/homebrew/opt/coreutils/libexec/gnubin ] && ok "coreutils (gnubin) obecne" || warn "brak coreutils — 'brew install coreutils gnu-sed grep bash'"
fi

echo "== 2. Lokalny obraz worker-nf (streaming individuals.py + ANNOTATE) =="
if [ ! -f "$THIS_DIR/individuals.streaming.py" ]; then
  warn "brak individuals.streaming.py — wymagany do COPY w worker-nf.Dockerfile"
else
  docker build --platform linux/amd64 -f "$THIS_DIR/worker-nf.Dockerfile" \
    -t 1000genome-worker-nf:1.3 "$THIS_DIR" && ok "zbudowano 1000genome-worker-nf:1.3" || warn "build worker-nf nieudany"
fi

echo "== 3. Obrazy HyperFlow / dane (pobieranie) =="
IMAGES=(
  "hyperflowwms/1000genome-worker:1.3-je1.4.2"   # streaming worker (HyperFlow EXECUTE)
  "hyperflowwms/hyperflow:v1.11.1"               # silnik HyperFlow
  "redis:7-alpine"                               # kolejka HyperFlow
  "hyperflowwms/1000genome-data:1.0"             # dane micro
  "broadinstitute/gatk:4.4.0.0"                  # tabix (harness EXTRACT)
)
for img in "${IMAGES[@]}"; do
  if docker image inspect "$img" >/dev/null 2>&1; then ok "jest: $img"
  else echo "  pobieram $img ..."; docker pull "$img" >/dev/null 2>&1 && ok "pobrano: $img" || warn "nie pobrano: $img"; fi
done
echo "  (obraz htslib do Nextflow EXTRACT pobiera sie sam przy pierwszym runie)"

echo "== 4. Pakiety Python (conda env) =="
if command -v conda >/dev/null; then
  echo "  Utworz env i zainstaluj zaleznosci (jednorazowo):"
  echo "    conda create -y -n 1000genome python=3.11"
  echo "    conda run -n 1000genome pip install -r '$THIS_DIR/requirements.txt'"
  echo "    conda run -n 1000genome pip install -e '$THIS_DIR/../1000genome-workflow/workflow-composer'"
fi

echo
echo "== Gotowe. Nastepnie: =="
echo "  1) wpisz klucz LLM do  ../1000genome-workflow/.env   (GEMINI_API_KEY=...)"
echo "  2) ./run-gui.sh   ->   http://localhost:8765"
