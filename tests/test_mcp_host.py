"""Host-header behaviour of the streamable HTTP app.

The MCP SDK allowlists localhost when the bind address is loopback. A public
hostname must be accepted when the process binds a non-loopback address.
"""

from starlette.testclient import TestClient

from mcp_server.app import create_app

TOKEN = "test-token"
PUBLIC_BASE = "https://nathans-web-scraping-tool.onrender.com"


def _post(app, base_url: str):
    with TestClient(app, base_url=base_url) as client:
        return client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
        )


def test_loopback_bind_rejects_public_host():
    app = create_app(token=TOKEN, host="127.0.0.1")
    response = _post(app, PUBLIC_BASE)
    assert response.status_code == 421
    assert response.text == "Invalid Host header"


def test_public_bind_accepts_public_host():
    app = create_app(token=TOKEN, host="0.0.0.0")
    response = _post(app, PUBLIC_BASE)
    assert response.status_code != 421


def test_listen_port_uses_mcp_port(monkeypatch):
    from mcp_server.app import listen_port

    monkeypatch.setenv("MCP_PORT", "8000")
    monkeypatch.setenv("PORT", "10000")
    assert listen_port() == 8000


def test_listen_port_falls_back_to_platform_port(monkeypatch):
    from mcp_server.app import listen_port

    monkeypatch.setenv("MCP_PORT", "Render's PORT")
    monkeypatch.setenv("PORT", "10000")
    assert listen_port() == 10000


def test_listen_port_defaults_locally(monkeypatch):
    from mcp_server.app import listen_port

    monkeypatch.delenv("MCP_PORT", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    assert listen_port() == 8000
