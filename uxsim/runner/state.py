"""ペルソナ 1 人・1 セッションの状態と、応答の「画面の投影」。

投影は DTO をペルソナが読む日本語に整形したもの。**内部 ID は隠さない**（§7.3 — 隠すと
内部 ID が画面に漏れる欠陥が審判 B から見えなくなる）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

OBSERVATION_CHARS = 6000


@dataclass
class PersonaSession:
    """行為の path テンプレート（``{course_id}`` 等）を解決するための既知の状態。"""

    persona_id: str
    username: str = ""
    password: str = ""
    role: str = "STUDENT"
    session_no: int = 1
    token: str = ""
    user: dict = field(default_factory=dict)
    course_id: str = ""
    topic_id: str = ""
    topic_ids: list[str] = field(default_factory=list)
    material: dict = field(default_factory=dict)
    histories: dict[str, list[dict]] = field(default_factory=dict)  # UI が送るクライアント側の履歴（topic 別）
    topics: dict[str, dict] = field(default_factory=dict)  # 開いたコースのトピック（id → DTO）
    last_status: Optional[int] = None
    last_body: Any = None
    last_action: str = ""
    last_answer: str = ""
    course_draft: dict = field(default_factory=dict)
    known: dict[str, list[str]] = field(default_factory=dict)
    maps: dict[str, dict[str, str]] = field(default_factory=dict)  # 例: graph_sessions {document_id: session_id}
    scratch: dict[str, Any] = field(default_factory=dict)  # 直前の提案など、ID 以外の一時データ
    seq_counter: int = 0

    def history_for(self, topic_id: str) -> list[dict]:
        """トピックごとの会話履歴（UI の state.chatMessages 相当）。"""
        return self.histories.setdefault(topic_id or "", [])

    @property
    def chat_history(self) -> list[dict]:
        return self.history_for(self.topic_id)

    def remember(self, kind: str, value: Any) -> None:
        """既知 ID を末尾に積む（重複は末尾へ移す）。"""
        if value in (None, ""):
            return
        v = str(value)
        items = self.known.setdefault(kind, [])
        if v in items:
            items.remove(v)
        items.append(v)

    def latest(self, kind: str) -> str:
        items = self.known.get(kind) or []
        return items[-1] if items else ""

    def all(self, kind: str) -> list[str]:
        return list(self.known.get(kind) or [])

    def next_message_id(self) -> str:
        self.seq_counter += 1
        return f"uxsim-{self.persona_id}-{self.session_no}-{self.seq_counter}"

    def resolve(self, name: str, args: dict) -> str:
        """path の穴 ``name`` を引数 → 状態の順で埋める。埋まらなければ空文字。"""
        if args.get(name) not in (None, ""):
            return str(args[name])
        direct = {"course_id": self.course_id, "topic_id": self.topic_id}
        if direct.get(name):
            return direct[name]
        plural = {"material_id": "materials", "document_id": "documents", "component_id": "components",
                  "task_id": "tasks", "trace_id": "traces", "item_id": "recon_items", "recon_id": "recons",
                  "chunk_id": "chunks", "group_id": "groups", "node_id": "nodes", "element_id": "elements",
                  "element_type": "element_types"}.get(name)
        return self.latest(plural) if plural else ""


# ----------------------------------------------------------------------------
# 画面の投影
# ----------------------------------------------------------------------------

def _clip(text: str, n: int = OBSERVATION_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _str(v: Any) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def _generic(body: Any, depth: int = 0, max_lines: int = 40) -> list[str]:
    """DTO を「項目: 値」の行に平たく並べる（深さ 2 まで・リストは先頭 8 件）。"""
    lines: list[str] = []
    pad = "  " * depth
    if isinstance(body, dict):
        for k, v in body.items():
            if len(lines) >= max_lines:
                break
            if isinstance(v, (dict, list)) and depth < 2 and v:
                lines.append(f"{pad}{k}:")
                lines.extend(_generic(v, depth + 1, max_lines - len(lines)))
            elif v not in (None, "", [], {}):
                lines.append(f"{pad}{k}: {_str(v)[:200]}")
    elif isinstance(body, list):
        for i, v in enumerate(body[:8]):
            if isinstance(v, (dict, list)) and depth < 2:
                lines.append(f"{pad}- ({i + 1})")
                lines.extend(_generic(v, depth + 1, max_lines - len(lines)))
            else:
                lines.append(f"{pad}- {_str(v)[:200]}")
        if len(body) > 8:
            lines.append(f"{pad}- …ほか")
    else:
        lines.append(pad + _str(body)[:400])
    return lines


def _chat(body: dict) -> list[str]:
    out = ["AI の回答:", str(body.get("answer", ""))]
    stance = body.get("stance") or {}
    if isinstance(stance, dict) and stance.get("label"):
        out.append(f"〔回答の調子: {stance.get('label')}〕")
    grounding = {"course_material": "教材に基づく", "other_material": "別の資料に基づく",
                 "model_generated": "出典を追えない AI の説明"}.get(body.get("content_grounding") or "", "")
    if grounding:
        out.append(f"〔出所: {grounding}〕")
    for i, s in enumerate(body.get("sources") or [], 1):
        if isinstance(s, dict):
            out.append(f"出典{i}: {s.get('source_title') or s.get('title') or ''} {s.get('meta') or ''}".rstrip())
    if isinstance(body.get("mirror"), dict):
        out.append(f"〔鏡〕{body['mirror'].get('text', '')}")
    confirm = body.get("anchor_confirm")
    if isinstance(confirm, dict):
        opts = " / ".join(str(o.get("label", "")) for o in confirm.get("options") or [] if isinstance(o, dict))
        out.append(f"確認: {confirm.get('question', '')}（{opts}）")
    for a in body.get("next_actions") or []:
        if isinstance(a, dict):
            out.append(f"ボタン: {a.get('label', '')}")
    for c in body.get("manual_citations") or []:
        if isinstance(c, dict):
            out.append(f"マニュアル: {c.get('title', '')}")
    if body.get("degraded"):
        out.append("〔この回答は縮退した固定文です〕")
    return out


def _error(status: int, body: Any) -> str:
    detail = body.get("detail") if isinstance(body, dict) else body
    return f"エラーが表示された（HTTP {status}）: {_str(detail)[:600] if detail not in (None, '') else '（詳細なし）'}"


def project_observation(action_id: str, status: Optional[int], body: Any) -> str:
    """応答をペルソナが読む画面の文に投影する（最大 ~1500 字）。"""
    if action_id.startswith("unsupported:"):
        return "この操作は画面に見当たらない（実行できなかった）。"
    if status is None and isinstance(body, dict) and body.get("_precondition"):
        return f"この操作に必要なものがまだ画面に無い（{body['_precondition']}）。"
    if status is None:
        return "画面が応答しなかった（接続できない / 時間切れ）。"
    if status >= 400:
        return _clip(_error(status, body))
    if isinstance(body, dict) and "answer" in body and action_id.startswith(
            ("learning.chat", "learning.discuss.ask", "learning.corpus.discuss_ask", "admin.course_builder.chat",
             "admin.copilot")):
        lines = _chat(body)
        if action_id == "admin.course_builder.chat" and isinstance(body.get("course_draft"), dict):
            draft = body["course_draft"]
            titles = [t.get("title", "") for t in draft.get("topics") or [] if isinstance(t, dict)]
            lines.append(f"コースの下書き: {draft.get('title', '')}（トピック: {'、'.join(titles[:12])}）")
        return _clip("\n".join(lines))
    if isinstance(body, dict) and body.get("_binary"):
        return f"音声・画像が返ってきた（{body['_binary']}）。"
    if action_id == "learning.course.list" and isinstance(body, list):
        lines = ["コース一覧:"]
        for c in body[:20]:
            if isinstance(c, dict):
                tag = "（受講可能）" if c.get("is_enrollable") else ""
                lines.append(f"- {c.get('title', '')}{tag} id={c.get('id', '')}")
        return _clip("\n".join(lines) if len(lines) > 1 else "コースが 1 つも表示されていない。")
    if action_id == "learning.topic.open" and isinstance(body, dict):
        lines = ["教材:"]
        for ch in body.get("chunks") or []:
            if isinstance(ch, dict):
                lines.append(str(ch.get("text") or ch.get("content") or "")[:500])
        return _clip("\n".join(lines))
    lines = _generic(body)
    return _clip("\n".join(lines) if lines else "（画面に何も表示されなかった）")
