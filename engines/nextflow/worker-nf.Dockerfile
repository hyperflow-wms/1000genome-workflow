# Worker image for the Nextflow port.
#
# Derives from the shared base image -- node:20-alpine plus python and the five
# analysis scripts (worker-base-image/) -- rather than from
# hyperflowwms/1000genome-worker, which adds the HyperFlow job executor that
# Nextflow does not use. Both engines therefore run the same scripts from the
# same layer, so "the science is identical" is a property of the image graph
# rather than a claim about two copies staying in sync.
FROM hyperflowwms/1000genome-worker-base:1.5

# Nextflow requires bash in the container; the base is Alpine, which ships ash.
RUN apk add --no-cache bash

# Optional fast mode: frequency.py reads its Monte Carlo iteration count from
# N_RUNS. Without the variable the count stays 1000, so behaviour is unchanged
# and only an explicit opt-in shortens it. Runs used for output equivalence
# must leave N_RUNS unset.
RUN sed -i "s/^n_runs = 1000/n_runs = int(os.environ.get('N_RUNS', 1000))/" /1000genome/scripts/frequency.py

# The base sets no entrypoint; clear it explicitly so a future base change
# cannot collide with the wrapper Nextflow injects.
ENTRYPOINT []
