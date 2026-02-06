
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

    def _delete(self, url):
        """
        Deletes a blob by its public URL.
        """
        if not url: return
        try:
            # Vercel Blob API Delete: DELETE /?url=...
            params = {"url": url}
            resp = requests.delete(self.api_url, headers=self._get_headers(), params=params)
            if resp.status_code == 200:
                # print(f"   [Storage] Cleaned up: {url}")
                pass
            else:
                print(f"   [Storage Warning] Delete failed for {url}: {resp.status_code}")
        except Exception as e:
            print(f"   [Storage Exception] _delete failed: {e}")

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
        Saves company data and deletes ANY previous versions of this company's file.
        """
        clean_name = self.sanitize_filename(company_name)
        prefix = f"companies/{clean_name}"
        
        # 1. Find old files BEFORE saving (so we know what to delete)
        old_blobs = self._list(prefix=prefix, limit=20)
        
        # 2. Save NEW file
        filename = f"{prefix}.json"
        new_url = self._put(filename, full_data)
        
        # 3. Delete OLD files
        if new_url and old_blobs:
            print(f"   [Storage] Housekeeping: Deleting {len(old_blobs)} old versions for {company_name}...")
            for b in old_blobs:
                # Safety check: Don't delete the file we just created if it appears in the list
                if b.get("url") != new_url:
                    self._delete(b.get("url"))
                    
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
        
        # Optional: Self-healing if too many duplicates exist
        if len(blobs) > 3:
             print(f"   [Storage] Note: Found {len(blobs)} duplicates for {company_name}. Cleanup will occur on next write.")

        return self._get(target_url)

    def save_global_index_direct(self, full_list):
        # When creating a new list directly, assume we want to clean up ANY old lists
        old_blobs = self._list("index/summary_list", limit=50)
        
        new_url = self._put("index/summary_list.json", full_list)
        
        # Cleanup
        if new_url and old_blobs:
            print(f"   [Storage] Index Housekeeping: Purging {len(old_blobs)} old index files...")
            for b in old_blobs:
                if b.get("url") != new_url:
                    self._delete(b.get("url"))
                
        return new_url

    def update_global_index(self, summary_entry):
        print(f"   [Storage] Updating Global Index for {summary_entry['companyName']}...")
        
        # 1. FIND ALL existing index files (to read AND delete later)
        blobs = self._list("index/summary_list", limit=50)
        
        # 2. READ the content of the most recent one
        current_index = []
        if blobs:
            blobs.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
            latest_url = blobs[0].get("url")
            print(f"   [Storage] Reading latest index from: {latest_url}")
            data = self._get(latest_url)
            if data and isinstance(data, list):
                current_index = data
        
        # 3. MODIFY the list in memory
        found = False
        for i, item in enumerate(current_index):
            if item["companyName"] == summary_entry["companyName"]:
                current_index[i] = summary_entry
                found = True
                break
        
        if not found:
            current_index.append(summary_entry)
            
        # 4. SAVE NEW file (Vercel will give it a new name like summary_list-XyZ.json)
        print("   [Storage] Uploading new index version...")
        new_url = self._put("index/summary_list.json", current_index)
        
        # 5. PURGE OLD files (Cleanup the "quite many files" issue)
        if new_url and blobs:
            print(f"   [Storage] Index Housekeeping: Purging {len(blobs)} obsolete index files...")
            for b in blobs:
                # Safety: Don't delete the file we just uploaded
                if b.get("url") != new_url:
                    self._delete(b.get("url"))
        
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
            # Fetch in batches
            blobs = self._list(prefix="", limit=500)
            if not blobs:
                break
                
            for b in blobs:
                self._delete(b['url'])
                count += 1
                
        print(f"   [Storage] Wipe Complete. Deleted {count} files.")
        return count
