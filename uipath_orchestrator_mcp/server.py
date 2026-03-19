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

import logging
import os
from datetime import datetime
from functools import wraps
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from .api import UiPathJobsClient

load_dotenv()

transport = os.getenv("MCP_TRANSPORT", "stdio")

if transport != "stdio":

    mcp = FastMCP(
        "UiPath Orchestrator",
        instructions=(
            "Use these tools to interact with a UiPath Orchestrator instance. "
            "Most operations require a folder_name to scope the request to the correct tenant folder."
        ),
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", 8000)),
    )
else:
    mcp = FastMCP(
        "UiPath Orchestrator",
        instructions=(
            "Use these tools to interact with a UiPath Orchestrator instance. "
            "Most operations require a folder_name to scope the request to the correct tenant folder."
        ),
    )

log_level_name = os.getenv("MCP_LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_name, logging.INFO)
logging.basicConfig(
    level=log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("uipath_orchestrator_mcp")
logger.setLevel(log_level)


def _log_tool_inputs(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        logger.info(
            "Tool %s invoked with args=%s kwargs=%s",
            func.__name__,
            args,
            kwargs,
        )
        return func(*args, **kwargs)

    return wrapper


def logged_tool(*tool_args, **tool_kwargs):
    def decorator(func):
        logged = _log_tool_inputs(func)        # wrap with logging first
        return mcp.tool(*tool_args, **tool_kwargs)(logged)  # then register with MCP

    return decorator

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
# Summary of Orchestrator
@logged_tool()
def get_orchestrator_summary() -> dict:
    """
    Get all folders with their processes and queues in a single call.
    Use this to resolve folder/process names before taking any action.
    Call this once at the start of any request where the folder is unknown.
    """
    client = _get_client()
    folders = client.get_orchestrator_folders()
    summary = []
    for folder in folders:
        folder_name = folder.get("FullyQualifiedName") or folder.get("DisplayName")
        try:
            releases = client.get_releases_by_folder_name(folder_name)
        except Exception:
            releases = []
        try:
            queues = client.get_queue_definitions_by_folder_name(folder_name)
        except Exception:
            queues = []
        summary.append({
            "folder": folder_name,
            "processes": [r.get("Name") for r in releases],
            "queues": [q.get("Name") for q in queues],
        })
        
    summary = [f for f in summary if f["processes"] or f["queues"]]  # filter out empty folders
    summary = [f for f in summary if 'workspace' not in f['folder'].lower()]  # filter out personal workspaces
    
    return {"folders": summary}


# ---------------------------------------------------------------------------
# Folder tools
# ---------------------------------------------------------------------------


@logged_tool()
def list_folders() -> list[dict]:
    """List all folders in UiPath Orchestrator."""
    return _get_client().get_orchestrator_folders()


# ---------------------------------------------------------------------------
# Queue tools
# ---------------------------------------------------------------------------


@logged_tool()
def list_queue_definitions(folder_name: str) -> list[dict]:
    """
    List all queue definitions in a folder.

    Args:
        folder_name: The display name of the folder.
    """
    return _get_client().get_queue_definitions_by_folder_name(folder_name)


@logged_tool()
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


@logged_tool()
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


@logged_tool()
def delete_queue_item(queue_item_id: int, folder_name: str) -> dict:
    """
    Delete a queue item by its ID.

    Args:
        queue_item_id: The ID of the queue item to delete.
        folder_name: The display name of the folder containing the item.
    """
    return _get_client().delete_queue_item(queue_item_id, folder_name)


@logged_tool()
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


_JOB_SUMMARY_FIELDS = {
    "Id",
    "ReleaseName",        # process name — what ran
    "State",              # Faulted, Successful, etc.
    "StartTime",          # when it started
    "EndTime",            # when it finished
    "Info",               # the error message / completion info
    "JobError",           # structured error detail if present
    "HostMachineName",    # which robot ran it
    "Source",             # Manual, Schedule, etc — how it was triggered
    "OrganizationUnitFullyQualifiedName",  # folder — redundant with _folder but useful
    "_folder",            # folder we queried
}


@logged_tool()
def list_jobs(
    folder_name: str,
    batch_size: int = 50,
    release_name: Optional[str] = None,
    skip: int = 0,
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
        state: Filter by job state — 'Faulted', 'Successful', 'Pending', 'Running', 'Stopped', 'Stopping'.
        job_priority: Filter by priority — 'Low', 'Normal', or 'High'.
        start_time: Filter jobs that started at or after this ISO 8601 datetime string.
        end_time: Filter jobs that ended at or before this ISO 8601 datetime string.
        release_name: Filter jobs by release name.

    """
    kwargs: dict = {}
    if state:
        kwargs["state"] = state
    if job_priority:
        kwargs["job_priority"] = job_priority
    if start_time:
        kwargs["start_time"] = datetime.fromisoformat(start_time)
    if end_time:
        kwargs["end_time"] = datetime.fromisoformat(end_time)
    if release_name:
        kwargs["release_name"] = release_name
        
    jobs = _get_client().get_job_batch_by_folder_name(
        folder_name=folder_name,
        batch_size=batch_size,
        skip=skip,
        **kwargs,
    )
    
    #filter down to summary fields + folder for easier consumption by agents, and add folder info to each job
    jobs = [{k: v for k, v in job.items() if k in _JOB_SUMMARY_FIELDS} for job in jobs]
    
    for job in jobs:
        job["_folder"] = folder_name  # add folder info to each job for easier reference
    return jobs
        
    


@logged_tool()
def get_job(job_id: int, folder_name: str) -> dict:
    """
    Get details for a specific job by ID.

    Args:
        job_id: The ID of the job.
        folder_name: The display name of the folder containing the job.
    """
    return _get_client().get_job_by_id(job_id, folder_name)


@logged_tool()
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


@logged_tool()
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


@mcp.tool()
def get_jobs_across_folders(
    state: Optional[str] = None,
    folder_names: Optional[list[str]] = None,
    hours_back: Optional[int] = None,
    days_back: Optional[int] = None,
    release_name: Optional[str] = None,
    batch_size: int = 50,
) -> list[dict]:
    """
    Get jobs across multiple folders in a single call.
    More efficient than calling list_jobs per folder.
    Use this whenever the user asks about jobs across the tenant or multiple folders.

    Args:
        state: Filter by execution state — 'Faulted', 'Successful', 'Pending', 'Running', 'Stopped', 'Stopping'.
        folder_names: Folders to check. If omitted, checks all non-workspace folders.
        hours_back: Return jobs started within the last N hours. Takes priority over days_back.
        days_back: Return jobs started within the last N days.
        release_name: Filter jobs by release name.
        batch_size: Max jobs to return per folder (default 50).
    """
    from datetime import datetime, timedelta

    client = _get_client()

    if not folder_names:
        folders = client.get_orchestrator_folders()
        folder_names = [
            f.get("FullyQualifiedName") or f.get("DisplayName")
            for f in folders
            if "workspace" not in (f.get("FullyQualifiedName") or "").lower()
            and (f.get("FullyQualifiedName") or f.get("DisplayName"))
        ]

    start_time = None
    if hours_back is not None:
        start_time = datetime.utcnow() - timedelta(hours=hours_back)
    elif days_back is not None:
        start_time = datetime.utcnow() - timedelta(days=days_back)

    results = []
    for folder_name in folder_names:
        try:
            kwargs = dict(
                folder_name=folder_name,
                release_name=release_name,
                batch_size=batch_size,
            )
            if state:
                kwargs["state"] = state
            if start_time:
                kwargs["start_time"] = start_time

            jobs = client.get_job_batch_by_folder_name(**kwargs)
            for job in jobs:
                job["_folder"] = folder_name
            results.extend(jobs)
        except Exception:
            continue
        
    results = [{k: v for k, v in job.items() if k in _JOB_SUMMARY_FIELDS} for job in results]  # filter down to summary fields

    return results


# ---------------------------------------------------------------------------
# Release / Process tools
# ---------------------------------------------------------------------------


@logged_tool()
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


@logged_tool()
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


@logged_tool()
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


@logged_tool()
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


@logged_tool()
def list_schedules(folder_name: str) -> list[dict]:
    """
    List all process schedules (triggers) in a folder.

    Args:
        folder_name: The display name of the folder.
    """
    return _get_client().get_process_schedules(folder_name)


@logged_tool()
def enable_schedule(schedule_id: int, folder_name: str) -> dict:
    """
    Enable a process schedule.

    Args:
        schedule_id: The ID of the schedule to enable.
        folder_name: The display name of the folder containing the schedule.
    """
    return _get_client().set_schedule_enabled(schedule_id, folder_name, enabled=True)


@logged_tool()
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


@logged_tool()
def list_robots(folder_name: Optional[str] = None) -> list[dict]:
    """
    List robots, optionally scoped to a specific folder.

    Args:
        folder_name: The display name of the folder (optional). If omitted, returns all robots.
    """
    return _get_client().get_robots(folder_name)


@logged_tool()
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


@logged_tool()
def list_assets(folder_name: str) -> list[dict]:
    """
    List all assets in a folder.

    Args:
        folder_name: The display name of the folder.
    """
    return _get_client().get_assets(folder_name)


@logged_tool()
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

    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
