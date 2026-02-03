FROM hyperflowwms/1000genome-generator:1.0

# Install MCP SDK
RUN pip3 install --break-system-packages mcp

# Copy MCP server and skills
COPY server.py /1000genome-workflow/
COPY skills/ /1000genome-workflow/skills/

WORKDIR /1000genome-workflow

# Run MCP server
ENTRYPOINT ["python3", "server.py"]
