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
                    "page_end", "pages", "chapter_title", "version_note")
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


_METRIC_DIFF_KEYS = ("relative_difference", "separation_ratio")


def _walk(v: Any) -> Iterator[dict]:
    if isinstance(v, dict):
        yield v
        for x in v.values():
            yield from _walk(x)
    elif isinstance(v, list):
        for x in v:
            yield from _walk(x)


def _by_symbol_numeric(by_symbol: Any) -> list[str]:
    """記号ごとの比較で、相対差・分離比が数で入っている記号の名前（値は残さない — PE7）。"""
    items = by_symbol.items() if isinstance(by_symbol, dict) else (
        [(str(e.get("symbol") or ""), e) for e in by_symbol if isinstance(e, dict)] if isinstance(by_symbol, list) else [])
    out = []
    for sym, entry in items:
        if isinstance(entry, dict) and any(isinstance(entry.get(k), (int, float)) and not isinstance(entry.get(k), bool)
                                           for k in _METRIC_DIFF_KEYS):
            out.append(str(sym))
    return out


def simulation_runs_record(body: Any) -> list[dict]:
    """SB層の実行記録（``program_id`` と ``status`` を持つ dict）ごとに、配線の有無と記号ごとの比較の数の有無。

    審判 B11（配線の無い実行に相対差が数で載る）が 2000 字の抜粋の外を読むため。数そのものは残さない。
    """
    out: list[dict] = []
    for d in _walk(body):
        if "program_id" in d and "status" in d and ("result" in d or "inputs" in d):
            result = d.get("result") if isinstance(d.get("result"), dict) else {}
            comparison = result.get("comparison") if isinstance(result.get("comparison"), dict) else {}
            metrics = comparison.get("metrics") if isinstance(comparison.get("metrics"), dict) else {}
            out.append({"id": str(d.get("id") or ""), "wired": bool(d.get("wiring_id")),
                        "by_symbol_numeric": _by_symbol_numeric(metrics.get("by_symbol"))[:MAX_KEYS]})
    return out[:MAX_SOURCES]


# ---------------------------------------------------------------------------
# 第 23 周: 非 LLM 応答の構造（審判 E7 の差分）と、わたしの記録の行（審判 E8）
# ---------------------------------------------------------------------------
# 学習者の非 LLM の GET のうち、同じコースなら学習者によらず同じであるはずの面（E7 が比べる）。
# 本人固有の欄（STRUCTURE_PERSONAL_KEYS）を落として比べる。
STRUCTURE_PATH_RES = (
    re.compile(r"^/api/learning/courses/[^/]+$"),
    re.compile(r"^/api/learning/courses/[^/]+/topics/[^/]+/material$"),
    re.compile(r"^/api/learning/courses/[^/]+/discuss/opening$"),
    re.compile(r"^/api/learning/courses/[^/]+/topics/[^/]+/reconstruction/next$"),
    re.compile(r"^/api/learning/courses/[^/]+/landscape$"),
)
# 本人の進み具合・本人の言葉・時刻（学習者ごとに違って当然の欄）。宛先（id 類）も比べない
# status_label は目次の状態の表示語で、本人の会話で学習中に変わる（第 23 周: E7 の誤検出源）
STRUCTURE_PERSONAL_KEYS = frozenset({
    "status", "status_label", "progress_pct", "progress", "intention", "enrolled", "notice", "learning_state",
    "current_topic_id",
    "personal_graph", "progress_data", "trace_id", "message_id", "revisit", "carryover", "leave_note",
})
_STRUCT_ADDRESS_RE = re.compile(r"(?:^|_)(?:id|ids|uuid)$|_at$|^(?:created|updated)$")
STRUCTURE_MAX_PATHS = 200
STRUCTURE_MAX_DEPTH = 4
STRUCTURE_MAX_LIST = 30


def _structure_walk(v: Any, path: str, depth: int, out: dict[str, str]) -> None:
    if len(out) >= STRUCTURE_MAX_PATHS:
        return
    if isinstance(v, dict) and depth < STRUCTURE_MAX_DEPTH:
        for k in sorted(v):
            if k in STRUCTURE_PERSONAL_KEYS or _STRUCT_ADDRESS_RE.search(str(k)):
                continue
            _structure_walk(v[k], f"{path}.{k}" if path else str(k), depth + 1, out)
    elif isinstance(v, list) and depth < STRUCTURE_MAX_DEPTH:
        out[f"{path}#len"] = str(len(v))
        for i, x in enumerate(v[:STRUCTURE_MAX_LIST]):
            _structure_walk(x, f"{path}[{i}]", depth + 1, out)
    else:
        if isinstance(v, (dict, list)):
            v = _strip_struct(v)
        out[path or "."] = _sha(json.dumps(v, ensure_ascii=False, sort_keys=True))[:8]


def _strip_struct(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _strip_struct(x) for k, x in v.items()
                if not (k in STRUCTURE_PERSONAL_KEYS or _STRUCT_ADDRESS_RE.search(str(k)))}
    if isinstance(v, list):
        return [_strip_struct(x) for x in v]
    return v


def structure_record(body: Any) -> dict[str, str]:
    """欄の道 → 値の指紋（先頭 8 字）。本人固有の欄・宛先・時刻を落とす。値そのものは残さない（PE7）。"""
    out: dict[str, str] = {}
    _structure_walk(body, "", 0, out)
    return out


def records_record(body: Any) -> list[dict]:
    """わたしの記録（GET /api/me/records）の行: id・種類・状態・地図に出していないか（本文は残さない）。"""
    out: list[dict] = []
    for sys_ in (body.get("systems") if isinstance(body, dict) else None) or []:
        for it in (sys_.get("items") if isinstance(sys_, dict) else None) or []:
            if isinstance(it, dict) and it.get("id"):
                flags = it.get("flags") if isinstance(it.get("flags"), dict) else {}
                out.append({"id": str(it["id"]), "kind": str(it.get("kind") or ""), "status": str(it.get("status") or ""),
                            "map_excluded": bool(flags.get("map_excluded"))})
    return out[:500]


def response_digest(body: Any, path: str = "") -> dict:
    out: dict[str, Any] = {}
    if path and isinstance(body, (dict, list)) and any(r.match(path) for r in STRUCTURE_PATH_RES):
        out["structure"] = structure_record(body)
    if path == "/api/me/records":
        rec = records_record(body)
        if rec:
            out["records"] = rec
    sim_runs = simulation_runs_record(body)
    if sim_runs:
        out["simulation_runs"] = sim_runs
    keys = numeric_keys(body)
    if keys:
        out["numeric_keys"] = keys
    chat = chat_record(body)
    if chat is not None:
        out["chat"] = chat
    return out
