
import os
import requests
import json
import time
from datetime import datetime
import urllib.parse

class BlobStorage:
    def __init__(self):
        self.api_url = "https://blob.vercel-storage.com"
        self.token = self._load_token()
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
                # SUCCESS: Vercel received and stored the file.
                return response.json().get("url")
            else:
                print(f"Storage Error ({response.status_code}): {response.text}")
                return None
        except Exception as e:
            print(f"Storage Exception: {e}")
            return None

    def _get(self, url):
        try:
            # Always add cache buster
            ts = int(time.time())
            resp = requests.get(f"{url}?t={ts}")
            if resp.status_code == 200:
                return resp.json()
        except:
            pass
        return None

    # --- NEW ARCHITECTURE METHODS ---

    def sanitize_filename(self, name):
        # Convert "Nokia Oyj" -> "nokia_oyj"
        return name.lower().replace(" ", "_").replace("ä", "a").replace("ö", "o").replace(".", "")

    def save_company_data(self, company_name, full_data):
        """
        Saves the detailed analysis to specific file: companies/nokia_oyj.json
        BLOCKING CALL: Returns URL only after success.
        """
        filename = f"companies/{self.sanitize_filename(company_name)}.json"
        return self._put(filename, full_data)

    def get_company_data(self, company_name):
        filename = f"companies/{self.sanitize_filename(company_name)}.json"
        if self.public_base_url:
            base = self.public_base_url.rstrip("/")
            url = f"{base}/{filename}"
            data = self._get(url)
            if data: return data
        return None

    def save_global_index_direct(self, full_list):
        """
        Overwrites the entire index file. Used during Batch Seeding for efficiency.
        """
        return self._put("index/summary_list.json", full_list)

    def update_global_index(self, summary_entry):
        """
        Updates 'index/summary_list.json'.
        This file ONLY contains lightweight data for the table/graph.
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
