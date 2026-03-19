# UiPath Orchestrator MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that exposes UiPath Orchestrator operations as tools, enabling AI assistants like Claude to interact with your UiPath tenant directly.

## Features

**23 tools** spanning the core Orchestrator APIs:

| Category | Tools |
|---|---|
| **Overview** | `get_orchestrator_summary` |
| **Folders** | `list_folders` |
| **Jobs** | `list_jobs`, `get_job`, `start_job`, `stop_job`, `get_jobs_across_folders`, `get_job_logs` |
| **Processes** | `list_releases` |
| **Queues** | `list_queue_definitions`, `get_queue_items`, `count_queue_items`, `add_queue_item`, `bulk_add_queue_items`, `delete_queue_item`, `retry_queue_item` |
| **Schedules** | `list_schedules`, `enable_schedule`, `disable_schedule` |
| **Robots & Machines** | `list_robots`, `list_machines` |
| **Assets** | `list_assets`, `get_asset` |

## Prerequisites

- Python 3.12+
- A UiPath Cloud account with an external application (OAuth client credentials)
- The following OAuth scopes: `OR.Jobs OR.Folders OR.Queues OR.Robots OR.Execution OR.Machines OR.Assets`

### Creating a UiPath External Application

1. In UiPath Automation Cloud, go to **Admin > External Applications**
2. Create a new application with **Application Scopes** (not user scopes)
3. Grant the scopes listed above
4. Copy the **Client ID** and **Client Secret**

## Installation

### Option 1: pip (stdio transport — recommended for local use)

```bash
pip install uipath-orchestrator-mcp
```

### Option 2: Run from source

```bash
git clone https://github.com/adamstuber/uipath-orchestrator-mcp.git
cd uipath-orchestrator-mcp
pip install .
```

### Option 3: Docker (SSE transport — for remote/shared use)

```bash
docker build -t uipath-orchestrator-mcp .
docker run -p 8000:8000 \
  -e UIPATH_CLIENT_ID=your_client_id \
  -e UIPATH_CLIENT_SECRET=your_client_secret \
  -e UIPATH_TENANT_NAME=your_tenant \
  -e UIPATH_ORG_NAME=your_org \
  uipath-orchestrator-mcp
```

## Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `UIPATH_CLIENT_ID` | Yes | OAuth client ID from your external application |
| `UIPATH_CLIENT_SECRET` | Yes | OAuth client secret |
| `UIPATH_TENANT_NAME` | Yes | Your UiPath tenant name (e.g. `DefaultTenant`) |
| `UIPATH_ORG_NAME` | Yes | Your UiPath organization name (from the cloud URL) |
| `UIPATH_SCOPES` | No | Space-separated OAuth scopes (default: all required scopes) |
| `MCP_TRANSPORT` | No | `stdio` (default), `streamable-http` (recommended for HTTP), or `sse` (legacy HTTP) |
| `MCP_HOST` | No | Host to bind when using an HTTP transport (default: `0.0.0.0`) |
| `MCP_PORT` | No | Port to bind when using an HTTP transport (default: `8000`) |
| `MCP_LOG_LEVEL` | No | Logging level: `DEBUG`, `INFO`, `WARN`, `ERROR` (default: `INFO`) |

## Usage

### Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "uipath-orchestrator": {
      "command": "uipath-orchestrator-mcp",
      "env": {
        "UIPATH_CLIENT_ID": "your_client_id",
        "UIPATH_CLIENT_SECRET": "your_client_secret",
        "UIPATH_TENANT_NAME": "your_tenant",
        "UIPATH_ORG_NAME": "your_org"
      }
    }
  }
}
```

Alternatively, if you use a `.env` file:

```json
{
  "mcpServers": {
    "uipath-orchestrator": {
      "command": "uipath-orchestrator-mcp"
    }
  }
}
```

### Claude Code (CLI)

```bash
claude mcp add uipath-orchestrator uipath-orchestrator-mcp
```

### HTTP transports (Docker or remote server)

Two HTTP-based transports are supported for remote or containerised deployments:

| Transport | `MCP_TRANSPORT` value | Endpoint | Notes |
|---|---|---|---|
| Streamable HTTP | `streamable-http` | `http://host:8000/mcp` | Recommended — newer MCP spec standard |
| SSE | `sse` | `http://host:8000/sse` | Legacy, kept for older client compatibility |

Example with streamable HTTP:

```bash
docker run -p 8000:8000 \
  -e MCP_TRANSPORT=streamable-http \
  -e UIPATH_CLIENT_ID=... \
  -e UIPATH_CLIENT_SECRET=... \
  -e UIPATH_TENANT_NAME=... \
  -e UIPATH_ORG_NAME=... \
  uipath-orchestrator-mcp
```

Then connect your MCP client to `http://your-host:8000/mcp`.

## Example prompts

Once connected, you can ask Claude things like:

- *"Show me all faulted jobs from the last 24 hours across all folders."*
- *"Start the InvoiceProcessing process in the Finance folder."*
- *"How many Failed items are in the BillingQueue queue?"*
- *"Retry all failed queue items in the Accounts folder."*
- *"Disable the nightly schedule for the ReportGenerator process."*
- *"What robots are available in the Production folder?"*

## Development

```bash
git clone https://github.com/adamstuber/uipath-orchestrator-mcp.git
cd uipath-orchestrator-mcp
pip install poetry
poetry install
cp .env.example .env  # fill in your credentials
```

Run the server locally (stdio):

```bash
poetry run uipath-orchestrator-mcp
```

### Project structure

```
uipath_orchestrator_mcp/
  __init__.py     # package init
  api.py          # UiPath Orchestrator API client (authentication, OData, retries)
  server.py       # MCP tool definitions (FastMCP)
Dockerfile        # SSE transport container
pyproject.toml
```

## Contributing

Contributions are welcome. Please open an issue before submitting a pull request for significant changes.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes
4. Open a pull request

## License

MIT — see [LICENSE](LICENSE).
