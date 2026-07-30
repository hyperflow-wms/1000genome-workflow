FROM hyperflowwms/1000genome-worker-base:1.3

# Version of the job executor should be passed via docker build, e.g.:
# docker build --build-arg hf_job_executor_version="1.3.4"
ARG hf_job_executor_version

# Optional: name of a locally built 'npm pack' tarball of the job executor
# (placed in the build context). When set, it is installed instead of the
# registry version, e.g.:
# docker build --build-arg local_executor_tgz="hyperflow-job-executor-1.4.1.tgz"
ARG local_executor_tgz=

ENV HYPERFLOW_JOB_EXECUTOR_VERSION=$hf_job_executor_version

# The 'Dockerfile' anchor keeps the wildcard COPY valid when no tarball is present
COPY Dockerfile ${local_executor_tgz}* /tmp/build/
RUN if [ -n "$local_executor_tgz" ]; then \
      npm install -g /tmp/build/${local_executor_tgz}; \
    else \
      npm install -g @hyperflow/job-executor@${HYPERFLOW_JOB_EXECUTOR_VERSION}; \
    fi && rm -rf /tmp/build

# Required for running NodeJS application as process with non primary PID.
# NodeJS application with PID 1 are not properly receiving signals like SIGTERM
# https://github.com/nodejs/docker-node/blob/main/docs/BestPractices.md#handling-kernel-signals
RUN wget -O /usr/local/bin/dumb-init https://github.com/Yelp/dumb-init/releases/download/v1.2.5/dumb-init_1.2.5_x86_64
RUN chmod +x /usr/local/bin/dumb-init
ENTRYPOINT ["/usr/local/bin/dumb-init", "--"]
