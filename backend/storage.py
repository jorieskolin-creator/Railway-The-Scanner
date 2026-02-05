
import os
import requests
import json

class BlobStorage:
    def __init__(self):
        # API_URL: The Vercel API endpoint for commands (Put/List). 
        # DO NOT change this to your public bucket URL.
        self.api_url = "https://blob.vercel-storage.com"
        
        # Initial load
        self.token = self._load_token()
        
        # Clean and load the Public URL
        raw_url = os.environ.get("VITE_BLOB_URL") or os.environ.get("BLOB_URL") or ""
        self.public_base_url = self._clean_env_var(raw_url)

    def _load_token(self):
        raw_token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
        return self._clean_env_var(raw_token)

    def _clean_env_var(self, value: str):
        """
        Helper to fix common copy-paste errors in Railway variables.
        Example Input: 'BLOB_TOKEN="vercel_blob_rw_123"'
        Example Output: 'vercel_blob_rw_123'
        """
        if not value:
            return ""
        
        # If user pasted "KEY=VALUE", take the VALUE part
        if "=" in value:
            try:
                # Split on the first equals sign only
                parts = value.split("=", 1)
                value = parts[1]
            except:
                pass
        
        # Remove whitespace and quotes
        return value.strip().strip('"').strip("'")

    def _get_headers(self):
        # Ensure we always use the freshest token
        self.token = self._load_token()
        return {
            "authorization": f"Bearer {self.token}",
            "x-api-version": "1"
        }

    def upload_json(self, filename: str, data: dict):
        """
        Uploads a JSON object to Vercel Blob.
        """
        # Reload token to be sure
        self.token = self._load_token()
        
        if not self.token:
            print("CRITICAL: BLOB_READ_WRITE_TOKEN not set (or empty after cleaning). Upload skipped.")
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
        self.token = self._load_token()
        
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
        Checks both API access (Token) and Public URL access.
        """
        self.token = self._load_token()
        
        debug_info = {
            "config": {
                "has_token": bool(self.token),
                # Mask token for security in logs, show first 4 chars
                "token_prefix": self.token[:4] + "..." if self.token else "None",
                "api_url": self.api_url,
                "public_base_url": self.public_base_url
            },
            "read_access": {},
            "write_access": {}
        }

        # 1. Check Read Access via Public URL
        if self.public_base_url:
            try:
                test_url = f"{self.public_base_url.rstrip('/')}/dashboard_data.json"
                r = requests.head(test_url)
                # 404 is technically "success" for connectivity, just means file missing.
                # 403 or ConnectionError means bad URL.
                debug_info["read_access"]["public_url_status"] = r.status_code
                debug_info["read_access"]["file_exists"] = (r.status_code == 200)
                debug_info["read_access"]["message"] = "Public URL reachable" if r.status_code in [200, 404] else "Public URL unreachable"
            except Exception as e:
                debug_info["read_access"]["error"] = str(e)
        else:
            debug_info["read_access"]["message"] = "No VITE_BLOB_URL configured"

        # 2. Check Write Access via Token
        if not self.token:
            debug_info["status"] = "partial_configuration"
            debug_info["write_access"]["message"] = "BLOB_READ_WRITE_TOKEN missing. Uploads disabled."
            return debug_info

        try:
            response = requests.get(self.api_url, headers=self._get_headers())
            debug_info["http_code"] = response.status_code
            if response.status_code == 200:
                debug_info["status"] = "success"
                debug_info["write_access"]["message"] = "Token valid. Write access enabled."
                debug_info["files"] = response.json().get("blobs", [])
            else:
                debug_info["status"] = "api_error"
                debug_info["write_access"]["message"] = "Token invalid or API error."
                debug_info["response"] = response.text
        except Exception as e:
            debug_info["status"] = "exception"
            debug_info["message"] = str(e)
            
        return debug_info
