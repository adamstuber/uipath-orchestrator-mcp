from uipath_orchestrator_mcp.server import get_jobs_across_folders, list_jobs, diagnose_job




if __name__ == "__main__":
    # Example usage of the get_jobs_across_folders function
    try:
        jobs = list_jobs(folder_name='Backup', state='Faulted')  # Retrieve the most recent job in the 'Backup' folder with state 'Faulted'
        job = jobs[-1] if jobs else None 
        print(job.keys())# Get the first job if available
        print(job)  # Print the retrieved job details
        logs = diagnose_job(job_id=job['Id'], folder_name='Backup', tail=5)  # Retrieve the most recent 5 logs for the retrieved job
        print(logs['combined_log_view'][0].keys())
        print(logs) # Print the retrieved job logs

    except Exception as e:
        print(f"An error occurred while retrieving jobs: {e}")