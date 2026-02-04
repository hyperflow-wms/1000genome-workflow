# Build from repository root:
#   docker build -f mcp-server/Dockerfile -t hyperflowwms/1000genome-mcp .

FROM python:3.11-slim

WORKDIR /app

# Copy and install workflow-composer with MCP support
COPY workflow-composer/ /app/workflow-composer/
RUN pip install --no-cache-dir /app/workflow-composer mcp

# Run MCP server
ENTRYPOINT ["python", "-m", "workflow_composer.mcp_server"]
