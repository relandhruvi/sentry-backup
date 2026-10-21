import pytest
from starlette.websockets import WebSocketDisconnect

from app.components import COMPONENTS


def test_session_lifecycle_streams_status_events(client):
    session_id = client.post("/sessions").json()["session_id"]

    with client.websocket_connect(f"/ws/events/{session_id}") as ws:
        first = ws.receive_json()
        assert first["type"] == "status"
        assert first["payload"] == {"component": "session", "status": "started", "detail": ""}
        assert first["seq"] == 1

        components = {ws.receive_json()["payload"]["component"] for _ in COMPONENTS}
        assert set(COMPONENTS) == components

        assert client.post(f"/sessions/{session_id}/end").json()["ended"] is True
        last = ws.receive_json()
        assert last["payload"]["status"] == "ended"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()

    assert client.post(f"/sessions/{session_id}/end").status_code == 404


def test_unknown_session_events_ws_is_rejected(client):
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws/events/nope") as ws,
    ):
        ws.receive_json()
    assert exc.value.code == 4404
