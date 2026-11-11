"""Versioned, integrity-pinned assets for the embed and hosted chat."""

import base64
import hashlib
from functools import lru_cache
from html import escape
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from .channels import public_settings

router = APIRouter(include_in_schema=False)
ROOT = Path(__file__).resolve().parents[2]
VERSION = "1.0.0"
ASSET_PREFIX = f"/widget-assets/{VERSION}"


@lru_cache
def asset(name: str) -> bytes:
    return (ROOT / "widget" / name).read_bytes()


def embed_details(origin: str | None, site_key: str):
    if not origin:
        return {"script_url": None, "integrity": None, "hosted_url": None}
    checksum = base64.b64encode(hashlib.sha384(asset("widget.js")).digest()).decode()
    return {
        "script_url": f"{origin}{ASSET_PREFIX}/widget.js",
        "integrity": f"sha384-{checksum}",
        "hosted_url": f"{origin}/c/{site_key}",
    }


def script_tag(origin: str, site_key: str, hosted: bool = False) -> str:
    details = embed_details(origin, site_key)
    mode = ' data-mode="hosted"' if hosted else ""
    return (
        f'<script src="{escape(details["script_url"], quote=True)}" data-site-key="{escape(site_key, quote=True)}"'
        f' integrity="{details["integrity"]}" crossorigin="anonymous"{mode} async></script>'
    )


@router.get(ASSET_PREFIX + "/{name}")
def widget_asset(name: str) -> Response:
    types = {"widget.js": "application/javascript", "widget.css": "text/css"}
    if name not in types:
        raise HTTPException(404)
    return Response(asset(name), media_type=types[name], headers={
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
