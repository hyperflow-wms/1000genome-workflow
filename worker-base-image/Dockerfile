FROM node:20-alpine

# Install Python and scientific libraries
RUN apk add --no-cache python3 py3-pip py3-numpy py3-matplotlib

# Copy analysis scripts
WORKDIR /1000genome
COPY scripts/ /1000genome/scripts/

# Set environment
ENV PATH="/1000genome/scripts:${PATH}"
ENV PYTHONPATH="/1000genome/scripts"

WORKDIR /
