FROM node:22-alpine
RUN apk add --no-cache python3 py3-pip && \
    apk add py3-future && \
    npm install -g https://github.com/hyperflow-wms/pegasus-hyperflow-converter/archive/master.tar.gz 

WORKDIR /1000genome-workflow
COPY . .

ENV PATH=/1000genome-workflow:$PATH
ENV PYTHONPATH=/1000genome-workflow
