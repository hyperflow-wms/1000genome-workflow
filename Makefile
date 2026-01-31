# 1000genome-workflow - Top-level build orchestration

.PHONY: all build-all push-all build-worker-base build-worker build-generator build-mcp build-data clean test

all: build-all

# Build all images in dependency order
build-all: build-worker-base build-worker build-generator build-mcp build-data

# Push all images
push-all:
	$(MAKE) -C worker-base-image push
	$(MAKE) -C worker-image push
	$(MAKE) -C workflow-generator push
	$(MAKE) -C mcp-server push
	$(MAKE) -C data-container push

# Individual image builds
build-worker-base:
	$(MAKE) -C worker-base-image image

build-worker: build-worker-base
	$(MAKE) -C worker-image image

build-generator:
	$(MAKE) -C workflow-generator image

build-mcp: build-generator
	$(MAKE) -C mcp-server image

build-data:
	$(MAKE) -C data-container image

# Clean all images
clean:
	$(MAKE) -C worker-base-image clean || true
	$(MAKE) -C worker-image clean || true
	$(MAKE) -C workflow-generator clean || true
	$(MAKE) -C mcp-server clean || true
	$(MAKE) -C data-container clean || true

# Generate workflow
generate:
	$(MAKE) -C workflow-generator image
	docker run --rm -v $(PWD)/data:/output hyperflowwms/1000genome-generator:1.0 \
		sh -c "cd /1000genome-workflow && ./generate_workflow.sh && cp workflow.json /output/"

# Prepare input data (decompress to data/ directory)
prepare-data: build-data
	$(MAKE) -C data-container prepare

# Run tests
test:
	bash tests/run_all.sh
