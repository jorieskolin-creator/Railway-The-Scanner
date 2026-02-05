import os
import requests
import json

class BlobStorage:
    def __init__(self):
        self.token = os.environ.get("BLOB_READ_WRITE_TOKEN")
        self.api_url = "https://blob.vercel-storage.com"

    def upload_json(self, filename: str, data: dict):
        """
        Uploads a JSON object to Vercel Blob.
        Note: Vercel Blob API usually requires a PUT to the upload URL.
        This is a simplified implementation wrapping the HTTP request.
        """
        if not self.token:
            print("Warning: BLOB_READ_WRITE_TOKEN not set. Skipping upload.")
            return None

        headers = {
            "authorization": f"Bearer {self.token}",
            "x-api-version": "1"
        }
        
        # 1. Put file (simplified for Vercel Blob basic upload)
        # In a real scenario, you might use the @vercel/blob JS SDK or the specific HTTP flow
        # defined in Vercel docs (PUT /pathname).
        
        response = requests.put(
            f"{self.api_url}/{filename}",
            headers=headers,
            data=json.dumps(data),
        )
        
        if response.status_code == 200:
            return response.json().get("url")
        else:
            print(f"Blob Upload Error: {response.text}")
            return None

    def get_json(self, url: str):
        response = requests.get(url)
        if response.status_code == 200:
            return response.json()
        return None
