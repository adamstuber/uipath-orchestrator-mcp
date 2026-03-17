"""
UiPath Orchestrator MCP Server

Exposes UiPath Orchestrator operations as MCP tools so that AI agents can
interact with folders, queues, jobs, releases, robots, machines, and assets.

Required environment variables:
    UIPATH_CLIENT_ID
    UIPATH_CLIENT_SECRET
    UIPATH_TENANT_NAME
    UIPATH_ORG_NAME
"""

import os
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from .api import UiPathJobsClient

load_dotenv()

mcp = FastMCP(
    "UiPath Orchestrator",
    instructions=(
        "Use these tools to interact with a UiPath Orchestrator instance. "
        "Most operations require a folder_name to scope the request to the correct tenant folder."
    ),
)

_client: Optional[UiPathJobsClient] = None


def _get_client() -> UiPathJobsClient:
    global _client
    if _client is None:
        _client = UiPathJobsClient(
            client_id=os.getenv("UIPATH_CLIENT_ID"),
            client_secret=os.getenv("UIPATH_CLIENT_SECRET"),
            tenant_name=os.getenv("UIPATH_TENANT_NAME"),
            org_name=os.getenv("UIPATH_ORG_NAME"),
        )
    return _client


# ---------------------------------------------------------------------------
# Folder tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_folders() -> list[dict]:
    """List all folders in UiPath Orchestrator."""
    return _get_client().get_orchestrator_folders()


# ---------------------------------------------------------------------------
# Queue tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_queue_definitions(folder_name: str) -> list[dict]:
    """
    List all queue definitions in a folder.

    Args:
        folder_name: The display name of the folder.
    """
    return _get_client().get_queue_definitions_by_folder_name(folder_name)


@mcp.tool()
def get_queue_items(
    folder_name: str,
    queue_name: str,
    batch_size: int = 100,
    skip: int = 0,
    filter_query: Optional[str] = None,
) -> list[dict]:
    """
    Retrieve a batch of queue items.

    Args:
        folder_name: The display name of the folder.
        queue_name: The name of the queue.
        batch_size: Number of items to return (default 100).
        skip: Number of items to skip for pagination (default 0).
        filter_query: Optional OData filter string (e.g. "Status eq 'Failed'").
    """
    return _get_client().get_queue_item_batch(
        folder_name=folder_name,
        queue_name=queue_name,
        batch_size=batch_size,
        skip=skip,
        filter_query=filter_query,
    )


@mcp.tool()
def add_queue_item(
    folder_name: str,
    queue_name: str,
    specific_content: dict,
    priority: str = "Normal",
    reference: Optional[str] = None,
) -> dict:
    """
    Add a new item to a queue.

    Args:
        folder_name: The display name of the folder.
        queue_name: The name of the queue.
        specific_content: Key-value pairs of data to store with the queue item.
        priority: Item priority — 'Low', 'Normal', or 'High' (default 'Normal').
        reference: Optional reference string for tracking purposes.
    """
    return _get_client().add_queue_item(
        folder_name=folder_name,
        queue_name=queue_name,
        specific_content=specific_content,
        priority=priority,
        reference=reference,
    )


@mcp.tool()
def delete_queue_item(queue_item_id: int, folder_name: str) -> dict:
    """
    Delete a queue item by its ID.

    Args:
        queue_item_id: The ID of the queue item to delete.
        folder_name: The display name of the folder containing the item.
    """
    return _get_client().delete_queue_item(queue_item_id, folder_name)


@mcp.tool()
def retry_queue_item(
    queue_item_id: int, row_version: str, folder_name: str
) -> dict:
    """
    Retry a failed queue item.

    Args:
        queue_item_id: The ID of the queue item to retry.
        row_version: The RowVersion value of the queue item (required for optimistic concurrency).
        folder_name: The display name of the folder containing the item.
    """
    return _get_client().retry_queue_item(queue_item_id, row_version, folder_name)


# ---------------------------------------------------------------------------
# Job tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_jobs(
    folder_name: str,
    batch_size: int = 50,
    skip: int = 0,
    status: Optional[str] = None,
    state: Optional[str] = None,
    job_priority: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> list[dict]:
    """
    List jobs in a folder with optional filtering.

    Args:
        folder_name: The display name of the folder.
        batch_size: Number of jobs to return (default 50).
        skip: Number of jobs to skip for pagination (default 0).
        status: Filter by job result status, e.g. 'Successful', 'Faulted', 'Stopped'.
        state: Filter by job execution state, e.g. 'Running', 'Pending', 'Suspended'.
        job_priority: Filter by priority — 'Low', 'Normal', or 'High'.
        start_time: Filter jobs that started at or after this ISO 8601 datetime string.
        end_time: Filter jobs that ended at or before this ISO 8601 datetime string.
    """
    kwargs: dict = {}
    if status:
        kwargs["status"] = status
    if state:
        kwargs["state"] = state
    if job_priority:
        kwargs["job_priority"] = job_priority
    if start_time:
        kwargs["start_time"] = datetime.fromisoformat(start_time)
    if end_time:
        kwargs["end_time"] = datetime.fromisoformat(end_time)

    return _get_client().get_job_batch_by_folder_name(
        folder_name=folder_name,
        batch_size=batch_size,
        skip=skip,
        **kwargs,
    )


@mcp.tool()
def get_job(job_id: int, folder_name: str) -> dict:
    """
    Get details for a specific job by ID.

    Args:
        job_id: The ID of the job.
        folder_name: The display name of the folder containing the job.
    """
    return _get_client().get_job_by_id(job_id, folder_name)


@mcp.tool()
def start_job(
    folder_name: str,
    process_name: Optional[str] = None,
    release_key: Optional[str] = None,
    strategy: str = "All",
    robot_ids: Optional[list[int]] = None,
    jobs_count: int = 1,
    input_arguments: Optional[dict] = None,
) -> list[dict]:
    """
    Start a UiPath job for a process.

    Args:
        folder_name: The display name of the folder containing the process.
        process_name: The name of the process to run. Used to look up the release key automatically.
        release_key: The release key GUID (overrides process_name lookup).
        strategy: Robot allocation strategy:
            - 'All' — run on all available robots (default).
            - 'Specific' — run on robots listed in robot_ids.
            - 'JobsCount' — start exactly jobs_count jobs.
        robot_ids: List of robot IDs (only used when strategy is 'Specific').
        jobs_count: Number of jobs to start (only used when strategy is 'JobsCount').
        input_arguments: Dictionary of input argument name→value pairs to pass to the process.
    """
    return _get_client().start_job(
        folder_name=folder_name,
        process_name=process_name,
        release_key=release_key,
        strategy=strategy,
        robot_ids=robot_ids,
        jobs_count=jobs_count,
        input_arguments=input_arguments,
    )


@mcp.tool()
def stop_job(
    job_id: int,
    folder_name: str,
    strategy: str = "SoftStop",
) -> dict:
    """
    Stop a running job.

    Args:
        job_id: The ID of the job to stop.
        folder_name: The display name of the folder containing the job.
        strategy: Stop strategy — 'SoftStop' (graceful, waits for current transaction) or 'Kill' (immediate).
    """
    return _get_client().stop_job(job_id, folder_name, strategy)


# ---------------------------------------------------------------------------
# Release / Process tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_job_logs(
    job_id: int,
    folder_name: str,
    batch_size: int = 100,
    skip: int = 0,
    min_level: Optional[str] = None,
) -> list[dict]:
    """
    Retrieve robot execution logs for a job.

    Args:
        job_id: The ID of the job.
        folder_name: The display name of the folder containing the job.
        batch_size: Number of log entries to return (default 100).
        skip: Number of entries to skip for pagination.
        min_level: Minimum log level to return — 'Trace', 'Info', 'Warn', 'Error', or 'Fatal'.
    """
    return _get_client().get_job_logs(job_id, folder_name, batch_size, skip, min_level)


@mcp.tool()
def list_releases(folder_name: str) -> list[dict]:
    """
    List all releases (deployed processes) in a folder.

    Args:
        folder_name: The display name of the folder.
    """
    return _get_client().get_releases_by_folder_name(folder_name)


# ---------------------------------------------------------------------------
# Queue bulk operations
# ---------------------------------------------------------------------------


@mcp.tool()
def count_queue_items(
    folder_name: str,
    queue_name: Optional[str] = None,
    filter_query: Optional[str] = None,
) -> int:
    """
    Return the total number of queue items matching the given criteria.

    Args:
        folder_name: The display name of the folder.
        queue_name: Optional queue name to scope the count.
        filter_query: Optional OData filter string (e.g. "Status eq 'Failed'").
    """
    return _get_client().count_queue_items(folder_name, queue_name, filter_query)


@mcp.tool()
def bulk_add_queue_items(
    folder_name: str,
    queue_name: str,
    items: list[dict],
    commit_type: str = "AllOrNothing",
) -> dict:
    """
    Add multiple items to a queue in a single API call.

    Args:
        folder_name: The display name of the folder.
        queue_name: The name of the queue.
        items: List of item objects. Each item may contain:
            - specific_content (dict, required) — the data payload
            - priority (str, optional) — 'Low', 'Normal', or 'High'
            - reference (str, optional) — tracking reference
        commit_type: 'AllOrNothing' (default) — rolls back everything if any item fails;
                     'ProcessAllIndependently' — commits each item individually.
    """
    return _get_client().bulk_add_queue_items(
        folder_name=folder_name,
        queue_name=queue_name,
        items=items,
        commit_type=commit_type,
    )


# ---------------------------------------------------------------------------
# Schedule / Trigger tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_schedules(folder_name: str) -> list[dict]:
    """
    List all process schedules (triggers) in a folder.

    Args:
        folder_name: The display name of the folder.
    """
    return _get_client().get_process_schedules(folder_name)


@mcp.tool()
def enable_schedule(schedule_id: int, folder_name: str) -> dict:
    """
    Enable a process schedule.

    Args:
        schedule_id: The ID of the schedule to enable.
        folder_name: The display name of the folder containing the schedule.
    """
    return _get_client().set_schedule_enabled(schedule_id, folder_name, enabled=True)


@mcp.tool()
def disable_schedule(schedule_id: int, folder_name: str) -> dict:
    """
    Disable a process schedule.

    Args:
        schedule_id: The ID of the schedule to disable.
        folder_name: The display name of the folder containing the schedule.
    """
    return _get_client().set_schedule_enabled(schedule_id, folder_name, enabled=False)


# ---------------------------------------------------------------------------
# Robot & Machine tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_robots(folder_name: Optional[str] = None) -> list[dict]:
    """
    List robots, optionally scoped to a specific folder.

    Args:
        folder_name: The display name of the folder (optional). If omitted, returns all robots.
    """
    return _get_client().get_robots(folder_name)


@mcp.tool()
def list_machines(folder_name: Optional[str] = None) -> list[dict]:
    """
    List machines, optionally scoped to a specific folder.

    Args:
        folder_name: The display name of the folder (optional). If omitted, returns all machines.
    """
    return _get_client().get_machines(folder_name)


# ---------------------------------------------------------------------------
# Asset tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_assets(folder_name: str) -> list[dict]:
    """
    List all assets in a folder.

    Args:
        folder_name: The display name of the folder.
    """
    return _get_client().get_assets(folder_name)


@mcp.tool()
def get_asset(folder_name: str, asset_name: str) -> list[dict]:
    """
    Get a specific asset by name from a folder.

    Args:
        folder_name: The display name of the folder.
        asset_name: The name of the asset to retrieve.
    """
    return _get_client().get_asset_by_name(folder_name, asset_name)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    # stdio  — MCP client spawns this process directly (default, local use)
    # sse    — server listens on a port, clients connect over HTTP (Docker / remote)
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
