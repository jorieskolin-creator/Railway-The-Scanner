"""
patch_summary.py — One-off utility to patch text in a company's stored data.

Usage:
    python patch_summary.py "Vivicta" "end of 2025" "end of 2026"

Requires BLOB_READ_WRITE_TOKEN environment variable.
"""

import sys
from storage import BlobStorage


def patch_company_field(company_name: str, field: str, old_text: str, new_text: str):
    storage = BlobStorage()
    data = storage.get_company_data(company_name)

    if not data:
        print(f"ERROR: No data found for '{company_name}'")
        return False

    value = data.get(field)
    if not value or not isinstance(value, str):
        print(f"ERROR: Field '{field}' not found or not a string")
        return False

    if old_text not in value:
        print(f"WARNING: '{old_text}' not found in {field}. No changes made.")
        return False

    data[field] = value.replace(old_text, new_text)
    print(f"Replaced '{old_text}' → '{new_text}' in {field}")

    url = storage.save_company_data(company_name, data)
    if url:
        print(f"Saved successfully: {url}")
        return True
    else:
        print("ERROR: Save failed")
        return False


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python patch_summary.py <company_name> <old_text> <new_text>")
        print('Example: python patch_summary.py "Vivicta" "end of 2025" "end of 2026"')
        sys.exit(1)

    company = sys.argv[1]
    old = sys.argv[2]
    new = sys.argv[3]

    # Patch foresight_summary
    patch_company_field(company, "foresight_summary", old, new)
