REPO_NAME = 1000genome-worker-base
PREFIX = hyperflowwms
VERSION = 1.7
TAG = $(VERSION)

all: push

image:
	docker build --no-cache -t $(PREFIX)/$(REPO_NAME):$(TAG) .
	docker tag $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):latest

push: image push-only

# Pushes the tags already present locally. CI builds the base first so that
# worker-image can build FROM it, and publishes that same build; the 'image'
# prerequisite would rebuild with --no-cache and publish a different one.
push-only:
	docker push $(PREFIX)/$(REPO_NAME):$(TAG)
	docker push $(PREFIX)/$(REPO_NAME):latest

clean:
	docker rmi $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):latest || true

.PHONY: all image push push-only clean
