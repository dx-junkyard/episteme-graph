"""応答の要約（``HttpTrace.digest``）— 抜粋の 2000 字では届かない欄を審判が読むため。

学習チャットの回答は本文だけで 2000 字を超えることが多く、``sources`` は抜粋に入らない（第 7 周）。
runner が応答**全体**から決定論で次を取り出して trace に残す:

- ``numeric_keys``: 値が数（int / float。bool は除く）の欄の名前（重複なし・並び順固定）。文字列の中の
  数字は見ない（教材本文・引用の中の数は対象外）。``_`` で始まる欄は runner 自身の注記なので除く。
- ``chat``: ``answer`` を持つ応答の要約 — 本文の指紋（sha256 の先頭）・先頭の抜き書き・本文の
  ``[出典N]`` の番号・``degraded``・``sources`` の番号 / chunk_id / 表示題 / 区別に使える欄。

値（点数・重み）そのものは残さない（欄の名前だけ — PE7）。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterator, Optional

MARKER_RE = re.compile(r"\[出典\s*(\d+)\]")
# 出典チップを互いに区別できる欄（題以外）。どれかが出典ごとに違えば「区別できる」
DISTINGUISH_KEYS = ("meta", "section", "section_title", "section_id", "used_section", "page", "page_start",
                    "page_end", "pages", "chapter_title")
MAX_KEYS = 80
MAX_SOURCES = 24
HEAD_CHARS = 160
_NUMERIC_KEY_RE = re.compile(r'(?<!\\)"([A-Za-z_][A-Za-z0-9_]*)"\s*:\s*-?\d')
_ANSWER_RE = re.compile(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)("?)')


def _walk_numeric(v: Any, key: str = "") -> Iterator[str]:
    if isinstance(v, dict):
        for k, x in v.items():
            yield from _walk_numeric(x, str(k))
    elif isinstance(v, list):
        for x in v:
            yield from _walk_numeric(x, key)
    elif isinstance(v, (int, float)) and not isinstance(v, bool) and key and not key.startswith("_"):
        yield key


def numeric_keys(body: Any) -> list[str]:
    return sorted(set(_walk_numeric(body)))[:MAX_KEYS]


def numeric_keys_from_excerpt(excerpt: str) -> list[str]:
    """抜粋が JSON として読めないとき（途中で切れた）に、エスケープされていない ``"key": 数`` を拾う。"""
    return sorted({k for k in _NUMERIC_KEY_RE.findall(excerpt or "") if not k.startswith("_")})[:MAX_KEYS]


def answer_markers(text: str) -> list[int]:
    return sorted({int(n) for n in MARKER_RE.findall(text or "")})


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _source_row(pos: int, s: dict) -> dict:
    try:
        index = int(s.get("index") or 0)
    except (TypeError, ValueError):
        index = 0
    row: dict[str, Any] = {"index": index, "position": pos, "chunk_id": str(s.get("chunk_id") or ""),
                           "source_title": str(s.get("source_title") or s.get("title") or "")}
    for k in DISTINGUISH_KEYS:
        v = s.get(k)
        if v not in (None, "", [], {}):
            row[k] = v if isinstance(v, (str, int, float)) else json.dumps(v, ensure_ascii=False)[:120]
    return row


def chat_record(body: Any) -> Optional[dict]:
    """``answer`` を持つ応答の要約。持たなければ None。"""
    if not isinstance(body, dict) or not isinstance(body.get("answer"), str):
        return None
    answer = body["answer"]
    sources = [_source_row(i, s) for i, s in enumerate(body.get("sources") or [], 1) if isinstance(s, dict)]
    return {"answer_sha": _sha(answer), "answer_head": answer[:HEAD_CHARS], "answer_complete": True,
            "answer_markers": answer_markers(answer), "degraded": bool(body.get("degraded")),
            "sources": sources[:MAX_SOURCES], "sources_known": True}


def chat_record_from_excerpt(excerpt: str) -> Optional[dict]:
    """古い transcript（digest なし）の抜粋から要約を作る。切れていれば本文は途中まで・出典は不明。"""
    try:
        body = json.loads(excerpt)
    except (json.JSONDecodeError, TypeError):
        body = None
    if body is not None:
        return chat_record(body)
    m = _ANSWER_RE.search(excerpt or "")
    if not m:
        return None
    raw, closed = m.group(1), bool(m.group(2))
    try:
        answer = json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        answer = raw
    return {"answer_sha": _sha(answer) if closed else "", "answer_head": answer[:HEAD_CHARS],
            "answer_prefix": answer, "answer_complete": closed, "answer_markers": answer_markers(answer),
            "degraded": '"degraded": true' in excerpt, "sources": [], "sources_known": False}


def response_digest(body: Any) -> dict:
    out: dict[str, Any] = {}
    keys = numeric_keys(body)
    if keys:
        out["numeric_keys"] = keys
    chat = chat_record(body)
    if chat is not None:
        out["chat"] = chat
    return out
