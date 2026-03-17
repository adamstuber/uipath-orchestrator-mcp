FROM python:3.12-slim

WORKDIR /app

# Install poetry
RUN pip install --no-cache-dir poetry==2.1.1

# Copy dependency files first for layer caching
COPY pyproject.toml poetry.lock ./

# Install dependencies into the system Python (no venv needed in a container)
RUN poetry config virtualenvs.create false \
    && poetry install --only=main --no-root --no-interaction

# Copy source and install the package
COPY uipath_orchestrator_mcp/ ./uipath_orchestrator_mcp/
RUN poetry install --only=main --no-interaction

# SSE transport — server listens on port 8000
ENV MCP_TRANSPORT=sse
EXPOSE 8000

CMD ["uipath-orchestrator-mcp"]
