
import os
import requests
import json
import time
import re
import threading
from datetime import datetime
import urllib.parse

class BlobStorage:
    _index_lock = threading.Lock()

    def __init__(self):
        self.api_url = "https://blob.vercel-storage.com"
        self.token = self._load_token()
        
        # 1. Try to get URL from Env
        raw_url = os.environ.get("VITE_BLOB_URL") or os.environ.get("BLOB_URL") or ""
        self.public_base_url = self._clean_env_var(raw_url)
        
        # 2. If missing, Auto-Discover it
        if not self.public_base_url and self.token:
            self.public_base_url = self._discover_base_url()

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

    def _discover_base_url(self):
        """
        Self-Healing: If BLOB_URL is missing, list 1 file to find the store's public domain.
        """
        print("   [Storage] BLOB_URL env var missing. Attempting auto-discovery via API...")
        try:
            # List 1 item to get its URL
            resp = requests.get(f"{self.api_url}?limit=1", headers=self._get_headers())
            if resp.status_code == 200:
                data = resp.json()
                blobs = data.get("blobs", [])
                if blobs:
                    # Example: https://storeid.public.blob.vercel-storage.com/folder/file.json
                    sample_url = blobs[0].get("url")
                    if sample_url:
                        # Extract https://storeid.public.blob.vercel-storage.com
                        parts = sample_url.split("/")
                        # parts[0] = https:, parts[1] = '', parts[2] = domain
                        base = f"{parts[0]}//{parts[2]}"
                        print(f"   [Storage] Auto-discovery Success. Base URL: {base}")
                        return base
        except Exception as e:
            print(f"   [Storage] Auto-discovery failed: {e}")
        
        print("   [Storage] Could not determine Base URL. Reads will fail.")
        return ""

    def _list(self, prefix, limit=100):
        """
        List files in the blob store matching a prefix.
        """
        try:
            params = {"prefix": prefix, "limit": limit}
            resp = requests.get(self.api_url, headers=self._get_headers(), params=params)
            if resp.status_code == 200:
                return resp.json().get("blobs", [])
            else:
                print(f"   [Storage Error] List failed: {resp.text}")
        except Exception as e:
            print(f"   [Storage Exception] _list failed: {e}")
        return []

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

    def _delete(self, urls):
        """
        Deletes one or more blobs by their public URLs.
        Accepts a single URL string or a list of URL strings.
        Uses Vercel Blob API: POST /delete with JSON body.
        """
        if not urls: return
        if isinstance(urls, str):
            urls = [urls]
        urls = [u for u in urls if u]
        if not urls: return
        try:
            resp = requests.post(
                f"{self.api_url}/delete",
                headers={**self._get_headers(), "Content-Type": "application/json"},
                json={"urls": urls}
            )
            if resp.status_code == 200:
                pass
            else:
                print(f"   [Storage Warning] Delete failed ({resp.status_code}): {resp.text[:200]}")
        except Exception as e:
            print(f"   [Storage Exception] _delete failed: {e}")

    def _prune_old_versions(self, prefix, keep=3):
        """
        Keeps only the newest `keep` files for a given prefix.
        Deletes the rest in a single batch call. Self-healing: runs after every save.
        """
        blobs = self._list(prefix=prefix, limit=50)
        if len(blobs) <= keep:
            return
        blobs.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
        to_delete = blobs[keep:]
        urls = [b.get("url") for b in to_delete if b.get("url")]
        print(f"   [Storage] Pruning {len(urls)} old versions (keeping {keep}) for '{prefix}'...")
        self._delete(urls)

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
        Saves company data and keeps max 3 versions per company.
        """
        clean_name = self.sanitize_filename(company_name)
        prefix = f"companies/{clean_name}"

        # 1. Save NEW file
        filename = f"{prefix}.json"
        new_url = self._put(filename, full_data)

        # 2. Prune: keep only the 3 newest versions
        if new_url:
            self._prune_old_versions(prefix, keep=3)

        return new_url

    def get_company_data(self, company_name):
        """
        Retrieves company data by searching for the file prefix.
        This handles Vercel's random suffixes (e.g. nokia_oyj-AbCd.json).
        """
        clean_name = self.sanitize_filename(company_name)
        prefix = f"companies/{clean_name}"
        
        # 1. Search for the file via API (this avoids the suffix guessing game)
        blobs = self._list(prefix=prefix, limit=10)
        
        if not blobs:
            print(f"   [Storage] No file found for prefix: {prefix}")
            return None
            
        # 2. Sort by date desc to get latest
        blobs.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
        target_url = blobs[0].get("url")
        
        print(f"   [Storage] Found company file: {target_url}")
        
        # Self-healing: prune if too many versions accumulated
        if len(blobs) > 3:
            self._prune_old_versions(prefix, keep=3)

        return self._get(target_url)

    def save_global_index_direct(self, full_list):
        """Saves global index and keeps max 3 index versions."""
        new_url = self._put("index/summary_list.json", full_list)

        if new_url:
            self._prune_old_versions("index/summary_list", keep=3)

        return new_url

    def update_global_index(self, summary_entry):
        """
        Thread-safe index update. Uses a lock to prevent concurrent scans
        from clobbering each other's updates (read-modify-write race condition).
        """
        with BlobStorage._index_lock:
            print(f"   [Storage] Updating Global Index for {summary_entry['companyName']}...")

            # 1. Read current index (latest version)
            blobs = self._list("index/summary_list", limit=10)
            current_index = []
            if blobs:
                blobs.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
                latest_url = blobs[0].get("url")
                print(f"   [Storage] Reading latest index from: {latest_url}")
                data = self._get(latest_url)
                if data and isinstance(data, list):
                    current_index = data

            # 2. Upsert company in the list
            found = False
            for i, item in enumerate(current_index):
                if item["companyName"] == summary_entry["companyName"]:
                    current_index[i] = summary_entry
                    found = True
                    break
            if not found:
                current_index.append(summary_entry)

            # 3. Save new index version
            print("   [Storage] Uploading new index version...")
            new_url = self._put("index/summary_list.json", current_index)

            # 4. Prune: keep only the 3 newest index files
            if new_url:
                self._prune_old_versions("index/summary_list", keep=3)

            return new_url

    def retrieve_latest_data(self, filename_prefix="index/summary_list"):
        """
        Robustly finds the latest index file by listing the directory,
        ignoring Vercel's random suffixes.
        """
        print(f"   [Storage] Finding latest data for prefix: {filename_prefix}")
        
        # 1. Use the LIST API to find matches
        blobs = self._list(filename_prefix, limit=20)
        
        if not blobs:
            print("   [Storage] No index files found via List API.")
            return []
            
        # 2. Sort by uploadedAt (descending) to get the newest
        blobs.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
        
        latest_url = blobs[0].get("url")
        print(f"   [Storage] Latest Index found: {latest_url}")
        
        # 3. Fetch content
        return self._get(latest_url)
        
    def nuke_everything(self):
        """
        DANGEROUS: Deletes ALL files in the blob storage.
        Used to reset the database when scaling logic changes.
        """
        print("   [Storage] INITIATING COMPLETE DATABASE WIPE...")
        count = 0
        while True:
            blobs = self._list(prefix="", limit=500)
            if not blobs:
                break
            urls = [b['url'] for b in blobs if b.get('url')]
            self._delete(urls)
            count += len(urls)

        print(f"   [Storage] Wipe Complete. Deleted {count} files.")
        return count
