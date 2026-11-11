"""Serve the built admin dashboard (frontend/dist) from the API's own origin.

Same-origin serving needs no CORS and keeps the session cookies first-party. Routes owned by the API
(`/api/...`, `/c/...`, `/demo/`, `/widget-assets/...`) are registered earlier and never fall back to the
dashboard; an unknown `/api/...` path is still a 404, not the dashboard page.
"""

from os import environ
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[2]
RESERVED = ("api/", "c/", "demo/", "widget-assets/")
DASHBOARD_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; "
    "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)
PAGE_HEADERS = {
    "Content-Security-Policy": DASHBOARD_CSP,
    "Cache-Control": "no-cache",
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}


def dashboard_dist() -> Path:
    return Path(environ.get("SOURCED_WEB_DIST", ROOT / "frontend" / "dist")).resolve()


def mount_dashboard(app: FastAPI) -> bool:
    """Serve the dashboard if it has been built. Returns whether it was mounted."""
    dist = dashboard_dist()
    index = dist / "index.html"
    if not index.is_file():
        return False
    if (dist / "assets").is_dir():
        # Vite fingerprints these file names, so they can be cached for a long time.
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="dashboard-assets")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def dashboard(path: str) -> FileResponse:
        if path.startswith(RESERVED) or path in {"api", "c", "demo", "widget-assets"}:
            raise HTTPException(404)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist in candidate.parents:
            return FileResponse(candidate, headers={"X-Content-Type-Options": "nosniff"})
        # Every other path is a dashboard route handled by the React router.
        return FileResponse(index, headers=PAGE_HEADERS)

    return True
