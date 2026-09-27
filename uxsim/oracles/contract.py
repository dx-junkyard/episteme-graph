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

from uxsim.oracles.findings import FindingFactory, excerpt_json
from uxsim.schema import Finding, TranscriptStep

TIMEOUT_MS = 120_000
QUOTA_WORDS = ("上限", "回数", "制限")
STANCE_KEYS = {"stance", "source", "label"}
CHAT_ACTIONS = ("learning.chat.", "learning.discuss.ask", "learning.corpus.discuss_ask")
_STANCE_RE = re.compile(r'"stance"\s*:\s*(\{[^{}]*\}|null)')


def _skip(trace) -> bool:
    return trace.error.startswith(("precondition:", "unsupported"))


def check(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
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
    return out


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
