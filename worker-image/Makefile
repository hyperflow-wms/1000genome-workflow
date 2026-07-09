REPO_NAME = 1000genome-worker
PREFIX = hyperflowwms
VERSION = 1.1
HF_JOB_EXECUTOR_VERSION = 1.4.0
TAG = $(VERSION)-je$(HF_JOB_EXECUTOR_VERSION)

all: push

image:
	docker build --no-cache --build-arg hf_job_executor_version=$(HF_JOB_EXECUTOR_VERSION) -t $(PREFIX)/$(REPO_NAME):$(TAG) .
	docker tag $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):$(VERSION)-latest

push: image
	docker push $(PREFIX)/$(REPO_NAME):$(TAG)
	docker push $(PREFIX)/$(REPO_NAME):$(VERSION)-latest

clean:
	docker rmi $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):$(VERSION)-latest || true

.PHONY: all image push clean
