REPO_NAME = 1000genome-generator
PREFIX = hyperflowwms
VERSION = 1.0
TAG = $(VERSION)

all: push

container: image

image:
	docker build --no-cache -t $(PREFIX)/$(REPO_NAME):$(TAG) .
	docker tag $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):latest

push: image
	docker push $(PREFIX)/$(REPO_NAME):$(TAG)
	docker push $(PREFIX)/$(REPO_NAME):latest

clean:
	docker rmi $(PREFIX)/$(REPO_NAME):$(TAG) $(PREFIX)/$(REPO_NAME):latest || true

.PHONY: all container image push clean
