"""OpenAI 互換の proxy — 製品側の LLM 呼び出しを Claude Code の頭脳（mailbox）へ回す。

砂場の api-server に ``OPENAI_BASE_URL=http://host.docker.internal:8090/v1`` を渡すと、製品コードを変えずに
chat.completions（テキスト / JSON モード / JSON Schema の構造化出力）がここへ届く。要求は mailbox に
kind="product" で置き、Claude Code の子エージェントが応える。埋め込み・音声は Claude では作れないので
本物の OpenAI へ素通しする（鍵は .env の LLM_API_KEY。残高が無ければ上流のエラーをそのまま返す）。
vision（image_url 付きメッセージ）は v1 未対応で 400 を返す（製品側は縮退する）。

起動: backend/.venv/bin/python uxsim/sandbox/claude_proxy.py --port 8090
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from uxsim.mailbox import Mailbox, MailboxError, MailboxTimeout, default_root  # noqa: E402

app = FastAPI(title="uxsim claude proxy")
BOX = Mailbox(default_root())
STATS = {"chat": 0, "embeddings": 0, "vision_rejected": 0, "errors": 0}


def _env(key: str) -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return ""


def _has_image(messages: list) -> bool:
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c):
            return True
    return False


def _schema_note(response_format) -> str:
    if not isinstance(response_format, dict):
        return ""
    if response_format.get("type") == "json_object":
        return "応答は JSON オブジェクトだけを返す（前後に文章を付けない）。"
    if response_format.get("type") == "json_schema":
        schema = (response_format.get("json_schema") or {}).get("schema") or {}
        return "応答は次の JSON Schema に**厳密に**従う JSON だけを返す（前後に文章を付けない）:\n" + json.dumps(schema, ensure_ascii=False)
    return ""


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages") or []
    if _has_image(messages):
        STATS["vision_rejected"] += 1
        return JSONResponse(status_code=400, content={"error": {"message": "vision is not supported by the uxsim claude proxy (v1)", "type": "invalid_request_error"}})
    if body.get("stream"):
        return JSONResponse(status_code=400, content={"error": {"message": "streaming is not supported by the uxsim claude proxy (v1)", "type": "invalid_request_error"}})
    STATS["chat"] += 1
    note = _schema_note(body.get("response_format"))
    try:
        data = BOX.ask("product", {"model": body.get("model"), "messages": messages,
                                   "response_format": body.get("response_format"), "note": note})
    except (MailboxTimeout, MailboxError) as exc:
        STATS["errors"] += 1
        return JSONResponse(status_code=502, content={"error": {"message": f"uxsim brain: {exc}", "type": "server_error"}})
    content = data.get("content")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return {
        "id": f"chatcmpl-uxsim-{uuid.uuid4().hex[:12]}", "object": "chat.completion", "created": int(time.time()),
        "model": str(body.get("model") or "claude-code-brain"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content, "refusal": None}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    STATS["embeddings"] += 1
    body = await request.json()
    key = _env("LLM_API_KEY") or _env("OPENAI_API_KEY")
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post("https://api.openai.com/v1/embeddings", json=body, headers={"Authorization": f"Bearer {key}"})
    return JSONResponse(status_code=r.status_code, content=r.json())


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "claude-code-brain", "object": "model"}]}


@app.get("/uxsim/stats")
async def stats():
    return STATS


if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8090)
    a = ap.parse_args()
    uvicorn.run(app, host="0.0.0.0", port=a.port, log_level="info")
