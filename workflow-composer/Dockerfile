# MCP Server Docker image for workflow-composer
#
# Build from workflow-composer directory:
#   docker build -t hyperflowwms/1000genome-mcp:2.0 .
#
# Or from repository root:
#   docker build -f workflow-composer/Dockerfile -t hyperflowwms/1000genome-mcp:2.0 workflow-composer/

FROM python:3.11-slim

WORKDIR /app

# Copy and install workflow-composer with MCP support
COPY . /app/workflow-composer/
RUN pip install --no-cache-dir /app/workflow-composer[mcp]

# Run MCP server
ENTRYPOINT ["python", "-m", "workflow_composer.mcp_server"]
