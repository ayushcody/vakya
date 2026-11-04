"""The phone-only public app (phone_public.py): only the stream, the answer document, the status callback and the missed-call
hook exist on it; everything the tunnel used to expose answers 404. The main app keeps serving the same routes on the
loopback port."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core.runtime import runtime as backend
import phone_public
from routers import phone as px


@pytest.fixture()
def public(monkeypatch):
    monkeypatch.setenv("VAKYA_SESSION_SECRET", "test-secret")
    return TestClient(phone_public.public_app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", [
    "/api/voices", "/api/agents", "/api/generate", "/api/keys", "/api/health", "/docs", "/openapi.json",
    "/api/phone/numbers", "/api/phone/calls", "/api/phone/status", "/api/phone/live",
])
def test_everything_but_the_phone_routes_is_absent(public, path):
    assert public.get(path).status_code == 404, path


def test_the_call_endpoint_is_absent_too(public):
    r = public.post("/api/phone/call", json={"agent_id": "a", "to": "+919876543210", "caller_agreed": True})
    assert r.status_code == 404


def test_health_probe_names_only_the_public_routes(public):
    r = public.get("/api/phone/public/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["routes"] == sorted([px.STREAM_PATH + "/{token}", px.ANSWER_PATH + "/{token}",
                                     px.STATUS_CALLBACK_PATH + "/{token}", px.MISSED_CALL_PATH + "/{token}"])


def test_status_callback_exists_and_ignores_a_bad_token(public):
    r = public.post(px.STATUS_CALLBACK_PATH + "/not-a-token", data={"CallUUID": "x", "CallStatus": "completed"})
    assert r.status_code == 200 and r.json() == {"ok": False}


def test_stream_exists_and_refuses_a_bad_token(public):
    with pytest.raises(WebSocketDisconnect) as info:
        with public.websocket_connect(px.STREAM_PATH + "/not-a-token"):
            pass
    assert info.value.code == 4401


def test_main_app_still_serves_the_public_routes(app_client, monkeypatch):
    monkeypatch.setenv("VAKYA_SESSION_SECRET", "test-secret")
    with pytest.raises(WebSocketDisconnect) as info:   # the route exists: it is the token check that refuses
        with app_client.websocket_connect(px.STREAM_PATH + "/not-a-token"):
            pass
    assert info.value.code == 4401
    r = app_client.post(px.STATUS_CALLBACK_PATH + "/not-a-token", data={"CallUUID": "x"})
    assert r.status_code == 200 and r.json() == {"ok": False}
    # and the owner routes stay on the main app only (a request without a body is a 422, not a 404)
    assert app_client.post("/api/phone/call", headers={"storage-mode": "local"}).status_code == 422


def test_port_setting(monkeypatch):
    monkeypatch.delenv("PHONE_PUBLIC_PORT", raising=False)
    assert phone_public.port() == phone_public.DEFAULT_PORT
    monkeypatch.setenv("PHONE_PUBLIC_PORT", "0")
    assert phone_public.port() is None
    monkeypatch.setenv("PHONE_PUBLIC_PORT", "nope")
    assert phone_public.port() is None
    monkeypatch.setenv("PHONE_PUBLIC_PORT", "8021")
    assert phone_public.port() == 8021


@pytest.mark.asyncio
async def test_public_server_starts_on_a_free_port_and_stops(monkeypatch):
    import socket
    import httpx

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    monkeypatch.setenv("PHONE_PUBLIC_PORT", str(free))
    srv = phone_public.PublicServer()
    assert await srv.start() == free
    try:
        async with httpx.AsyncClient() as client:
            for _ in range(50):  # the server binds in the background; give it a moment
                try:
                    r = await client.get(f"http://127.0.0.1:{free}/api/phone/public/health")
                    break
                except httpx.ConnectError:
                    import asyncio
                    await asyncio.sleep(0.05)
            assert r.status_code == 200 and r.json()["ok"] is True
            assert (await client.get(f"http://127.0.0.1:{free}/api/voices")).status_code == 404
    finally:
        await srv.stop()
    assert not srv.running


def test_answer_and_missed_call_exist_on_the_public_app_and_refuse_a_bad_token(public):
    assert public.post(px.ANSWER_PATH + "/not-a-token").status_code == 404           # no XML for a token we did not sign
    r = public.post(px.MISSED_CALL_PATH + "/not-a-token", data={"CallUUID": "x", "From": "+919876500001"})
    assert r.status_code == 200 and "<Hangup" in r.text                              # the call is never answered
