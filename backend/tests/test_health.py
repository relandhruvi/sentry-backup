from app.components import COMPONENTS


def test_health_reports_every_component(client):
    body = client.get("/health").json()

    assert set(body["components"]) == set(COMPONENTS)
    assert body["components"]["storage"]["status"] == "ok"
    assert body["components"]["llm"]["status"] == "disabled"
    assert body["components"]["voice"]["status"] == "unavailable"
    assert body["components"]["verify"]["status"] == "unconfigured"  # no TINYFISH_API_KEY in tests
    assert body["components"]["alerts"]["status"] == "ok"  # dashboard channel
    # STT/VAD/lexicon aren't implemented yet, so the core pipeline is degraded, not "ok".
    assert body["status"] == "degraded"
