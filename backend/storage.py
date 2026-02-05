
import os
import requests
import json

class BlobStorage:
    def __init__(self):
        # This generic URL is CORRECT for the Vercel Blob API. 
        # The Token directs requests to your specific bucket.
        self.token = os.environ.get("BLOB_READ_WRITE_TOKEN")
        self.api_url = "https://blob.vercel-storage.com"

    def _get_headers(self):
        return {
            "authorization": f"Bearer {self.token}",
            "x-api-version": "1"
        }

    def upload_json(self, filename: str, data: dict):
        """
        Uploads a JSON object to Vercel Blob.
        """
        if not self.token:
            print("Warning: BLOB_READ_WRITE_TOKEN not set. Skipping upload.")
            return None

        try:
            # Vercel Blob API: PUT /<filename>
            response = requests.put(
                f"{self.api_url}/{filename}",
                headers=self._get_headers(),
                data=json.dumps(data),
            )
            
            if response.status_code == 200:
                resp_json = response.json()
                print(f"Blob Upload Success: {resp_json.get('url')}")
                return resp_json.get("url")
            else:
                print(f"Blob Upload Error ({response.status_code}): {response.text}")
                return None
        except Exception as e:
            print(f"Blob Exception: {e}")
            return None

    def retrieve_latest_data(self, filename_match="dashboard_data.json"):
        """
        Lists files in the Blob store, finds the one matching the filename,
        and downloads its content.
        """
        if not self.token:
            print("Blob Token missing.")
            return []

        try:
            # 1. List files
            list_response = requests.get(
                self.api_url,
                headers=self._get_headers()
            )
            
            if list_response.status_code != 200:
                print(f"Failed to list blobs: {list_response.text}")
                return []
                
            blobs = list_response.json().get("blobs", [])
            
            # Find closest match.
            matches = [b for b in blobs if filename_match in b.get("pathname", "")]
            matches.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
            
            if not matches:
                print("No database file found in Blob storage.")
                return []
            
            target_url = matches[0]["url"]
            print(f"Fetching database from: {target_url}")
            
            # 2. Download content
            data_response = requests.get(target_url)
            if data_response.status_code == 200:
                return data_response.json()
            else:
                print(f"Failed to download blob content: {data_response.status_code}")
                return []
                
        except Exception as e:
            print(f"Blob Retrieval Exception: {e}")
            return []

    def debug_list_files(self):
        """
        Returns the raw list of files or error details for debugging.
        """
        if not self.token:
            return {"status": "error", "message": "BLOB_READ_WRITE_TOKEN is missing from environment variables."}

        try:
            response = requests.get(self.api_url, headers=self._get_headers())
            return {
                "status": "success" if response.status_code == 200 else "error",
                "http_code": response.status_code,
                "response_body": response.json() if response.status_code == 200 else response.text
            }
        except Exception as e:
            return {"status": "exception", "message": str(e)}
