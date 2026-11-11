"""Run browser checks with a disposable Sourced API and pgvector database."""

import argparse
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import ExitStack
from pathlib import Path

from backend.app.chat import SAMPLE_PDF
from backend.app.pdf_tasks import render_preview
from backend.tests.postgres import disposable_postgres

ROOT = Path(__file__).resolve().parents[2]


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", action="store_true")
    parser.add_argument("--widgets", action="store_true", help="Run the M4 widget and hosted-chat walkthrough.")
    args = parser.parse_args()
    with ExitStack() as stack:
        engine = stack.enter_context(disposable_postgres())
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        api_url = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "SOURCED_DATABASE_URL": engine.url.render_as_string(hide_password=False),
            "SOURCED_API_URL": api_url,
            "RAG_OFFLINE_MODE": "1",
            "TOP_K": "5",
            "RETRIEVAL_MODE": "hybrid",
            "MAX_UPLOAD_MB": "10",
            "MAX_UPLOAD_PAGES": "80",
            "SOURCED_E2E_LIVE": "1",
        }
        if args.widgets:
            from .widget_fixtures import seed_widget_demo

            env.update(
                SOURCED_PUBLIC_ORIGIN="https://127.0.0.1:4173",
                SOURCED_DASHBOARD_ORIGIN="https://127.0.0.1:4173",
                SOURCED_PREVIEW_SECRET=secrets.token_urlsafe(32),
                SOURCED_DEMO_SITE_KEY=seed_widget_demo(engine, api_url),
                SOURCED_WIDGET_REQUESTS_PER_MINUTE="3",
                SOURCED_IP_REQUESTS_PER_MINUTE="20",
                SOURCED_WORKSPACE_QUESTIONS_PER_DAY="100",
                SOURCED_E2E_WIDGETS="1",
                SOURCED_E2E_WIDGET_SCREENSHOTS="1" if args.screenshots else "0",
            )
        elif args.screenshots:
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            preview = directory / "preview.png"
            preview.write_bytes(render_preview(SAMPLE_PDF.read_bytes(), 4, "Lead, Principal and Director roles | 8 weeks"))
            env["SOURCED_PREVIEW_PNG"] = str(preview)

        api = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(port), "--no-access-log", "--no-proxy-headers"],
            cwd=ROOT, env=env,
        )
        stack.callback(stop, api)
        deadline = time.monotonic() + 20
        while True:
            try:
                with urllib.request.urlopen(api_url + "/api/v1/health", timeout=1) as response:
                    assert response.status == 200
                break
            except urllib.error.URLError:
                if api.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("The disposable Sourced API did not start.") from None
                time.sleep(0.2)
        worker = subprocess.Popen([sys.executable, "-m", "backend.app.worker"], cwd=ROOT, env=env)
        stack.callback(stop, worker)
        spec = "widget.spec.ts" if args.widgets else "keyboard.spec.ts"
        command = ["node", "node_modules/@playwright/test/cli.js", "test", "--workers=1", spec]
        if args.screenshots and not args.widgets:
            command.append("screenshots.spec.ts")
        result = subprocess.run(command, cwd=ROOT / "frontend", env=env, check=False)
        return result.returncode


if __name__ == "__main__":
    sys.exit(main())
