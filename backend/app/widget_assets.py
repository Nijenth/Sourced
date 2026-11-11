"""Versioned, integrity-pinned assets for the embed and hosted chat."""

import base64
import hashlib
import json
from functools import lru_cache
from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter(include_in_schema=False)
ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "widget" / "dist"
RELEASES = json.loads((DIST / "releases.json").read_text(encoding="utf-8"))
LATEST_VERSION = max(RELEASES, key=lambda version: tuple(map(int, version.split("."))))
ASSET_PREFIX = f"/widget-assets/{LATEST_VERSION}"
ASSET_TYPES = {"widget.js": "application/javascript", "widget.css": "text/css"}


@lru_cache
def asset(version: str, name: str) -> bytes:
    expected = RELEASES.get(version, {}).get(name)
    if expected is None or name not in ASSET_TYPES:
        raise HTTPException(404)
    try:
        data = (DIST / version / name).read_bytes()
    except OSError:
        raise HTTPException(503, "Widget release assets are unavailable.") from None
    checksum = base64.b64encode(hashlib.sha384(data).digest()).decode()
    if f"sha384-{checksum}" != expected:
        raise HTTPException(503, "Widget release assets are unavailable.")
    return data


def embed_details(origin: str | None, site_key: str, hosted_enabled: bool = False):
    if not origin:
        return {"script_url": None, "integrity": None, "hosted_url": None}
    return {
        "script_url": f"{origin}{ASSET_PREFIX}/widget.js",
        "integrity": RELEASES[LATEST_VERSION]["widget.js"],
        "hosted_url": f"{origin}/c/{site_key}" if hosted_enabled else None,
    }


def script_tag(origin: str, site_key: str, hosted: bool = False) -> str:
    details = embed_details(origin, site_key)
    mode = ' data-mode="hosted"' if hosted else ""
    return (
        f'<script src="{escape(details["script_url"], quote=True)}" data-site-key="{escape(site_key, quote=True)}"'
        f' integrity="{details["integrity"]}" crossorigin="anonymous"{mode} async></script>'
    )


@router.get("/widget-assets/{version}/{name}")
def widget_asset(version: str, name: str) -> Response:
    return Response(asset(version, name), media_type=ASSET_TYPES[name], headers={
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    })


def page_headers(origin: str, hosted: bool = False) -> dict[str, str]:
    sources = "'self'" if hosted else f"'self' {origin}"
    return {
        "Content-Security-Policy": (
            f"default-src 'none'; script-src {sources}; style-src {sources}; connect-src {sources}; "
            f"img-src {sources} blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        ),
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }
