FROM hyperflowwms/1000genome-generator:1.0

# Install MCP SDK
RUN pip3 install --break-system-packages mcp

# Copy MCP server
COPY server.py /1000genome-workflow/

WORKDIR /1000genome-workflow

# Run MCP server
ENTRYPOINT ["python3", "server.py"]
