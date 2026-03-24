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
from datetime import datetime, timezone
from functools import wraps
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from .api import UiPathJobsClient

load_dotenv()

transport = os.getenv("MCP_TRANSPORT", "stdio")

_INSTRUCTIONS = (
    "Use these tools to interact with a UiPath Orchestrator instance — an enterprise RPA platform "
    "that runs software robots (automations) called processes or releases.\n\n"
    "Key concepts:\n"
    "  - Folder: a tenant-level namespace that groups processes, queues, jobs, robots, and assets.\n"
    "    Most tools require a folder_name. Use get_orchestrator_summary to discover valid folder names.\n"
    "  - Release / Process: a deployed version of a UiPath automation. list_releases returns these.\n"
    "  - Job: a single execution run of a release on a robot. Jobs have states: Pending, Running,\n"
    "    Successful, Faulted, Stopped, Stopping.\n"
    "  - Queue / Queue Item: a work-item queue. Items have statuses: New, InProgress, Successful,\n"
    "    Failed, Abandoned, Retried, Deleted.\n\n"
    "Typical workflows:\n"
    "  1. Discovery: call get_orchestrator_summary once to learn folder names, process names, and queues.\n"
    "  2. Job monitoring: use get_jobs_across_folders for tenant-wide queries; use list_jobs for a single folder.\n"
    "  3. Failure diagnosis: call diagnose_job first — it combines job metadata with error logs in one call.\n"
    "     Only use get_job_logs if you need full paginated logs or a specific time range.\n"
    "  4. Starting a job: provide process_name (human-readable) and folder_name — the release key is "
    "looked up automatically.\n"
    "  5. Queue operations: use count_queue_items before get_queue_items to avoid over-fetching large queues.\n"
    "     Use bulk_add_queue_items when adding more than one item."
)

if transport != "stdio":
    mcp = FastMCP(
        "UiPath Orchestrator",
        instructions=_INSTRUCTIONS,
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", 8000)),
    )
else:
    mcp = FastMCP(
        "UiPath Orchestrator",
        instructions=_INSTRUCTIONS,
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
    Return all non-workspace folders with their deployed processes and queue names.

    This is the primary discovery tool. Call it once at the start of any session
    or whenever the folder name, process name, or queue name is unknown. The names
    returned here are the exact values expected by folder_name, process_name, and
    queue_name parameters in all other tools.

    Returns a list of objects: {folder, processes: [...], queues: [...]}.
    Personal workspace folders are excluded automatically.
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
    """
    List all folders in UiPath Orchestrator (raw API response).

    Prefer get_orchestrator_summary over this tool — it also returns processes and
    queues per folder and filters out personal workspaces. Use list_folders only when
    you need the raw folder metadata (IDs, types, parent hierarchy).
    """
    return _get_client().get_orchestrator_folders()


# ---------------------------------------------------------------------------
# Queue tools
# ---------------------------------------------------------------------------


@logged_tool()
def list_queue_definitions(folder_name: str) -> list[dict]:
    """
    List all queue definitions (metadata) in a folder.

    Returns queue names, IDs, and configuration — but not the queue items themselves.
    To retrieve actual work items, use get_queue_items. To count items matching a
    filter before fetching, use count_queue_items.

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
    most_recent_first: bool = False,
) -> list[dict]:
    """
    Retrieve a batch of queue items from a specific queue.

    Queue item statuses: New, InProgress, Successful, Failed, Abandoned, Retried, Deleted.

    Before calling this on a large queue, use count_queue_items to check volume.
    Use most_recent_first=True with a small batch_size to efficiently get the latest items.

    OData filter examples:
      - "Status eq 'Failed'"
      - "Status eq 'New' and Priority eq 'High'"
      - "Reference eq 'ORDER-123'"

    Args:
        folder_name: The display name of the folder.
        queue_name: The name of the queue.
        batch_size: Number of items to return (default 100).
        skip: Number of items to skip for pagination (default 0).
        filter_query: Optional OData filter string.
        most_recent_first: If True, return newest items first.
    """
    return _get_client().get_queue_item_batch(
        folder_name=folder_name,
        queue_name=queue_name,
        batch_size=batch_size,
        skip=skip,
        filter_query=filter_query,
        order_desc=most_recent_first,
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
    Retry a Failed or Abandoned queue item, resetting it to New so robots will pick it up again.

    Only applicable to items with status Failed or Abandoned — retrying items in other
    states will return an API error. The row_version field is returned with each queue item
    from get_queue_items and is required for optimistic concurrency control.

    Args:
        queue_item_id: The ID of the queue item to retry.
        row_version: The RowVersion value from the queue item (prevents stale retries).
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
    source: Optional[str] = None,
    most_recent_first: bool = False,
) -> list[dict]:
    """
    List and filter jobs within a single folder.

    Use this when the user asks about jobs in a specific, known folder.
    Use get_jobs_across_folders instead when querying multiple folders or the entire tenant.

    Returns a trimmed set of fields per job: Id, ReleaseName, State, StartTime, EndTime,
    Info, JobError, HostMachineName, Source, and _folder.

    Args:
        folder_name: The display name of the folder.
        batch_size: Number of jobs to return (default 50).
        skip: Number of jobs to skip for pagination (default 0).
        state: Filter by job state — 'Faulted', 'Successful', 'Pending', 'Running', 'Stopped', 'Stopping'.
        job_priority: Filter by priority — 'Low', 'Normal', or 'High'.
        start_time: Filter jobs that started at or after this ISO 8601 datetime string.
        end_time: Filter jobs that ended at or before this ISO 8601 datetime string.
        release_name: Filter jobs by release (process) name.
        source: Filter by how the job was triggered — 'Manual', 'Schedule', 'Agent', 'Queue'.
        most_recent_first: If True, return most recently started jobs first.
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
    if source:
        kwargs["source"] = source

    jobs = _get_client().get_job_batch_by_folder_name(
        folder_name=folder_name,
        batch_size=batch_size,
        skip=skip,
        order_desc=most_recent_first,
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
    Get the full details for a specific job by ID (raw API response).

    Returns all job fields. If you need to understand why a job failed, use
    diagnose_job instead — it also fetches error logs and returns a structured summary.

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
    Start a new execution of a process (release) in a folder.

    Provide process_name (as returned by get_orchestrator_summary) and folder_name —
    the release key is looked up automatically. Only provide release_key if you already
    have the exact GUID and want to skip that lookup.

    Strategy options:
      - 'All' (default): run on all connected robots in the folder.
      - 'Specific': run only on the robots listed in robot_ids.
      - 'JobsCount': start exactly jobs_count parallel job instances.

    Args:
        folder_name: The display name of the folder containing the process.
        process_name: The process name as returned by get_orchestrator_summary.
        release_key: GUID of the release. Omit to auto-resolve from process_name.
        strategy: Robot allocation strategy — 'All', 'Specific', or 'JobsCount'.
        robot_ids: List of robot IDs to target (required when strategy is 'Specific').
        jobs_count: Number of job instances to start (required when strategy is 'JobsCount').
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
    source: Optional[str] = None,
    most_recent_first: bool = False,
    batch_size: int = 50,
) -> list[dict]:
    """
    Query jobs across multiple folders in a single call — the preferred tool for
    tenant-wide or multi-folder job queries.

    Use list_jobs instead only when you already know the single folder and need
    advanced OData filtering or job_priority filtering.

    Returns a trimmed set of fields per job (same as list_jobs). Results from all
    folders are merged and optionally sorted by StartTime.

    Args:
        state: Filter by execution state — 'Faulted', 'Successful', 'Pending', 'Running', 'Stopped', 'Stopping'.
        folder_names: Folders to check. If omitted, checks all non-workspace folders.
        hours_back: Return jobs started within the last N hours. Takes priority over days_back.
        days_back: Return jobs started within the last N days.
        release_name: Filter jobs by release name.
        source: Filter by how the job was triggered — 'Manual', 'Schedule', 'Agent', 'Queue'.
        most_recent_first: If True, return most recently started jobs first per folder, then sort combined results. Use with batch_size to efficiently find the last N jobs across folders.
        batch_size: Max jobs to return per folder (default 50).
    """
    from datetime import timedelta

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
        start_time = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    elif days_back is not None:
        start_time = datetime.now(timezone.utc) - timedelta(days=days_back)

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
            if source:
                kwargs["source"] = source
            if start_time:
                kwargs["start_time"] = start_time

            jobs = client.get_job_batch_by_folder_name(order_desc=most_recent_first, **kwargs)
            for job in jobs:
                job["_folder"] = folder_name
            results.extend(jobs)
        except Exception:
            continue

    results = [{k: v for k, v in job.items() if k in _JOB_SUMMARY_FIELDS} for job in results]

    if most_recent_first:
        results.sort(key=lambda j: j.get("StartTime") or "", reverse=True)

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
    log_levels: Optional[list[str]] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> list[dict]:
    """
    Retrieve paginated robot execution logs for a specific job.

    Use diagnose_job instead when the goal is to understand a failure — it combines
    job metadata with error logs and recent context in a single focused response.
    Use get_job_logs when you need full log pagination, specific time ranges, or
    log levels that diagnose_job doesn't surface.

    Log levels (lowest to highest): Trace, Info, Warn, Error, Fatal.

    Args:
        job_id: The ID of the job.
        folder_name: The display name of the folder containing the job.
        batch_size: Number of log entries to return (default 100).
        skip: Number of entries to skip for pagination.
        min_level: Minimum log level to return — 'Trace', 'Info', 'Warn', 'Error', or 'Fatal'.
                   Ignored if log_levels is provided.
        log_levels: Exact levels to include, e.g. ['Error', 'Fatal']. Takes priority over min_level.
        start_time: Return logs at or after this ISO 8601 datetime string.
        end_time: Return logs at or before this ISO 8601 datetime string.
    """
    kwargs: dict = {}
    if log_levels:
        kwargs["log_levels"] = log_levels
    if start_time:
        kwargs["start_time"] = datetime.fromisoformat(start_time)
    if end_time:
        kwargs["end_time"] = datetime.fromisoformat(end_time)
    return _get_client().get_job_logs(job_id, folder_name, batch_size, skip, min_level, **kwargs)


@logged_tool()
def diagnose_job(
    job_id: int,
    folder_name: str,
    tail: int = 15,
) -> dict:
    """
    Return a focused diagnostic report for a job — the first tool to call when
    asked 'what went wrong?' or 'why did this job fail?'.

    Combines job metadata and error logs in one call, avoiding the need to chain
    get_job + get_job_logs. Only use get_job_logs afterward if you need more log
    history than `tail` provides or need to paginate through all entries.

    Returns:
      - job: metadata (state, process, start/end time, duration, robot, error fields)
      - error_logs: all Error and Fatal log entries for the job
      - combined_log_view: deduplicated, time-sorted merge of error logs + last `tail` entries

    Args:
        job_id: The ID of the job to diagnose.
        folder_name: The display name of the folder containing the job.
        tail: Number of most-recent log entries to include for context (default 15).
    """
    client = _get_client()

    job = client.get_job_by_id(job_id, folder_name)

    _LOG_FIELDS = {"TimeStamp", "Level", "Message", "RobotName", "HostMachineName"}

    def _trim(log: dict) -> dict:
        return {k: v for k, v in log.items() if k in _LOG_FIELDS}

    error_logs = client.get_job_logs(
        job_id, folder_name, batch_size=200, log_levels=["Error", "Fatal"]
    )

    tail_logs = client.get_job_logs(
        job_id, folder_name, batch_size=tail, order_desc=True
    )
    tail_logs = list(reversed(tail_logs))  # restore chronological order

    # Merge error logs + tail logs, deduplicate by TimeStamp+Level+Message
    seen = set()
    combined = []
    for entry in error_logs + tail_logs:
        key = (entry.get("TimeStamp"), entry.get("Level"), entry.get("Message"))
        if key not in seen:
            seen.add(key)
            combined.append(_trim(entry))
    combined.sort(key=lambda e: e.get("TimeStamp") or "")

    start = job.get("StartTime")
    end = job.get("EndTime")
    duration_seconds = None
    if start and end:
        from dateutil import parser as dtparser
        try:
            duration_seconds = round(
                (dtparser.parse(end) - dtparser.parse(start)).total_seconds()
            )
        except Exception:
            pass

    return {
        "job": {
            "Id": job.get("Id"),
            "State": job.get("State"),
            "ReleaseName": job.get("ReleaseName"),
            "StartTime": start,
            "EndTime": end,
            "DurationSeconds": duration_seconds,
            "HostMachineName": job.get("HostMachineName"),
            "Source": job.get("Source"),
            "Info": job.get("Info"),
            "JobError": job.get("JobError"),
        },
        "error_logs": [_trim(e) for e in error_logs],
        "combined_log_view": combined,
    }


@logged_tool()
def list_releases(folder_name: str) -> list[dict]:
    """
    List all releases (deployed automations) in a folder with full metadata.

    In UiPath, a "release" is a specific version of a process package deployed to a folder.
    get_orchestrator_summary returns just the process names; use list_releases when you need
    release keys (GUIDs), package versions, entry points, or other deployment metadata.

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
    Return the total number of queue items matching the given criteria — without fetching the items.

    Call this before get_queue_items to check volume and decide on batch_size / pagination
    strategy. Also useful for health checks (e.g. how many New items are waiting?).

    Args:
        folder_name: The display name of the folder.
        queue_name: Queue name to scope the count. If omitted, counts across all queues in the folder.
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
    List all process schedules (time-based triggers) in a folder.

    Returns schedule metadata including cron expression, enabled/disabled state, and the
    associated release name. Use enable_schedule / disable_schedule to toggle them.

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
