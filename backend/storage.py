
import os
import requests
import json

class BlobStorage:
    def __init__(self):
        # API_URL: The Vercel API endpoint for commands (Put/List). 
        # DO NOT change this to your public bucket URL.
        self.token = os.environ.get("BLOB_READ_WRITE_TOKEN")
        self.api_url = "https://blob.vercel-storage.com"
        
        # User provided Public URL (optional fallback)
        self.public_base_url = os.environ.get("VITE_BLOB_URL") or os.environ.get("BLOB_URL")

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
            # This requires the generic API URL, not the specific bucket URL
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
        Attempts to find the data file.
        Strategy 1: Ask Vercel API to list files and find the latest one.
        Strategy 2: If API fails, try downloading directly from VITE_BLOB_URL.
        """
        
        # --- Strategy 1: API List ---
        if self.token:
            try:
                list_response = requests.get(
                    self.api_url,
                    headers=self._get_headers()
                )
                
                if list_response.status_code == 200:
                    blobs = list_response.json().get("blobs", [])
                    # Find closest match.
                    matches = [b for b in blobs if filename_match in b.get("pathname", "")]
                    matches.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
                    
                    if matches:
                        target_url = matches[0]["url"]
                        print(f"Strategy 1 (API): Found file at {target_url}")
                        data_response = requests.get(target_url)
                        if data_response.status_code == 200:
                            return data_response.json()
            except Exception as e:
                print(f"Strategy 1 (API) failed: {e}")

        # --- Strategy 2: Direct URL Fallback ---
        if self.public_base_url:
            # Clean up URL (remove trailing slash)
            base = self.public_base_url.rstrip("/")
            direct_url = f"{base}/{filename_match}"
            print(f"Strategy 2 (Direct): Trying {direct_url}")
            
            try:
                direct_response = requests.get(direct_url)
                if direct_response.status_code == 200:
                    print("Strategy 2 (Direct): Success!")
                    return direct_response.json()
                else:
                    print(f"Strategy 2 (Direct): Failed with {direct_response.status_code}")
            except Exception as e:
                print(f"Strategy 2 (Direct) Exception: {e}")

        print("All retrieval strategies failed. Returning empty list.")
        return []

    def debug_list_files(self):
        """
        Returns the raw list of files or error details for debugging.
        """
        debug_info = {
            "config": {
                "has_token": bool(self.token),
                "api_url": self.api_url,
                "public_base_url": self.public_base_url
            }
        }

        if not self.token:
            debug_info["status"] = "error"
            debug_info["message"] = "BLOB_READ_WRITE_TOKEN is missing"
            return debug_info

        try:
            response = requests.get(self.api_url, headers=self._get_headers())
            debug_info["http_code"] = response.status_code
            if response.status_code == 200:
                debug_info["status"] = "success"
                debug_info["files"] = response.json().get("blobs", [])
            else:
                debug_info["status"] = "api_error"
                debug_info["response"] = response.text
        except Exception as e:
            debug_info["status"] = "exception"
            debug_info["message"] = str(e)
            
        return debug_info
