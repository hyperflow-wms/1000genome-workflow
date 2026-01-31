REPO_NAME = 1000genome-data
PREFIX = hyperflowwms
VERSION = 1.0
TAG = $(VERSION)

all: push

image:
	docker build -t $(PREFIX)/$(REPO_NAME):$(TAG) .
	docker tag $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):latest

push: image
	docker push $(PREFIX)/$(REPO_NAME):$(TAG)
	docker push $(PREFIX)/$(REPO_NAME):latest

# Run data preparation to a local directory
prepare:
	docker run --rm -v $(PWD)/../data:/mnt/data $(PREFIX)/$(REPO_NAME):$(TAG) sh /prepare_data.sh

clean:
	docker rmi $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):latest || true

.PHONY: all image push prepare clean
