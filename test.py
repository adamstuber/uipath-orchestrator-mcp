from uipath_orchestrator_mcp.server import get_jobs_across_folders, list_jobs




if __name__ == "__main__":
    # Example usage of the get_jobs_across_folders function
    try:
        jobs = list_jobs(folder_name='BillingGreenLantern', release_name='ACCTG_BL_BatchInvoiceFiling_Billing')
        print(jobs)  # Print the retrieved jobs
    except Exception as e:
        print(f"An error occurred while retrieving jobs: {e}")