"""Flask web app: type Korean, get Chinese / English / Japanese at once.

Run:
    export ANTHROPIC_API_KEY=sk-ant-...
    python -m translator.app
Then open http://localhost:5000
"""

from __future__ import annotations

import os

import anthropic
from flask import Flask, jsonify, render_template, request

from translator.translate import translate

app = Flask(__name__, template_folder="templates")
MAX_CHARS = 5000


@app.get("/")
def index():
    return render_template("index.html", max_chars=MAX_CHARS)


@app.post("/api/translate")
def api_translate():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return jsonify({"error": "번역할 텍스트를 입력해 주세요."}), 400
    if len(text) > MAX_CHARS:
        return jsonify({"error": f"텍스트가 너무 깁니다 (최대 {MAX_CHARS}자)."}), 400

    try:
        result = translate(text)
    except anthropic.AuthenticationError:
        return jsonify({"error": "ANTHROPIC_API_KEY가 없거나 잘못되었습니다."}), 500
    except anthropic.RateLimitError:
        return jsonify({"error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."}), 429
    except anthropic.APIStatusError as e:
        return jsonify({"error": f"API 오류 ({e.status_code}): {e.message}"}), 502
    except anthropic.APIConnectionError:
        return jsonify({"error": "Anthropic API에 연결할 수 없습니다."}), 502
    except (RuntimeError, ValueError) as e:
        return jsonify({"error": str(e)}), 422

    return jsonify(result.as_dict())


@app.get("/health")
def health():
    return jsonify({"ok": True, "api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY"))})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
