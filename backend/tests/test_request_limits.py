"""Security audit S01: oversized request bodies are rejected before any route reads them."""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.app.errors import CodedHTTPException, coded_error
from backend.app.limits import UPLOAD_PATH, BodySizeLimit

from .test_widgets import ORIGIN, ask_widget, client, create_widget, public_env  # noqa: F401 (fixture)
from .test_workspaces import API, QUESTION, signup

LIMIT, UPLOAD_LIMIT = 1000, 5000


class Echo(BaseModel):
    text: str


def tiny_app():
    app = FastAPI()
    calls = []

    @app.post("/echo")
    def echo(body: Echo):
        calls.append(len(body.text))
        return {"length": len(body.text)}

    @app.get("/echo")
    def get_echo():
        return {"ok": True}

    @app.post(UPLOAD_PATH)
    async def upload(request: Request):
        size = 0
        async for block in request.stream():
            size += len(block)
        calls.append(size)
        return {"size": size}

    app.add_exception_handler(CodedHTTPException, coded_error)
    app.add_middleware(BodySizeLimit, max_body=LIMIT, max_upload=UPLOAD_LIMIT)
    return TestClient(app), calls


def chunks(total, size=100):
    for _ in range(total // size):
        yield b"x" * size


def test_small_bodies_pass_and_reads_are_untouched():
    browser, calls = tiny_app()
    assert browser.post("/echo", json={"text": "hello"}).json() == {"length": 5}
    assert browser.get("/echo").json() == {"ok": True}
    assert calls == [5]


def test_a_declared_oversized_body_is_refused_unread():
    browser, calls = tiny_app()
    response = browser.post("/echo", json={"text": "x" * LIMIT})
    assert response.status_code == 413
    assert response.json() == {"detail": "The request is too large.", "code": "request_too_large"}
    assert calls == []


def test_a_body_without_a_declared_length_is_cut_off_at_the_limit():
    browser, calls = tiny_app()
    response = browser.post("/echo", content=chunks(LIMIT * 5), headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"
    assert calls == []


def test_the_upload_route_gets_the_upload_limit():
    browser, calls = tiny_app()
    assert browser.post(UPLOAD_PATH, content=b"x" * (LIMIT * 3)).json() == {"size": LIMIT * 3}
    assert browser.post(UPLOAD_PATH, content=b"x" * (UPLOAD_LIMIT + 1)).status_code == 413
    assert browser.post(UPLOAD_PATH, content=chunks(UPLOAD_LIMIT * 2)).status_code == 413
    # Only POST to that exact path gets the bigger allowance.
    assert browser.post("/echo", content=b"x" * (LIMIT * 3), headers={"Content-Type": "application/json"}).status_code == 413
    assert calls == [LIMIT * 3]


def test_public_chat_refuses_a_huge_body(public_env, sessions):
    with client() as browser:
        signup(browser, "alpha")
        widget = create_widget(browser)
        huge = {"question": QUESTION, "history": [{"role": "user", "content": "x" * 300_000}]}
        response = browser.post(API + "/public/chat", params={"site_key": widget["site_key"]}, headers={
            "X-Sourced-Key": widget["site_key"], "Origin": ORIGIN,
        }, json=huge)
        assert response.status_code == 413
        assert response.json()["code"] == "request_too_large"
        assert response.headers["X-Request-ID"]
        # A normal question still works.
        assert ask_widget(browser, widget["site_key"]).status_code == 200
