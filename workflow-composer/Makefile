# workflow-composer Makefile
#
# Build MCP server Docker image and run tests

REPO_NAME = 1000genome-mcp
PREFIX = hyperflowwms
VERSION = 2.3
TAG = $(VERSION)

.PHONY: all test image push clean

all: test

# Run tests
test:
	pytest tests/ -v

# Build MCP server Docker image
image:
	docker build --no-cache -t $(PREFIX)/$(REPO_NAME):$(TAG) .
	docker tag $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):latest

# Push to Docker Hub
push: image
	docker push $(PREFIX)/$(REPO_NAME):$(TAG)
	docker push $(PREFIX)/$(REPO_NAME):latest

# Clean up images
clean:
	docker rmi $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):latest || true
