import json
import concurrent.futures
import time
import os
from datetime import datetime
from math import ceil

import requests
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

def format_date(date: datetime) -> str:
    """Format a datetime object to ISO 8601 OData format."""
    if not isinstance(date, datetime):
        raise ValueError("The provided date must be a datetime object.")
    return date.strftime("%Y-%m-%dT%H:%M:%SZ")


CLIENT_ID = os.getenv("UIPATH_CLIENT_ID")
CLIENT_SECRET = os.getenv("UIPATH_CLIENT_SECRET")
TENANT_NAME = os.getenv("UIPATH_TENANT_NAME")
ORG_NAME = os.getenv("UIPATH_ORG_NAME")
SCOPES = os.getenv(
    "UIPATH_SCOPES",
    "OR.Jobs OR.Folders OR.Queues OR.Robots OR.Execution OR.Machines OR.Assets",
)


class UiPathOrchestratorClient:
    def __init__(
        self,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        tenant_name=TENANT_NAME,
        org_name=ORG_NAME,
        scopes=SCOPES,
    ):
        self.org_name = org_name
        self.tenant_name = tenant_name
        self.client_id = client_id
        self.client_secret = client_secret
        self.cloud_url = "https://cloud.uipath.com"
        self.base_url = (
            f"{self.cloud_url}/{self.org_name}/{self.tenant_name}/orchestrator_/odata"
        )
        self.access_token_retrieved_time = None
        self.access_token = None
        self.expires_in = None
        self.scopes = scopes
        # Persistent session for connection pooling
        self._session = requests.Session()
        # Cache folder name → ID to avoid redundant GET /Folders calls
        self._folder_cache: dict[str, int] = {}

    def get_access_token(self):
        auth_url = f"{self.cloud_url}/identity_/connect/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": self.scopes,
        }
        response = requests.post(
            auth_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data,
        )
        if response.status_code == 200:
            token_data = response.json()
            self.access_token = token_data["access_token"]
            self.expires_in = token_data["expires_in"]
            self.access_token_retrieved_time = datetime.now()
            # Update session so all future requests carry the new token
            self._session.headers.update(
                {"Authorization": f"Bearer {self.access_token}"}
            )
        else:
            raise Exception(
                f"Failed to get access token: {response.status_code} - {response.text}"
            )

    def check_access_token(self):
        if self.access_token is None or (
            datetime.now() - self.access_token_retrieved_time
        ).total_seconds() > self.expires_in:
            self.get_access_token()

    def _make_request(
        self,
        method,
        endpoint,
        params=None,
        headers=None,
        data=None,
        max_retries=5,
        backoff_base=5,
    ):
        """Make a request with retry and exponential backoff for rate limits."""
        self.check_access_token()
        url = f"{self.base_url}{endpoint}"
        req_headers = headers or {}

        for attempt in range(max_retries):
            if data and req_headers.get("Content-Type") == "application/json":
                response = self._session.request(
                    method, url, params=params, headers=req_headers, json=data
                )
            else:
                response = self._session.request(
                    method, url, params=params, headers=req_headers, data=data
                )

            if response.status_code in [200, 201]:
                body = response.json()
                return body.get("value", body)
            elif response.status_code == 204:
                return {}
            elif response.status_code == 429:
                wait_time = int(
                    response.headers.get("Retry-After", backoff_base * (2**attempt))
                )
                time.sleep(wait_time)
            elif response.status_code in [502, 503, 504]:
                time.sleep(backoff_base * (2**attempt))
            else:
                raise Exception(
                    f"API request failed: {response.status_code} - {response.text}"
                )

        raise Exception(f"Max retries exceeded for request to {endpoint}")

    def _get_count(
        self, resource: str, folder_id: int, filter_query: str = None
    ) -> int:
        """
        Return the total item count for a resource using the OData $count endpoint.
        Raises an exception on any failure rather than returning 0.
        """
        self.check_access_token()
        endpoint = f"/{resource}?$count=true"

        if filter_query:
            endpoint = f"{endpoint}&$filter={filter_query}"
        url = f"{self.base_url}{endpoint}"
        print(f"Full url for count request: {url}")
        
        headers = {"X-UIPATH-OrganizationUnitId": str(folder_id)}
        response = self._session.get(url, headers=headers)

        if response.status_code != 200:
            raise RuntimeError(
                f"Count request to {endpoint} failed with status {response.status_code}: {response.text}"
            )

        try:
            json_response = response.json()
        except Exception as e:
            raise RuntimeError(f"Failed to parse JSON response from {endpoint}: {e}")

        if "@odata.count" not in json_response:
            raise KeyError(
                f"'@odata.count' not in response from {endpoint}. "
                f"Got keys: {list(json_response.keys())}"
            )

        return int(json_response["@odata.count"])


class UiPathFolderClient(UiPathOrchestratorClient):
    """Client for interacting with UiPath Orchestrator folders."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def get_orchestrator_folders(self):
        """Retrieve all folders from UiPath Orchestrator."""
        folders = self._make_request("GET", "/Folders")
        # Opportunistically populate cache whenever we fetch the full list
        if folders:
            for folder in folders:
                self._folder_cache[folder["DisplayName"]] = folder["Id"]
                # Also index by FullyQualifiedName so callers can pass hierarchical
                # paths like "BillingGreenLantern/Backup" and resolve correctly.
                fqn = folder.get("FullyQualifiedName")
                if fqn and fqn != folder["DisplayName"]:
                    self._folder_cache[fqn] = folder["Id"]
        return folders

    def get_folder_id_by_name(self, folder_name: str) -> int | None:
        """Get the folder ID by its display name or fully-qualified path (cached)."""
        if folder_name not in self._folder_cache:
            self.get_orchestrator_folders()
        return self._folder_cache.get(folder_name)


class UiPathQueueClient(UiPathFolderClient):
    """Client for interacting with UiPath Orchestrator queues."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def get_queue_definitions_by_folder_id(self, folder_id: int):
        """Retrieve queue definitions by folder ID."""
        headers = {"X-UIPATH-OrganizationUnitId": str(folder_id)}
        return self._make_request("GET", "/QueueDefinitions", headers=headers)

    def get_queue_definitions_by_folder_name(self, folder_name: str):
        """Retrieve queue definitions by folder name."""
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is not None:
            return self.get_queue_definitions_by_folder_id(folder_id)
        return None

    def _resolve_queue_filter(
        self, folder_id: int, queue_name: str, filter_query: str = None
    ) -> str:
        """Append a QueueDefinitionId filter for the named queue."""
        queue_definitions = self.get_queue_definitions_by_folder_id(folder_id)
        queue_id = next(
            (q["Id"] for q in queue_definitions if q["Name"] == queue_name), None
        )
        if queue_id is None:
            raise ValueError(f"Queue '{queue_name}' not found in folder.")
        queue_filter = f"QueueDefinitionId eq {queue_id}"
        return f"{filter_query} and {queue_filter}" if filter_query else queue_filter

    def count_queue_items(
        self, folder_name: str, queue_name: str = None, filter_query: str = None
    ) -> int:
        """
        Return the total number of queue items matching the given criteria.

        :param folder_name: The display name of the folder.
        :param queue_name: Optional queue name to scope the count.
        :param filter_query: Optional additional OData filter string.
        """
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is None:
            raise ValueError(f"Folder '{folder_name}' not found.")
        if queue_name:
            filter_query = self._resolve_queue_filter(folder_id, queue_name, filter_query)
        return self._get_count("QueueItems", folder_id, filter_query)

    def get_queue_items_by_folder_id(
        self, folder_id: int, batch_size=100, skip=0, filter_query=None
    ):
        """Retrieve queue items by folder ID with optional OData filtering."""
        headers = {"X-UIPATH-OrganizationUnitId": str(folder_id)}
        endpoint = f"/QueueItems?$top={batch_size}&$skip={skip}"
        if filter_query:
            endpoint = f"{endpoint}&$filter={filter_query}"
        return self._make_request("GET", endpoint, headers=headers)

    def get_queue_item_batch(
        self,
        folder_name: str,
        queue_name: str = None,
        batch_size=100,
        skip=0,
        filter_query=None,
    ):
        """Retrieve a batch of queue items by folder name, optionally filtered by queue."""
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is None:
            return None
        if queue_name is not None:
            filter_query = self._resolve_queue_filter(folder_id, queue_name, filter_query)
        return self.get_queue_items_by_folder_id(
            folder_id, batch_size, skip, filter_query
        )

    def get_all_queue_items(
        self,
        folder_name: str,
        queue_name: str,
        batch_size=100,
        filter_query=None,
        max_workers=10,
    ):
        """
        Retrieve all queue items for a queue using $count + concurrent page fetches.

        Uses the actual item count so no wasted requests are made.
        """
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is None:
            return None
        filter_query = self._resolve_queue_filter(folder_id, queue_name, filter_query)

        total = self._get_count("QueueItems", folder_id, filter_query)
        if total == 0:
            return []

        pages = ceil(total / batch_size)
        all_items = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    self.get_queue_items_by_folder_id,
                    folder_id,
                    batch_size,
                    page * batch_size,
                    filter_query,
                )
                for page in range(pages)
            ]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    all_items.extend(result)
        return all_items

    def add_queue_item(
        self,
        folder_name: str,
        queue_name: str,
        specific_content: dict,
        priority: str = "Normal",
        defer_date: datetime = None,
        due_date: datetime = None,
        reference: str = None,
    ):
        """
        Add a new item to a queue.

        :param folder_name: The folder containing the queue.
        :param queue_name: The name of the queue.
        :param specific_content: Dictionary of key-value pairs for the queue item data.
        :param priority: Item priority — 'Low', 'Normal', or 'High'.
        :param defer_date: Earliest date/time the item should be processed.
        :param due_date: Latest date/time the item must be processed by.
        :param reference: Optional reference string for the item.
        """
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is None:
            raise ValueError(f"Folder '{folder_name}' not found.")

        headers = {
            "X-UIPATH-OrganizationUnitId": str(folder_id),
            "Content-Type": "application/json",
        }
        item_data: dict = {
            "Name": queue_name,
            "Priority": priority,
            "SpecificContent": specific_content,
        }
        if defer_date:
            item_data["DeferDate"] = format_date(defer_date)
        if due_date:
            item_data["DueDate"] = format_date(due_date)
        if reference:
            item_data["Reference"] = reference

        return self._make_request(
            "POST",
            "/Queues/UiPath.Server.Configuration.OData.AddQueueItem",
            headers=headers,
            data={"itemData": item_data},
        )

    def bulk_add_queue_items(
        self,
        folder_name: str,
        queue_name: str,
        items: list[dict],
        commit_type: str = "AllOrNothing",
    ):
        """
        Add multiple queue items in a single API call.

        :param folder_name: The folder containing the queue.
        :param queue_name: The name of the queue.
        :param items: List of item dicts. Each dict may contain:
            - specific_content (dict, required)
            - priority (str, optional) — 'Low', 'Normal', or 'High'
            - reference (str, optional)
            - defer_date (datetime, optional)
            - due_date (datetime, optional)
        :param commit_type: 'AllOrNothing' (default) — rolls back all on any failure;
                            'ProcessAllIndependently' — commits each item individually.
        """
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is None:
            raise ValueError(f"Folder '{folder_name}' not found.")

        headers = {
            "X-UIPATH-OrganizationUnitId": str(folder_id),
            "Content-Type": "application/json",
        }

        def _build_item(item: dict) -> dict:
            req: dict = {
                "priority": item.get("priority", "Normal"),
                "specificContent": item["specific_content"],
            }
            if "reference" in item:
                req["reference"] = item["reference"]
            if "defer_date" in item:
                req["deferDate"] = format_date(item["defer_date"])
            if "due_date" in item:
                req["dueDate"] = format_date(item["due_date"])
            return req

        data = {
            "commitType": commit_type,
            "queueName": queue_name,
            "queueItemRequests": [_build_item(i) for i in items],
        }
        return self._make_request(
            "POST",
            "/Queues/UiPath.Server.Configuration.OData.BulkAddQueueItems",
            headers=headers,
            data=data,
        )

    def delete_queue_item(self, queue_item_id: int, folder_name: str):
        """Delete a specific queue item by its ID."""
        folder_id = self.get_folder_id_by_name(folder_name)
        headers = {"X-UIPATH-OrganizationUnitId": str(folder_id)}
        return self._make_request(
            "DELETE", f"/QueueItems({queue_item_id})", headers=headers
        )

    def delete_queue_items(self, queue_items: list, folder_name: str):
        """Delete multiple queue items."""
        responses = {}
        for item in queue_items:
            responses[item["Id"]] = self.delete_queue_item(item["Id"], folder_name)
        return responses

    def delete_queue_items_by_filter(
        self, folder_name: str, queue_name: str, filter_query: str
    ):
        """Delete queue items matching a filter query."""
        if not filter_query:
            raise ValueError("filter_query cannot be empty.")
        queue_items = self.get_queue_item_batch(
            folder_name, queue_name, filter_query=filter_query
        )
        return self.delete_queue_items(queue_items, folder_name)

    def retry_queue_item(
        self, queue_item_id: int, row_version: str, folder_name: str
    ):
        """Retry a specific failed queue item."""
        folder_id = self.get_folder_id_by_name(folder_name)
        headers = {
            "X-UIPATH-OrganizationUnitId": str(folder_id),
            "Content-Type": "application/json",
        }
        data = {
            "status": "Retried",
            "queueItems": [{"RowVersion": row_version, "Id": int(queue_item_id)}],
        }
        return self._make_request(
            "POST",
            "/QueueItems/UiPathODataSvc.SetItemReviewStatus",
            headers=headers,
            data=data,
        )

    def retry_queue_items(self, queue_items_data: list, folder_name: str):
        """Retry multiple failed queue items in a single request."""
        folder_id = self.get_folder_id_by_name(folder_name)
        headers = {
            "X-UIPATH-OrganizationUnitId": str(folder_id),
            "Content-Type": "application/json",
        }
        data = {
            "status": "Retried",
            "queueItems": [
                {"RowVersion": item["RowVersion"], "Id": int(item["Id"])}
                for item in queue_items_data
            ],
        }
        return self._make_request(
            "POST",
            "/QueueItems/UiPathODataSvc.SetItemReviewStatus",
            headers=headers,
            data=data,
        )


class UiPathJobsClient(UiPathQueueClient):
    """Client for UiPath Orchestrator jobs, releases, schedules, robots, machines, and assets."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def _build_odata_filter(
        self,
        status: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
        state: str = None,
        release_name: str = None,
        job_priority: str = None
    ) -> str:
        """Build an OData filter string from the provided parameters."""
        filters = []
        if status:
            filters.append(f"Status eq '{status}'")
        if start_time:
            filters.append(f"StartTime ge {format_date(start_time)}")
        if end_time:
            filters.append(f"EndTime le {format_date(end_time)}")
        if state:
            filters.append(f"State eq '{state}'")
        if job_priority:
            filters.append(f"Priority eq '{job_priority}'")
        if release_name:
            filters.append(f"ReleaseName eq '{release_name}'")
        return " and ".join(filters)

    def get_jobs_by_folder_id(
        self,
        folder_id: int,
        batch_size=100,
        skip=0,
        filter_query=None,
        **kwargs,
    ):
        """Retrieve jobs by folder ID with optional OData filtering."""
        if not filter_query:
            filter_query = self._build_odata_filter(**kwargs)
        headers = {"X-UIPATH-OrganizationUnitId": str(folder_id)}
        endpoint = f"/Jobs?$top={batch_size}&$skip={skip}"
        if filter_query:
            endpoint = f"{endpoint}&$filter={filter_query}"
        return self._make_request("GET", endpoint, headers=headers)

    def get_job_batch_by_folder_name(
        self,
        folder_name: str,
        batch_size=100,
        skip=0,
        filter_query=None,
        **kwargs,
    ):
        """Retrieve a batch of jobs by folder name."""
        if not filter_query:
            filter_query = self._build_odata_filter(**kwargs)
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is None:
            raise ValueError(
                f"Folder '{folder_name}' not found. "
                "Use list_folders() to see available folder names and paths."
            )
        return self.get_jobs_by_folder_id(folder_id, batch_size, skip, filter_query)

    def get_all_jobs_for_folder(
        self,
        folder_name: str,
        batch_size=100,
        filter_query=None,
        max_workers=10,
        **kwargs,
    ):
        """
        Retrieve all jobs for a folder using $count + concurrent page fetches.

        Uses the actual job count so no wasted requests are made.
        """
        if not filter_query:
            filter_query = self._build_odata_filter(**kwargs)
        folder_id = self.get_folder_id_by_name(folder_name)

        total = self._get_count("Jobs", folder_id, filter_query)
        if total == 0:
            return []

        pages = ceil(total / batch_size)
        all_jobs = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    self.get_jobs_by_folder_id,
                    folder_id,
                    batch_size,
                    page * batch_size,
                    filter_query,
                )
                for page in range(pages)
            ]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    all_jobs.extend(result)
        return all_jobs

    def get_jobs_all_folders(
        self,
        batch_size=100,
        filter_query=None,
        max_workers=10,
        **kwargs,
    ):
        """Retrieve jobs across all folders."""
        if not filter_query:
            filter_query = self._build_odata_filter(**kwargs)
        all_jobs = []
        folders = self.get_orchestrator_folders()
        for folder in folders:
            folder_jobs = self.get_all_jobs_for_folder(
                folder["DisplayName"], batch_size, filter_query, max_workers
            )
            if folder_jobs:
                all_jobs.extend(folder_jobs)
        return all_jobs

    def get_job_by_id(self, job_id: int, folder_name: str):
        """Retrieve a single job by its ID."""
        folder_id = self.get_folder_id_by_name(folder_name)
        headers = {"X-UIPATH-OrganizationUnitId": str(folder_id)}
        return self._make_request("GET", f"/Jobs({job_id})", headers=headers)

    def get_job_logs(
        self,
        job_id: int,
        folder_name: str,
        batch_size=100,
        skip=0,
        min_level: str = None,
    ):
        """
        Retrieve robot execution logs for a specific job.

        :param job_id: The ID of the job.
        :param folder_name: The folder containing the job.
        :param batch_size: Number of log entries to return (default 100).
        :param skip: Number of entries to skip for pagination.
        :param min_level: Optional minimum log level to filter by —
                          'Trace', 'Info', 'Warn', 'Error', 'Fatal'.
        """
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is None:
            raise ValueError(f"Folder '{folder_name}' not found.")

        headers = {"X-UIPATH-OrganizationUnitId": str(folder_id)}
        filter_parts = [f"JobKey eq guid'{job_id}'"]
        if min_level:
            level_order = ["Trace", "Info", "Warn", "Error", "Fatal"]
            if min_level in level_order:
                allowed = level_order[level_order.index(min_level):]
                level_filter = " or ".join(f"Level eq '{lvl}'" for lvl in allowed)
                filter_parts.append(f"({level_filter})")

        filter_query = " and ".join(filter_parts)
        endpoint = f"/RobotLogs?$top={batch_size}&$skip={skip}&$filter={filter_query}&$orderby=TimeStamp asc"
        return self._make_request("GET", endpoint, headers=headers)

    def start_job(
        self,
        folder_name: str,
        process_name: str = None,
        release_key: str = None,
        strategy: str = "All",
        robot_ids: list[int] = None,
        jobs_count: int = 1,
        input_arguments: dict = None,
    ):
        """
        Start a job for a process.

        :param folder_name: The folder containing the process.
        :param process_name: The name of the process/release to run (used to look up release_key).
        :param release_key: The release key GUID (takes precedence over process_name).
        :param strategy: Allocation strategy — 'All', 'Specific', or 'JobsCount'.
        :param robot_ids: List of robot IDs to run on (required for 'Specific' strategy).
        :param jobs_count: Number of jobs to start (used with 'JobsCount' strategy).
        :param input_arguments: Dictionary of input arguments to pass to the process.
        :return: List of started job objects.
        """
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is None:
            raise ValueError(f"Folder '{folder_name}' not found.")

        if release_key is None:
            if process_name is None:
                raise ValueError("Either process_name or release_key must be provided.")
            releases = self.get_releases(folder_id)
            release = next(
                (
                    r
                    for r in releases
                    if r.get("Name") == process_name
                    or r.get("ProcessKey") == process_name
                ),
                None,
            )
            if release is None:
                raise ValueError(
                    f"Process '{process_name}' not found in folder '{folder_name}'."
                )
            release_key = release["Key"]

        headers = {
            "X-UIPATH-OrganizationUnitId": str(folder_id),
            "Content-Type": "application/json",
        }
        start_info: dict = {
            "ReleaseKey": release_key,
            "Strategy": strategy,
            "InputArguments": json.dumps(input_arguments) if input_arguments else "{}",
        }
        if strategy == "Specific" and robot_ids:
            start_info["RobotIds"] = robot_ids
        elif strategy == "JobsCount":
            start_info["JobsCount"] = jobs_count

        return self._make_request(
            "POST",
            "/Jobs/UiPath.Server.Configuration.OData.StartJobs",
            headers=headers,
            data={"startInfo": start_info},
        )

    def stop_job(
        self,
        job_id: int,
        folder_name: str,
        strategy: str = "SoftStop",
    ):
        """
        Stop a running job.

        :param job_id: The ID of the job to stop.
        :param folder_name: The folder containing the job.
        :param strategy: Stop strategy — 'SoftStop' (graceful) or 'Kill' (immediate).
        """
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is None:
            raise ValueError(f"Folder '{folder_name}' not found.")

        headers = {
            "X-UIPATH-OrganizationUnitId": str(folder_id),
            "Content-Type": "application/json",
        }
        return self._make_request(
            "POST",
            f"/Jobs({job_id})/UiPath.Server.Configuration.OData.StopJob",
            headers=headers,
            data={"strategy": strategy},
        )

    def get_releases(self, folder_id: int):
        """Retrieve releases (processes) by folder ID."""
        headers = {"X-UIPATH-OrganizationUnitId": str(folder_id)}
        return self._make_request("GET", "/Releases", headers=headers)

    def get_releases_by_folder_name(self, folder_name: str):
        """Retrieve releases (processes) by folder name."""
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is None:
            raise ValueError(
                f"Folder '{folder_name}' not found. "
                "Use list_folders() to see available folder names and paths."
            )
        return self.get_releases(folder_id)

    # ------------------------------------------------------------------
    # Process schedules / triggers
    # ------------------------------------------------------------------

    def get_process_schedules(self, folder_name: str):
        """
        Retrieve all process schedules (triggers) in a folder.

        :param folder_name: The display name of the folder.
        """
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is None:
            raise ValueError(f"Folder '{folder_name}' not found.")
        headers = {"X-UIPATH-OrganizationUnitId": str(folder_id)}
        return self._make_request("GET", "/ProcessSchedules", headers=headers)

    def set_schedule_enabled(
        self, schedule_id: int, folder_name: str, enabled: bool
    ):
        """
        Enable or disable a process schedule.

        :param schedule_id: The ID of the schedule to toggle.
        :param folder_name: The folder containing the schedule.
        :param enabled: True to enable, False to disable.
        """
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is None:
            raise ValueError(f"Folder '{folder_name}' not found.")
        headers = {
            "X-UIPATH-OrganizationUnitId": str(folder_id),
            "Content-Type": "application/json",
        }
        return self._make_request(
            "POST",
            f"/ProcessSchedules({schedule_id})/UiPath.Server.Configuration.OData.SetEnabled",
            headers=headers,
            data={"enabled": enabled},
        )

    # ------------------------------------------------------------------
    # Robots, machines, assets
    # ------------------------------------------------------------------

    def get_robots(self, folder_name: str = None):
        """Retrieve robots, optionally scoped to a folder."""
        headers = {}
        if folder_name:
            folder_id = self.get_folder_id_by_name(folder_name)
            if folder_id is not None:
                headers["X-UIPATH-OrganizationUnitId"] = str(folder_id)
        return self._make_request("GET", "/Robots", headers=headers)

    def get_machines(self, folder_name: str = None):
        """Retrieve machines, optionally scoped to a folder."""
        headers = {}
        if folder_name:
            folder_id = self.get_folder_id_by_name(folder_name)
            if folder_id is not None:
                headers["X-UIPATH-OrganizationUnitId"] = str(folder_id)
        return self._make_request("GET", "/Machines", headers=headers)

    def get_assets(self, folder_name: str):
        """Retrieve assets in a folder."""
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is None:
            raise ValueError(f"Folder '{folder_name}' not found.")
        headers = {"X-UIPATH-OrganizationUnitId": str(folder_id)}
        return self._make_request("GET", "/Assets", headers=headers)

    def get_asset_by_name(self, folder_name: str, asset_name: str):
        """Retrieve a specific asset by name from a folder."""
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is None:
            raise ValueError(f"Folder '{folder_name}' not found.")
        headers = {"X-UIPATH-OrganizationUnitId": str(folder_id)}
        return self._make_request(
            "GET", f"/Assets?$filter=Name eq '{asset_name}'", headers=headers
        )


if __name__ == "__main__":
    
    from dotenv import load_dotenv
    load_dotenv()  # Load environment variables from .env file
    client = UiPathJobsClient()

    folder_name = "BillingGreenLantern/Backup"
    print(f"\nQueue definitions in folder '{folder_name}':")
    queue_defs = client.get_queue_definitions_by_folder_name(folder_name)
    print(queue_defs)

    if queue_defs:
        queue_name = queue_defs[2]["Name"]
        print(f"\nCounting items in queue '{queue_name}'...")
        count = client.count_queue_items(folder_name, queue_name)
        print(f"Total items in queue '{queue_name}': {count}")