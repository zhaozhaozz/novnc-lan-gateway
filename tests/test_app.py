import base64
import socketserver
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.main as main


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", main.TargetStore(tmp_path / "targets.json"))
    monkeypatch.setattr(main.settings, "app_username", "")
    monkeypatch.setattr(main.settings, "app_password", "")
    with TestClient(main.app) as test_client:
        yield test_client


def create_target(client: TestClient, **overrides):
    payload = {
        "name": "test host",
        "host": "127.0.0.1",
        "port": 5900,
        "description": "local test",
    }
    payload.update(overrides)
    response = client.post("/api/targets", json=payload)
    assert response.status_code == 201
    return response.json()


def test_target_crud(client: TestClient):
    assert client.get("/api/targets").json() == []

    target = create_target(client, name="alpha", host="192.168.1.12")
    assert target["name"] == "alpha"
    assert target["host"] == "192.168.1.12"

    update = client.put(
        f"/api/targets/{target['id']}",
        json={"description": "updated description"},
    )
    assert update.status_code == 200
    assert update.json()["description"] == "updated description"

    redirect = client.get(f"/viewer/{target['id']}", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"].startswith("/novnc/vnc.html")
    assert f"path=%2Fapi%2Fvnc%2F{target['id']}" in redirect.headers["location"]

    delete = client.delete(f"/api/targets/{target['id']}")
    assert delete.status_code == 204
    assert client.get("/api/targets").json() == []


def test_password_is_saved_but_not_returned(client: TestClient):
    target = create_target(client, password="secret pass")
    assert target["has_password"] is True
    assert "password" not in target

    targets = client.get("/api/targets").json()
    assert targets[0]["has_password"] is True
    assert "password" not in targets[0]

    redirect = client.get(f"/viewer/{target['id']}", follow_redirects=False)
    assert redirect.status_code == 307
    assert f"path=%2Fapi%2Fvnc%2F{target['id']}" in redirect.headers["location"]
    assert redirect.headers["location"].endswith("#password=secret%20pass")

    update = client.put(f"/api/targets/{target['id']}", json={"description": "keep password"})
    assert update.status_code == 200
    assert update.json()["has_password"] is True

    clear = client.put(f"/api/targets/{target['id']}", json={"password": ""})
    assert clear.status_code == 200
    assert clear.json()["has_password"] is False


def test_rejects_invalid_target(client: TestClient):
    response = client.post("/api/targets", json={"name": "bad", "host": "bad host", "port": 5900})
    assert response.status_code == 422


def test_probe_reachable_target(client: TestClient):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(1)

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        port = server.server_address[1]
        target = create_target(client, port=port)
        response = client.get(f"/api/targets/{target['id']}/probe")
        assert response.status_code == 200
        assert response.json()["ok"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_config_reports_auth_disabled(client: TestClient):
    assert client.get("/api/config").json() == {"auth_enabled": False}


def test_config_reports_auth_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", main.TargetStore(tmp_path / "targets.json"))
    monkeypatch.setattr(main.settings, "app_username", "admin")
    monkeypatch.setattr(main.settings, "app_password", "secret")

    with TestClient(main.app) as test_client:
        token = base64.b64encode(b"admin:secret").decode("ascii")
        response = test_client.get("/api/config", headers={"Authorization": f"Basic {token}"})
        assert response.json() == {"auth_enabled": True}


def test_websocket_proxy_relays_bytes(client: TestClient):
    received = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            received.append(self.request.recv(1024))
            self.request.sendall(b"server-hello")
            # Keep the connection open until the proxy closes it from its side.
            self.request.recv(1024)

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        target = create_target(client, port=server.server_address[1])
        with client.websocket_connect(f"/api/vnc/{target['id']}") as ws:
            ws.send_bytes(b"client-hello")
            assert ws.receive_bytes() == b"server-hello"
    finally:
        server.shutdown()
        server.server_close()

    assert received == [b"client-hello"]


def test_websocket_adhoc_relays_bytes(client: TestClient):
    received = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            received.append(self.request.recv(1024))
            self.request.sendall(b"server-hello")
            self.request.recv(1024)

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        port = server.server_address[1]
        # No target is created: host/port come straight from the query string.
        with client.websocket_connect(f"/api/vnc?host=127.0.0.1&port={port}") as ws:
            ws.send_bytes(b"client-hello")
            assert ws.receive_bytes() == b"server-hello"
    finally:
        server.shutdown()
        server.server_close()

    assert received == [b"client-hello"]
    assert client.get("/api/targets").json() == []  # nothing was persisted


def test_websocket_adhoc_rejects_bad_port(client: TestClient):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/vnc?host=127.0.0.1&port=not-a-port") as ws:
            ws.receive_bytes()


def test_websocket_rejected_without_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", main.TargetStore(tmp_path / "targets.json"))
    monkeypatch.setattr(main.settings, "app_username", "admin")
    monkeypatch.setattr(main.settings, "app_password", "secret")

    with TestClient(main.app) as test_client:
        token = base64.b64encode(b"admin:secret").decode("ascii")
        auth = {"Authorization": f"Basic {token}"}
        response = test_client.post(
            "/api/targets",
            json={"name": "t", "host": "127.0.0.1", "port": 5900},
            headers=auth,
        )
        target_id = response.json()["id"]

        with pytest.raises(WebSocketDisconnect):
            with test_client.websocket_connect(f"/api/vnc/{target_id}"):
                pass


def test_basic_auth_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", main.TargetStore(tmp_path / "targets.json"))
    monkeypatch.setattr(main.settings, "app_username", "admin")
    monkeypatch.setattr(main.settings, "app_password", "secret")

    with TestClient(main.app) as test_client:
        assert test_client.get("/health").status_code == 200
        assert test_client.get("/api/targets").status_code == 401

        token = base64.b64encode(b"admin:secret").decode("ascii")
        response = test_client.get("/api/targets", headers={"Authorization": f"Basic {token}"})
        assert response.status_code == 200
        assert response.json() == []
