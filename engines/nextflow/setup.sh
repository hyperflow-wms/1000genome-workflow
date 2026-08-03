#!/usr/bin/env bash
# One-time bootstrap for running either engine locally: builds the worker
# images, pulls what the HyperFlow harness needs, and checks the tooling.
#
#   bash engines/nextflow/setup.sh
set -uo pipefail
THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/../.." && pwd)"
ok(){   echo "  [ok]   $*"; }
warn(){ echo "  [warn] $*"; }

# The Nextflow worker derives from the shared base, so its tag follows
# worker-base-image rather than being pinned here.
BASE_VERSION="$(grep -E '^VERSION' "$REPO_ROOT/worker-base-image/Makefile" | head -1 | tr -d ' ' | cut -d= -f2)"
NF_IMAGE="1000genome-worker-nf:${BASE_VERSION}"

echo "== 1. Tooling =="
command -v docker >/dev/null && ok "docker: $(docker --version 2>/dev/null)" \
  || warn "docker missing -- install Docker Desktop and enable WSL integration"
if command -v nextflow >/dev/null; then
  ok "nextflow: $(nextflow -version 2>&1 | grep -oE 'version [0-9.]+' | head -1)"
else
  warn "nextflow missing. Install it:
         curl -fsSL -o ~/.local/bin/nextflow \\
           https://github.com/nextflow-io/nextflow/releases/download/v25.10.2/nextflow
         chmod +x ~/.local/bin/nextflow
       Pin NXF_VER=25.10.2; 26.x does not parse nf-core style configs."
fi
[ -f "$REPO_ROOT/.env" ] && ok "LLM key file present ($REPO_ROOT/.env)" \
  || warn "no $REPO_ROOT/.env -- interpretation needs GEMINI_API_KEY, or pass --intent-json"

echo "== 2. Worker images =="
# The base carries the analysis scripts and both engines build on it. Build it
# rather than pulling: a change under worker-base-image/scripts/ bumps the
# version, and that tag does not exist in the registry until it is published.
if make -C "$REPO_ROOT/worker-base-image" image >/dev/null 2>&1; then
  ok "built hyperflowwms/1000genome-worker-base:${BASE_VERSION}"
else
  warn "could not build the base image"
fi
if make -C "$REPO_ROOT/worker-image" image >/dev/null 2>&1; then
  ok "built the HyperFlow worker image"
else
  warn "could not build the HyperFlow worker image"
fi
if docker build --platform linux/amd64 -f "$THIS_DIR/worker-nf.Dockerfile" \
     -t "$NF_IMAGE" "$THIS_DIR" >/dev/null 2>&1; then
  ok "built $NF_IMAGE"
else
  warn "could not build $NF_IMAGE"
fi
echo "  (nextflow.config must name $NF_IMAGE)"

echo "== 3. Images the HyperFlow harness pulls =="
for img in "hyperflowwms/hyperflow:v1.11.1" "redis:7-alpine" \
           "hyperflowwms/1000genome-data:1.0" "broadinstitute/gatk:4.4.0.0"; do
  if docker image inspect "$img" >/dev/null 2>&1; then ok "present: $img"
  else docker pull "$img" >/dev/null 2>&1 && ok "pulled: $img" || warn "could not pull: $img"; fi
done
echo "  (the htslib image used by EXTRACT is pulled on first run)"

echo "== 4. Python =="
if python3 -c "import workflow_composer" >/dev/null 2>&1; then
  ok "workflow_composer importable"
else
  warn "workflow_composer not importable -- pip install -e $REPO_ROOT/workflow-composer"
fi

echo
echo "Done. Next:"
echo "  engines/nextflow/run-composer.sh \"Analyze BRCA1 variants in the British population.\""
echo "  GUI_HOST=0.0.0.0 gui/run-gui.sh     # then http://localhost:8765"
