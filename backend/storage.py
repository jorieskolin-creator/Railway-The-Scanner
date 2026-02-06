
import os
import requests
import json
import time
import re
from datetime import datetime
import urllib.parse

class BlobStorage:
    def __init__(self):
        self.api_url = "https://blob.vercel-storage.com"
        self.token = self._load_token()
        # Ensure we catch multiple naming conventions for the URL
        raw_url = os.environ.get("VITE_BLOB_URL") or os.environ.get("BLOB_URL") or ""
        self.public_base_url = self._clean_env_var(raw_url)

    def _load_token(self):
        raw_token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
        return self._clean_env_var(raw_token)

    def _clean_env_var(self, value: str):
        if not value: return ""
        if "=" in value:
            try: value = value.split("=", 1)[1]
            except: pass
        return value.strip().strip('"').strip("'")

    def _get_headers(self):
        self.token = self._load_token()
        return {
            "authorization": f"Bearer {self.token}",
            "x-api-version": "1"
        }

    # --- LOW LEVEL API WRAPPERS ---

    def _put(self, pathname, data):
        try:
            response = requests.put(
                f"{self.api_url}/{pathname}",
                headers=self._get_headers(),
                data=json.dumps(data),
            )
            if response.status_code == 200:
                # SUCCESS: Return the public URL
                return response.json().get("url")
            else:
                print(f"   [Storage Error] Status {response.status_code}: {response.text}")
                return None
        except Exception as e:
            print(f"   [Storage Exception] _put failed: {e}")
            return None

    def _get(self, url):
        try:
            # Always add cache buster to ensure fresh data
            ts = int(time.time())
            resp = requests.get(f"{url}?t={ts}")
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                print(f"   [Storage Warning] File not found at: {url}")
        except Exception as e:
            print(f"   [Storage Exception] _get failed: {e}")
        return None

    # --- ARCHITECTURE METHODS ---

    def sanitize_filename(self, name):
        # robust sanitization: "Lassila & Tikanoja Oyj" -> "lassila_and_tikanoja_oyj"
        clean = name.lower()
        clean = clean.replace(" & ", "_and_").replace("&", "and")
        clean = clean.replace("ä", "a").replace("ö", "o").replace("å", "a")
        # Remove all non-alphanumeric except underscores
        clean = re.sub(r'[^a-z0-9_]', '_', clean)
        # Remove double underscores
        clean = re.sub(r'_{2,}', '_', clean)
        return clean.strip('_')

    def save_company_data(self, company_name, full_data):
        """
        Saves the detailed analysis to specific file: companies/nokia_oyj.json
        Returns the PUBLIC URL of the saved file.
        """
        filename = f"companies/{self.sanitize_filename(company_name)}.json"
        return self._put(filename, full_data)

    def get_company_data(self, company_name):
        """
        Retrieves the individual heavy file.
        """
        if not self.public_base_url:
            print("   [Storage Error] BLOB_URL env var is missing! Cannot read files.")
            return None

        filename = f"companies/{self.sanitize_filename(company_name)}.json"
        base = self.public_base_url.rstrip("/")
        url = f"{base}/{filename}"
        
        return self._get(url)

    def save_global_index_direct(self, full_list):
        """
        Overwrites the entire index file.
        """
        return self._put("index/summary_list.json", full_list)

    def update_global_index(self, summary_entry):
        """
        Updates 'index/summary_list.json' with a single entry.
        """
        index_filename = "index/summary_list.json"
        
        current_index = []
        if self.public_base_url:
             base = self.public_base_url.rstrip("/")
             url = f"{base}/{index_filename}"
             fetched = self._get(url)
             if fetched: current_index = fetched
        
        found = False
        for i, item in enumerate(current_index):
            if item["companyName"] == summary_entry["companyName"]:
                # Update existing entry
                current_index[i] = summary_entry
                found = True
                break
        
        if not found:
            current_index.append(summary_entry)
            
        self._put(index_filename, current_index)
        return current_index

    def retrieve_latest_data(self, filename_match="index/summary_list.json"):
        if self.public_base_url:
            base = self.public_base_url.rstrip("/")
            url = f"{base}/{filename_match}"
            data = self._get(url)
            if data: return data
        return []
