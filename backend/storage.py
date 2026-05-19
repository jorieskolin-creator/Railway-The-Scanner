
import os
import requests
import json
import time
import re
import threading
from datetime import datetime
import urllib.parse

_DROPPED = object()  # sentinel: callable was dropped, distinct from legitimate None


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


    def _make_json_safe(self, obj, path="root"):
        """Return a JSON-serializable copy of obj; drop callables/functions.

        Returns _DROPPED sentinel (not None) when a callable is removed, so that
        callers can distinguish "was a callable" from "legitimate None value".
        """
        if callable(obj):
            print(f" [Storage JSON-Safe] Dropped callable/function at {path}")
            return _DROPPED
        if isinstance(obj, dict):
            cleaned = {}
            for key, value in obj.items():
                safe_value = self._make_json_safe(value, f"{path}.{key}")
                if safe_value is not _DROPPED:
                    cleaned[key] = safe_value
            return cleaned
        if isinstance(obj, (list, tuple)):
            # Tuples are converted to lists — they serialize identically in JSON
            cleaned = []
            for idx, value in enumerate(obj):
                safe_value = self._make_json_safe(value, f"{path}[{idx}]")
                if safe_value is not _DROPPED:
                    cleaned.append(safe_value)
            return cleaned
        try:
            json.dumps(obj)
            return obj
        except TypeError:
            print(f" [Storage JSON-Safe] Converted non-serializable object at {path}: {type(obj).__name__}")
            return str(obj)

    def _put(self, pathname, data):
        try:
            safe_data = self._make_json_safe(data, pathname)
            response = requests.put(
                f"{self.api_url}/{pathname}",
                headers=self._get_headers(),
                data=json.dumps(safe_data),
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

    def save_company_data(self, company_name, full_data, skip_if_newer_than=None, post_clean=False):
        """
        Saves company data and prunes old versions.

        Normal save: keeps max 3 versions per company.
        Post-clean save (post_clean=True): keeps ONLY the cleaned version.
        This ensures false data is truly gone from the database after cleaning.

        If skip_if_newer_than (ISO timestamp string) is provided, checks whether
        a version uploaded after that timestamp already exists — if so, skips the
        save to avoid overwriting a fresher manual scan during batch processing.
        """
        clean_name = self.sanitize_filename(company_name)
        prefix = f"companies/{clean_name}"

        # Timestamp-based conflict resolution
        if skip_if_newer_than:
            blobs = self._list(prefix=prefix, limit=5)
            if blobs:
                blobs.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
                latest_uploaded = blobs[0].get("uploadedAt", "")
                if latest_uploaded and latest_uploaded > skip_if_newer_than:
                    print(f"   [Storage] Conflict: existing data ({latest_uploaded}) is newer than batch start ({skip_if_newer_than}). Skipping save for '{company_name}'.")
                    return None

        # 1. Save NEW file
        filename = f"{prefix}.json"
        new_url = self._put(filename, full_data)

        # 2. Prune old versions
        if new_url:
            if post_clean:
                # STRICT CLEANING RULE: After a successful clean, delete ALL old versions.
                # Only the freshly cleaned file remains. False data is truly gone.
                self._prune_old_versions(prefix, keep=1)
                print(f"   [Storage] 🧹 Post-clean prune: only cleaned version kept for '{company_name}'")
            else:
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

            # 2. Upsert company in the list (case-insensitive to avoid duplicates like HIAB/Hiab)
            found = False
            for i, item in enumerate(current_index):
                if item["companyName"].lower() == summary_entry["companyName"].lower():
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

    def update_global_index_field(self, company_name, field_name, field_value):
        """Update a single field for a company in the global index."""
        return self.update_global_index_fields(company_name, {field_name: field_value})

    def update_global_index_fields(self, company_name, fields: dict):
        """Update multiple fields for a company in the global index (single atomic read-modify-write)."""
        with BlobStorage._index_lock:
            blobs = self._list("index/summary_list", limit=10)
            current_index = []
            if blobs:
                blobs.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
                latest_url = blobs[0].get("url")
                data = self._get(latest_url)
                if data and isinstance(data, list):
                    current_index = data

            updated = False
            for item in current_index:
                if item.get("companyName", "").lower() == company_name.lower():
                    item.update(fields)
                    updated = True
                    break

            if updated:
                new_url = self._put("index/summary_list.json", current_index)
                if new_url:
                    self._prune_old_versions("index/summary_list", keep=3)
                return new_url
            return None

    def remove_duplicate_from_index(self, company_name):
        """Remove duplicate entries for a company from the global index, keeping the most recent one."""
        with BlobStorage._index_lock:
            blobs = self._list("index/summary_list", limit=10)
            if not blobs:
                return 0
            blobs.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
            latest_url = blobs[0].get("url")
            data = self._get(latest_url)
            if not data or not isinstance(data, list):
                return 0

            # Find all entries for this company
            matches = [i for i, item in enumerate(data) if item.get("companyName", "").lower() == company_name.lower()]
            if len(matches) <= 1:
                return 0  # No duplicates

            # Keep the first match (most likely the one with best data), remove the rest
            to_remove = set(matches[1:])
            cleaned = [item for i, item in enumerate(data) if i not in to_remove]
            removed_count = len(to_remove)

            self._put("index/summary_list.json", cleaned)
            self._prune_old_versions("index/summary_list", keep=3)
            print(f"   [Storage] Removed {removed_count} duplicate(s) of '{company_name}' from global index")
            return removed_count

    def cleanup_orphaned_index_entries(self):
        """Remove entries from the summary index where company data files no longer exist."""
        with BlobStorage._index_lock:
            blobs = self._list("index/summary_list", limit=10)
            if not blobs:
                return []
            blobs.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
            latest_url = blobs[0].get("url")
            data = self._get(latest_url)
            if not data or not isinstance(data, list):
                return []

            # Get all existing company file prefixes
            company_blobs = self._list(prefix="companies/", limit=500) or []
            existing_keys = set()
            for b in company_blobs:
                pathname = b.get("pathname", "")
                key = pathname.replace("companies/", "").split(".")[0]
                if key:
                    existing_keys.add(key)

            # Find orphaned entries
            orphaned = []
            cleaned = []
            for item in data:
                name = item.get("companyName", "")
                clean_name = self.sanitize_filename(name)
                if clean_name in existing_keys:
                    cleaned.append(item)
                else:
                    orphaned.append(name)

            if orphaned:
                self._put("index/summary_list.json", cleaned)
                self._prune_old_versions("index/summary_list", keep=3)
                print(f"   [Storage] Removed {len(orphaned)} orphaned entries from index: {', '.join(orphaned)}")

            return orphaned

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
        
    def delete_company(self, company_name):
        """
        Deletes a single company's data from blob storage AND removes it from the global index.
        Returns dict with deletion counts.
        """
        clean_name = self.sanitize_filename(company_name)
        prefix = f"companies/{clean_name}"
        deleted_files = 0

        # 1. Delete all company file versions
        blobs = self._list(prefix=prefix, limit=50)
        if blobs:
            urls = [b.get("url") for b in blobs if b.get("url")]
            if urls:
                self._delete(urls)
                deleted_files = len(urls)
                print(f"   [Storage] Deleted {deleted_files} file(s) for '{company_name}'")

        # 2. Remove from global index
        removed_from_index = False
        with BlobStorage._index_lock:
            blobs = self._list("index/summary_list", limit=10)
            if blobs:
                blobs.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
                latest_url = blobs[0].get("url")
                data = self._get(latest_url)
                if data and isinstance(data, list):
                    original_len = len(data)
                    data = [item for item in data if item.get("companyName", "").lower() != company_name.lower()]
                    if len(data) < original_len:
                        removed_from_index = True
                        self._put("index/summary_list.json", data)
                        self._prune_old_versions("index/summary_list", keep=3)
                        print(f"   [Storage] Removed '{company_name}' from global index")

        return {"deleted_files": deleted_files, "removed_from_index": removed_from_index}

    def get_all_companies_summary(self):
        """
        Returns a list of all stored companies with metadata for admin review.
        Scans the companies/ prefix and returns name, last updated, file count, and data size.
        """
        blobs = self._list(prefix="companies/", limit=500)
        if not blobs:
            return []

        # Group blobs by company prefix
        companies = {}
        for blob in blobs:
            pathname = blob.get("pathname", "")
            # Extract company name from path like "companies/nokia_oyj.json"
            parts = pathname.replace("companies/", "").split(".")
            if parts:
                company_key = parts[0]
                if company_key not in companies:
                    companies[company_key] = []
                companies[company_key].append(blob)

        result = []
        for company_key, company_blobs in companies.items():
            company_blobs.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
            latest = company_blobs[0]
            # Fetch latest data for preview
            latest_data = self._get(latest.get("url"))
            preview = {}
            if latest_data and isinstance(latest_data, dict):
                ai_v = latest_data.get("ai_verification", {})
                preview = {
                    "companyName": latest_data.get("companyName", company_key),
                    "lastUpdated": latest_data.get("lastUpdated"),
                    "summary_preview": (latest_data.get("summary_text", "") or "")[:300],
                    "intent_score": latest_data.get("intent_score"),
                    "reality_score": latest_data.get("reality_score"),
                    "confidence_score": latest_data.get("confidence_score"),
                    "archetype": latest_data.get("archetype"),
                    "hallucination_flags": latest_data.get("hallucination_flags", []),
                    "hallucination_risk": latest_data.get("hallucination_risk", "unknown"),
                    "ai_verified": ai_v.get("verified") if ai_v else None,
                    "unsupported_claims": (ai_v.get("false_claims") or ai_v.get("unsupported_claims", [])) if ai_v else [],
                    "versions": len(company_blobs),
                    "uploaded_at": latest.get("uploadedAt"),
                }
            else:
                preview = {
                    "companyName": company_key,
                    "lastUpdated": None,
                    "summary_preview": "(could not read data)",
                    "versions": len(company_blobs),
                    "uploaded_at": latest.get("uploadedAt"),
                }
            result.append(preview)

        result.sort(key=lambda x: x.get("companyName", "").lower())
        return result

    # --- FORESIGHT KNOWLEDGE BASE ---

    def save_foresight_kb(self, theme_slug: str, signals_data: dict):
        """
        Saves validated foresight signals for a single FP theme.
        Stored under foresight_kb/{theme_slug}.json with max 2 versions.
        """
        prefix = f"foresight_kb/{theme_slug}"
        filename = f"{prefix}.json"
        new_url = self._put(filename, signals_data)
        if new_url:
            self._prune_old_versions(prefix, keep=2)
        return new_url

    def get_foresight_kb(self, theme_slug: str):
        """Retrieves the latest validated foresight signals for a single FP theme."""
        prefix = f"foresight_kb/{theme_slug}"
        blobs = self._list(prefix=prefix, limit=5)
        if not blobs:
            return None
        blobs.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
        return self._get(blobs[0].get("url"))

    def get_all_foresight_kb(self):
        """Retrieves all foresight KB themes. Returns dict {theme_slug: signals_data}."""
        blobs = self._list(prefix="foresight_kb/", limit=100)
        if not blobs:
            return {}
        # Group by theme (strip foresight_kb/ prefix and .json suffix)
        themes = {}
        for blob in blobs:
            pathname = blob.get("pathname", "")
            slug = pathname.replace("foresight_kb/", "").split(".")[0]
            if slug not in themes:
                themes[slug] = blob
            elif blob.get("uploadedAt", "") > themes[slug].get("uploadedAt", ""):
                themes[slug] = blob
        result = {}
        for slug, blob in themes.items():
            data = self._get(blob.get("url"))
            if data:
                result[slug] = data
        return result

    def get_foresight_kb_status(self):
        """Returns metadata about the foresight KB (theme count, last updated, signal counts)."""
        blobs = self._list(prefix="foresight_kb/", limit=100)
        if not blobs:
            return {"theme_count": 0, "total_signals": 0, "last_updated": None, "themes": []}
        # Group by theme, find latest per theme
        themes = {}
        for blob in blobs:
            pathname = blob.get("pathname", "")
            slug = pathname.replace("foresight_kb/", "").split(".")[0]
            uploaded = blob.get("uploadedAt", "")
            if slug not in themes or uploaded > themes[slug]["uploadedAt"]:
                themes[slug] = {"slug": slug, "uploadedAt": uploaded, "url": blob.get("url")}
        theme_list = sorted(themes.values(), key=lambda x: x["slug"])
        last_updated = max(t["uploadedAt"] for t in theme_list) if theme_list else None
        # Count signals per theme
        total_signals = 0
        theme_summaries = []
        for t in theme_list:
            data = self._get(t["url"])
            n = len(data.get("signals", [])) if data else 0
            total_signals += n
            theme_summaries.append({"theme": t["slug"], "signals": n, "updated": t["uploadedAt"]})
        return {
            "theme_count": len(theme_list),
            "total_signals": total_signals,
            "last_updated": last_updated,
            "themes": theme_summaries,
        }

    # --- FORESIGHT OBSERVATORY ---

    def save_observatory(self, data: dict):
        """Saves observatory analysis. Overwrites previous version (single global file)."""
        prefix = "observatory/latest"
        filename = f"{prefix}.json"
        new_url = self._put(filename, data)
        if new_url:
            self._prune_old_versions(prefix, keep=2)
        return new_url

    def get_observatory(self):
        """Retrieves the latest observatory analysis."""
        prefix = "observatory/latest"
        blobs = self._list(prefix=prefix, limit=5)
        if not blobs:
            return None
        blobs.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
        return self._get(blobs[0].get("url"))

    def save_cross_impact_interpretation(self, data: dict):
        """Saves the Cross-Impact Matrix Interpretation. Independent of observatory blob."""
        prefix = "market_intelligence/cross_impact_interpretation"
        filename = f"{prefix}.json"
        new_url = self._put(filename, data)
        if new_url:
            self._prune_old_versions(prefix, keep=2)
        return new_url

    def get_cross_impact_interpretation(self):
        """Retrieves the latest Cross-Impact Matrix Interpretation, or None."""
        prefix = "market_intelligence/cross_impact_interpretation"
        blobs = self._list(prefix=prefix, limit=5)
        if not blobs:
            return None
        blobs.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
        return self._get(blobs[0].get("url"))

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
