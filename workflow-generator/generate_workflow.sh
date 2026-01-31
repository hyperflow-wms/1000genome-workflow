#!/bin/sh
set -e
#export PYTHONPATH=$(pegasus-config --python)
python3 daxgen.py "$@" && hflow-convert-dax 1000genome.dax > workflow.json && rm 1000genome.dax
python3 edit_workflow.py -n 1000genome -v 1.0.0 -p workflow.json
