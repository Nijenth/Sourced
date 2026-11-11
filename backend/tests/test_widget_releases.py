import base64
import hashlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import widget_assets


def test_frozen_widget_releases_match_manifest_and_are_served():
    dist = widget_assets.DIST
    manifest = json.loads((dist / "releases.json").read_text(encoding="utf-8"))
    assert set(manifest) == {path.name for path in dist.iterdir() if path.is_dir()}
    app = FastAPI()
    app.include_router(widget_assets.router)
    with TestClient(app) as browser:
        for version, files in manifest.items():
            assert set(files) == {"widget.js", "widget.css"}
            assert set(files) == {path.name for path in (dist / version).iterdir()}
            for name, expected in files.items():
                data = (dist / version / name).read_bytes()
                digest = base64.b64encode(hashlib.sha384(data).digest()).decode()
                assert "sha384-" + digest == expected, f"Released asset changed in {version}/{name}"
                response = browser.get(f"/widget-assets/{version}/{name}")
                assert response.status_code == 200
                assert response.content == data
                assert "immutable" in response.headers["cache-control"]
        assert browser.get("/widget-assets/0.0.0/widget.js").status_code == 404
        assert browser.get("/widget-assets/1.0.0/releases.json").status_code == 404

    newest = max(manifest, key=lambda version: tuple(map(int, version.split("."))))
    snippet = widget_assets.embed_details("https://chat.example.com", "pk_example")
    assert snippet["script_url"].endswith(f"/{newest}/widget.js")
    assert snippet["integrity"] == manifest[newest]["widget.js"]
