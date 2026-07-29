REPO_NAME = 1000genome-worker-base
PREFIX = hyperflowwms
VERSION = 1.2
TAG = $(VERSION)

all: push

image:
	docker build --no-cache -t $(PREFIX)/$(REPO_NAME):$(TAG) .
	docker tag $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):latest

push: image
	docker push $(PREFIX)/$(REPO_NAME):$(TAG)
	docker push $(PREFIX)/$(REPO_NAME):latest

clean:
	docker rmi $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):latest || true

.PHONY: all image push clean
