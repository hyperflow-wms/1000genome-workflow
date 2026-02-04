REPO_NAME = 1000genome-mcp
PREFIX = hyperflowwms
VERSION = 2.0
TAG = $(VERSION)

# Build context is repository root (to include workflow-composer)
ROOT_DIR = $(shell cd .. && pwd)

all: push

image:
	cd $(ROOT_DIR) && docker build --no-cache -f mcp-server/Dockerfile -t $(PREFIX)/$(REPO_NAME):$(TAG) .
	docker tag $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):latest

push: image
	docker push $(PREFIX)/$(REPO_NAME):$(TAG)
	docker push $(PREFIX)/$(REPO_NAME):latest

clean:
	docker rmi $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):latest || true

.PHONY: all image push clean
