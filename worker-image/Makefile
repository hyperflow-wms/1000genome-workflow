REPO_NAME = 1000genome-worker
PREFIX = hyperflowwms
VERSION = 1.3
# ?= so CI and Renovate can override the version from the environment
HF_JOB_EXECUTOR_VERSION ?= 1.4.1
TAG = $(VERSION)-je$(HF_JOB_EXECUTOR_VERSION)

# Local job-executor checkout used by the 'image-dev' target
EXECUTOR_SRC ?= $(HOME)/hyperflow-job-executor
DEV_TAG = $(PREFIX)/$(REPO_NAME):dev

all: push

image:
	docker build --build-arg hf_job_executor_version=$(HF_JOB_EXECUTOR_VERSION) -t $(PREFIX)/$(REPO_NAME):$(TAG) .
	docker tag $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):$(VERSION)-latest

# Dev image: installs the job executor from a local checkout (npm pack)
# instead of the npm registry
image-dev:
	cd $(EXECUTOR_SRC) && npm pack --pack-destination $(CURDIR)
	docker build --build-arg local_executor_tgz=$$(ls hyperflow-job-executor-*.tgz | head -1) \
	  --build-arg hf_job_executor_version=$(HF_JOB_EXECUTOR_VERSION) -t $(DEV_TAG) .
	rm -f hyperflow-job-executor-*.tgz

push: image
	docker push $(PREFIX)/$(REPO_NAME):$(TAG)
	docker push $(PREFIX)/$(REPO_NAME):$(VERSION)-latest

clean:
	docker rmi $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):$(VERSION)-latest $(DEV_TAG) || true

.PHONY: all image image-dev push clean
