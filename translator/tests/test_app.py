"""Tests with the Claude call mocked out (no network, no API key needed)."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from translator import translate as tr
from translator.app import app


def fake_response(payload, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=None,
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
    )


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def test_translate_parses_three_languages():
    payload = {"zh": "你好", "en": "Hello", "ja": "こんにちは"}
    with patch.object(tr, "get_client") as gc:
        gc.return_value.messages.create.return_value = fake_response(payload)
        result = tr.translate("안녕하세요")
    assert result.as_dict() == payload
    kwargs = gc.return_value.messages.create.call_args.kwargs
    assert kwargs["messages"] == [{"role": "user", "content": "안녕하세요"}]
    assert kwargs["output_config"]["format"]["type"] == "json_schema"


def test_translate_rejects_empty():
    with pytest.raises(ValueError):
        tr.translate("   ")


def test_translate_refusal_raises():
    with patch.object(tr, "get_client") as gc:
        gc.return_value.messages.create.return_value = fake_response({}, stop_reason="refusal")
        with pytest.raises(RuntimeError):
            tr.translate("테스트")


def test_api_ok(client):
    payload = {"zh": "你好", "en": "Hello", "ja": "こんにちは"}
    with patch("translator.app.translate", return_value=tr.Translation(**payload)):
        r = client.post("/api/translate", json={"text": "안녕하세요"})
    assert r.status_code == 200
    assert r.get_json() == payload


def test_api_empty_text(client):
    r = client.post("/api/translate", json={"text": ""})
    assert r.status_code == 400


def test_index_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "번역" in r.get_data(as_text=True)
