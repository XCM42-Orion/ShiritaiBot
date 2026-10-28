import json

import httpx
import pytest
from fastapi.testclient import TestClient

from shiritaibot.web import create_app


@pytest.mark.parametrize("saved_key", [False, True])
def test_draft_model_test_uses_unsaved_form_without_persisting(tmp_path, saved_key):
    with TestClient(create_app(tmp_path, token="admin")) as client:
        body = {
            "provider": {
                "id": "draft",
                "api_key": "draft-test-key",
                "base_url": "https://fake.test/v1",
                "enabled": False,
                "max_retries": 0,
            },
            "model": {
                "id": "GLM-5.3-Flash",
                "provider": "draft",
                "model": "remote/GLM-5.3-Flash",
                "enabled": False,
                "extra_body": {"temperature": 0.4},
                "max_context": 64000,
                "modalities": ["text", "image"],
            },
        }
        assert client.post("/api/models/test", json=body).status_code == 401
        client.headers["Authorization"] = "Bearer admin"
        runtime = client.app.state.runtime
        if saved_key:
            client.portal.call(runtime.vault.set, "existing", "draft-test-key")
            body["provider"]["api_key"] = "secret:existing"
        original = client.get("/api/config").json()
        requests = []

        def handle(request):
            requests.append(request)
            assert str(request.url) == "https://fake.test/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer draft-test-key"
            assert b"remote/GLM-5.3-Flash" in request.content
            assert json.loads(request.content)["temperature"] == 0.4
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

        client.portal.call(runtime.llm.client.aclose)
        runtime.llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        response = client.post("/api/models/test", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["text"] == "OK"
        assert len(requests) == 1
        assert client.get("/api/config").json() == original
        assert "draft-test-key" not in client.get("/api/records/usage").text
        assert "draft-test-key" not in client.get("/api/records/activity").text
        assert "draft-test-key" not in (tmp_path / "config.toml").read_text("utf-8")
        missing = client.post("/api/models/missing/test")
        assert missing.status_code == 404
        assert "尚未保存或已改名" in missing.json()["detail"]
        body["provider"]["base_url"] = "bad-address"
        assert client.post("/api/models/test", json=body).status_code == 400
        assert len(requests) == 1
