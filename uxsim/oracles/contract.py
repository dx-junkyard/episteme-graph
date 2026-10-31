"""審判 A — 契約（決定論・LLM 0 回）。

- HTTP 5xx / 接続断 / nginx の 120 秒を超える応答
- 429 の後、上限に達したことが画面（投影）に出ているか
- 学習チャット系の DTO の形（``answer`` の存在・``stance`` が {stance, source, label} の 3 キーだけ）
- ``degraded: true``（製品側 LLM の失敗で固定文に縮退した往復）

限界: API runner は UI を通らないので、429 の detail を UI が汎用文に潰す欠陥（§8.3 s-quota-edge）は
browser runner で見る。ここで見えるのは「API の detail 自体が上限を言っているか」まで。
"""
from __future__ import annotations

import re
from typing import Any, Iterator

from uxsim.oracles.findings import FindingFactory, excerpt_json
from uxsim.oracles.principle import AI_READING_LABEL  # 製品の正本（principle.py 経由 — 製品 import は 1 箇所）
from uxsim.schema import Finding, TranscriptStep

TIMEOUT_MS = 120_000
QUOTA_WORDS = ("上限", "回数", "制限")
STANCE_KEYS = {"stance", "source", "label"}
CHAT_ACTIONS = ("learning.chat.", "learning.discuss.ask", "learning.corpus.discuss_ask")
_STANCE_RE = re.compile(r'"stance"\s*:\s*(\{[^{}]*\}|null)')


def _skip(trace) -> bool:
    return trace.error.startswith(("precondition:", "unsupported"))


HYP_BRAIN_LATENCY = ("ハーネス: {where} の応答が nginx の読み取り上限（120 秒）を超える"
                     "（製品の LLM 呼び出しが頭脳（mailbox）で待たされた遅延。製品欠陥の根拠にしない）")


def _product_llm_action(action_id: str) -> bool:
    from uxsim.actions import registry
    a = registry.get(action_id)
    return bool(a is not None and getattr(a, "llm_cost", "") == "product")


def check(steps: list[TranscriptStep], factory: FindingFactory, *, brain_backed: bool = False) -> list[Finding]:
    """``brain_backed`` が真（製品の LLM が mailbox の頭脳へ回っている）なら、製品 LLM を呼ぶ行為の 120 秒超えは
    「ハーネス:」印で出す（数値の検査自体は残す = PE7）。"""
    out: list[Finding] = []
    for i, step in enumerate(steps):
        for t in step.http:
            if _skip(t):
                continue
            where = f"{t.method} {t.path}"
            if t.status is None:
                out.append(factory.make(oracle="A", severity="blocked", step=step, quote=t.error,
                                        hypothesis=f"{where} が応答しない（接続断または時間切れ）"))
            elif t.status >= 500:
                out.append(factory.make(oracle="A", severity="blocked", step=step, quote=t.response_excerpt,
                                        hypothesis=f"{where} がサーバエラー（5xx）を返す"))
            if t.elapsed_ms is not None and t.elapsed_ms > TIMEOUT_MS:
                if brain_backed and _product_llm_action(step.action_id):
                    out.append(factory.make(oracle="A", severity="confused", step=step,
                                            hypothesis=HYP_BRAIN_LATENCY.format(where=where)))
                else:
                    out.append(factory.make(oracle="A", severity="blocked", step=step,
                                            hypothesis=f"{where} の応答が nginx の読み取り上限（120 秒）を超える"))
            if t.status == 429:
                seen = step.observation
                nxt = next((s for s in steps[i + 1:] if s.persona_id == step.persona_id), None)
                if nxt is not None:
                    seen += "\n" + nxt.observation
                if not any(w in seen for w in QUOTA_WORDS):
                    out.append(factory.make(oracle="A", severity="confused", step=step, quote=step.observation,
                                            hypothesis=f"{where} が 429 を返したが、上限に達したことが画面に示されない"))
        if step.action_id.startswith(CHAT_ACTIONS):
            out.extend(_check_chat_dto(step, factory))
        if step.action_id.startswith(GRAPH_CHAT_ACTIONS):
            out.extend(_check_stance_prefix(step, factory))
        if step.action_id.startswith(HOP_ACTIONS):
            out.extend(_check_hop_degraded(step, factory))
        if step.action_id.startswith(SYMBOL_ACTIONS):
            out.extend(_check_symbol_scope(step, steps[:i], factory))
    return out


GRAPH_CHAT_ACTIONS = ("admin.graph_review.chat",)
HOP_ACTIONS = ("learning.component.context",)
SYMBOL_ACTIONS = ("learning.symbol.lookup",)


def _ok_bodies(step: TranscriptStep) -> Iterator[tuple[Any, Any]]:
    for t in step.http:
        if t.status == 200 and t.response_excerpt:
            body = excerpt_json(t.response_excerpt)
            if isinstance(body, dict):
                yield t, body


def _check_stance_prefix(step: TranscriptStep, factory: FindingFactory) -> list[Finding]:
    """ラベルは画面が描く。reply / spoken の先頭に同じ文言が重複していないか（§18 追補・strip_stance_prefix）。"""
    for _t, body in _ok_bodies(step):
        label = str(body.get("stance_label") or AI_READING_LABEL)
        for key in ("reply", "spoken"):
            text = str(body.get(key) or "").lstrip()
            if label and text.startswith(label):
                return [factory.make(oracle="A", severity="inconsistent", step=step, quote=f"{key}: {text[:80]}",
                                     hypothesis="グラフ対話の返答本文の先頭に、画面が別に描く留保ラベルが重複している",
                                     layers=["graph_review", "frontend_admin_ui"])]
    return []


def _check_hop_degraded(step: TranscriptStep, factory: FindingFactory) -> list[Finding]:
    """中心を移したのに graph が空 — 契約違反ではなく縮退の事実として別の仮説で残す。"""
    for _t, body in _ok_bodies(step):
        if "graph" in body and not body.get("graph"):
            return [factory.make(oracle="A", severity="confused", step=step, quote="graph: 空",
                                 hypothesis="縮退の事実: 部品の文脈で中心を移したが、周りの関係（graph）が空で返った")]
    return []


def _document_ids(v: Any, key: str = "") -> Iterator[str]:
    if isinstance(v, dict):
        for k, x in v.items():
            yield from _document_ids(x, k)
    elif isinstance(v, list):
        for x in v:
            yield from _document_ids(x, key)
    elif isinstance(v, str) and key == "document_id" and v:
        yield v


def _course_source_documents(prior: list[TranscriptStep], persona: str) -> set[str]:
    docs: set[str] = set()
    for s in prior:
        if s.persona_id != persona or not s.action_id.startswith("learning.course."):
            continue
        for _t, body in _ok_bodies(s):
            course = body.get("course") if isinstance(body.get("course"), dict) else body
            data = course.get("data") if isinstance(course.get("data"), dict) else course
            docs.update(_document_ids(data.get("sources") or []))
    return docs


def _check_symbol_scope(step: TranscriptStep, prior: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    """記号の定義が、いま受講しているコースの sources に無い論文から返った（別論文を探した）。"""
    course_docs = _course_source_documents(prior, step.persona_id)
    if not course_docs:
        return []  # コースの sources が transcript に無いときは判定しない
    for _t, body in _ok_bodies(step):
        outside = sorted(set(_document_ids(body)) - course_docs)
        if outside:
            return [factory.make(oracle="A", severity="inconsistent", step=step, quote="document_id: コース外",
                                 hypothesis="記号の定義を、受講中のコースの資料ではない別論文から探して返した",
                                 layers=["concept_registry", "frontend_learning_ui"])]
    return []


def _check_chat_dto(step: TranscriptStep, factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []
    for t in step.http:
        if t.status != 200 or not t.path.endswith("/chat"):
            continue
        body = excerpt_json(t.response_excerpt)
        text = t.response_excerpt
        if body is not None and not (isinstance(body, dict) and "answer" in body):
            out.append(factory.make(oracle="A", severity="inconsistent", step=step, quote=text[:200],
                                    hypothesis="学習チャットの応答に answer が無い"))
        stance = body.get("stance") if isinstance(body, dict) else None
        if body is None:
            m = _STANCE_RE.search(text)
            stance = excerpt_json(m.group(1)) if m else None
        if isinstance(stance, dict) and set(stance) != STANCE_KEYS:
            out.append(factory.make(oracle="A", severity="inconsistent", step=step, quote=str(stance)[:200],
                                    hypothesis="学習チャットの stance が {stance, source, label} の 3 キーではない"))
        degraded = body.get("degraded") if isinstance(body, dict) else ('"degraded": true' in text)
        if degraded:
            out.append(factory.make(oracle="A", severity="inconsistent", step=step,
                                    hypothesis="製品側の LLM 呼び出しが失敗し、回答が固定文に縮退した"))
    return out
