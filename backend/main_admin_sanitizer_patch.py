"""
main_admin_sanitizer_patch.py — Copy/paste helper for adding a database sanitation endpoint to main.py.

This file is intentionally not auto-injected into main.py. It gives you a small,
reviewable FastAPI endpoint block. Paste the import and endpoint into main.py if
you want UI/API-triggered sanitation instead of running database_sanitizer.py.
"""

# Add near imports in main.py:
# from database_sanitizer import sanitize_database

# Add near other admin endpoints in main.py:
"""
@app.post("/admin/sanitize-foresight-records")
async def sanitize_foresight_records(request: Request, dry_run: bool = True, write: bool = False, post_clean: bool = False, company: str | None = None):
    verify_admin(request)
    report = sanitize_database(
        company=company,
        dry_run=dry_run or not write,
        write=write,
        post_clean=post_clean,
    )
    return report
"""
