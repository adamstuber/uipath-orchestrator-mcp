import requests
import concurrent.futures
import time
import os
from datetime import datetime


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

    def get_access_token(self):
        auth_url = f"{self.cloud_url}/identity_/connect/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": self.scopes,
        }
        response = requests.post(auth_url, headers=headers, data=data)
        if response.status_code == 200:
            token_data = response.json()
            self.access_token = token_data["access_token"]
            self.expires_in = token_data["expires_in"]
            self.access_token_retrieved_time = datetime.now()
        else:
            raise Exception(
                f"Failed to get access token: {response.status_code} - {response.text}"
            )

    def check_access_token(self):
        if self.access_token is None or (
            datetime.now() - self.access_token_retrieved_time
        ).seconds > self.expires_in:
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
        """
        Make a request with built-in retry and exponential backoff for rate limits.
        """
        self.check_access_token()
        url = f"{self.base_url}{endpoint}"
        headers = headers or {}
        headers["Authorization"] = f"Bearer {self.access_token}"

        for attempt in range(max_retries):
            if data and headers.get("Content-Type") == "application/json":
                response = requests.request(
                    method, url, params=params, headers=headers, json=data
                )
            else:
                response = requests.request(
                    method, url, params=params, headers=headers, data=data
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
                wait_time = backoff_base * (2**attempt)
                time.sleep(wait_time)
            else:
                raise Exception(
                    f"API request failed: {response.status_code} - {response.text}"
                )

        raise Exception(f"Max retries exceeded for request to {endpoint}")


class UiPathFolderClient(UiPathOrchestratorClient):
    """Client for interacting with UiPath Orchestrator folders."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def get_orchestrator_folders(self):
        """Retrieve all folders from UiPath Orchestrator."""
        return self._make_request("GET", "/Folders")

    def get_folder_id_by_name(self, folder_name: str) -> int | None:
        """Get the folder ID by its display name."""
        folders = self.get_orchestrator_folders()
        if folders:
            for folder in folders:
                if folder["DisplayName"] == folder_name:
                    return folder["Id"]
        return None


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
            queue_definitions = self.get_queue_definitions_by_folder_id(folder_id)
            queue_id = next(
                (q["Id"] for q in queue_definitions if q["Name"] == queue_name), None
            )
            if queue_id is not None:
                queue_filter = f"QueueDefinitionId eq {queue_id}"
                filter_query = (
                    f"{filter_query} and {queue_filter}" if filter_query else queue_filter
                )

        return self.get_queue_items_by_folder_id(
            folder_id, batch_size, skip, filter_query
        )

    def get_all_queue_items(
        self,
        folder_name: str,
        queue_name: str,
        batch_size=100,
        filter_query=None,
        max_items=1000,
        max_workers=10,
    ):
        """Retrieve all queue items for a specific queue using concurrent requests."""
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is None:
            return None

        queue_definitions = self.get_queue_definitions_by_folder_id(folder_id)
        queue_id = next(
            (q["Id"] for q in queue_definitions if q["Name"] == queue_name), None
        )
        if queue_id is None:
            return None

        queue_filter = f"QueueDefinitionId eq {queue_id}"
        filter_query = (
            f"{filter_query} and {queue_filter}" if filter_query else queue_filter
        )

        all_items = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    self.get_queue_items_by_folder_id,
                    folder_id,
                    batch_size,
                    skip,
                    filter_query,
                )
                for skip in range(0, max_items, batch_size)
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

        data = {"itemData": item_data}
        return self._make_request(
            "POST",
            "/Queues/UiPath.Server.Configuration.OData.AddQueueItem",
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
    """Client for interacting with UiPath Orchestrator jobs, releases, robots, machines, and assets."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def _build_odata_filter(
        self,
        status: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
        state: str = None,
        job_priority: str = None,
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
        if folder_id is not None:
            return self.get_jobs_by_folder_id(folder_id, batch_size, skip, filter_query)
        return None

    def get_all_jobs_for_folder(
        self,
        folder_name: str,
        batch_size=100,
        filter_query=None,
        max_items=10000,
        max_workers=10,
        **kwargs,
    ):
        """Retrieve all jobs for a folder using concurrent requests."""
        if not filter_query:
            filter_query = self._build_odata_filter(**kwargs)
        folder_id = self.get_folder_id_by_name(folder_name)
        all_jobs = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    self.get_jobs_by_folder_id,
                    folder_id,
                    batch_size,
                    skip,
                    filter_query,
                )
                for skip in range(0, max_items, batch_size)
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
        max_items=100,
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
                folder["DisplayName"], batch_size, filter_query, max_items, max_workers
            )
            if folder_jobs:
                all_jobs.extend(folder_jobs)
        return all_jobs

    def get_job_by_id(self, job_id: int, folder_name: str):
        """Retrieve a single job by its ID."""
        folder_id = self.get_folder_id_by_name(folder_name)
        headers = {"X-UIPATH-OrganizationUnitId": str(folder_id)}
        return self._make_request("GET", f"/Jobs({job_id})", headers=headers)

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

        import json as _json

        start_info: dict = {
            "ReleaseKey": release_key,
            "Strategy": strategy,
            "InputArguments": _json.dumps(input_arguments) if input_arguments else "{}",
        }
        if strategy == "Specific" and robot_ids:
            start_info["RobotIds"] = robot_ids
        elif strategy == "JobsCount":
            start_info["JobsCount"] = jobs_count

        data = {"startInfo": start_info}
        return self._make_request(
            "POST",
            "/Jobs/UiPath.Server.Configuration.OData.StartJobs",
            headers=headers,
            data=data,
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
        data = {"strategy": strategy}
        return self._make_request(
            "POST",
            f"/Jobs({job_id})/UiPath.Server.Configuration.OData.StopJob",
            headers=headers,
            data=data,
        )

    def get_releases(self, folder_id: int):
        """Retrieve releases (processes) by folder ID."""
        headers = {"X-UIPATH-OrganizationUnitId": str(folder_id)}
        return self._make_request("GET", "/Releases", headers=headers)

    def get_releases_by_folder_name(self, folder_name: str):
        """Retrieve releases (processes) by folder name."""
        folder_id = self.get_folder_id_by_name(folder_name)
        if folder_id is not None:
            return self.get_releases(folder_id)
        return None

    def get_robots(self, folder_name: str = None):
        """
        Retrieve robots. If folder_name is provided, scoped to that folder.
        """
        headers = {}
        if folder_name:
            folder_id = self.get_folder_id_by_name(folder_name)
            if folder_id is not None:
                headers["X-UIPATH-OrganizationUnitId"] = str(folder_id)
        return self._make_request("GET", "/Robots", headers=headers)

    def get_machines(self, folder_name: str = None):
        """
        Retrieve machines. If folder_name is provided, scoped to that folder.
        """
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
        endpoint = f"/Assets?$filter=Name eq '{asset_name}'"
        return self._make_request("GET", endpoint, headers=headers)
