"""質問 → コンポーネント候補生成サービス(C層 §5)。

受講者の質問と AI 回答本文から、「回答で説明されている理論・概念」を
theory_components の候補として抽出し、その説明が依拠しうる既存 theory_claims の
候補を提示する。A層(生成パイプライン)には手を入れず、既存の LLM 抽象化
(core.llm.generate_text_with_structured_output)と claim 参照だけを使う。

重要: claim 紐づけの最終確定は必ず教員。ここでは「候補」提示に限定し、
出力はすべて candidate / teacher_review_required 状態を前提とする。
core モジュールなので FastAPI は import しない(テスタビリティ確保)。
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from core.llm import generate_text_with_structured_output

logger = logging.getLogger(__name__)

# theory_components.component_type の許可語彙(migration 013 の CHECK 制約に一致)
ALLOWED_COMPONENT_TYPES = ("theory", "concept", "law", "mechanism", "operator", "observation")


class CandidateGenerationError(Exception):
    """LLM 呼び出し自体が失敗した場合の専用例外(§4 B2)。

    「LLM が正常応答して候補が0件」と「LLM 呼び出し自体が失敗」を区別するため、
    後者はこの例外で送出する(握りつぶして空配列を返さない)。呼び出し元(route)が
    503 + 事実文に変換する。
    """


class CandidateClaimLink(BaseModel):
    """AI が提示する claim 紐づけ候補(教員が確定するまでは候補扱い)。"""

    claim_id: str = ""
    reason: str = ""
    confidence: float = 0.0


class CandidateComponent(BaseModel):
    """回答で説明されている理論・概念の候補。"""

    name: str = ""
    component_type: str = "concept"
    summary: str = ""
    explanation_body: str = ""
    backing_claims: list[CandidateClaimLink] = Field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0


class CandidateGenerationResult(BaseModel):
    components: list[CandidateComponent] = Field(default_factory=list)


_PROMPT = """あなたは大学院の教材編集を補助するアシスタントです。
以下は受講者の質問と、それに対する AI の回答本文です。

# 受講者の質問
{question}

# AI の回答本文
{answer}

# 参照可能な既存 Claim(id と本文)
{claims_block}

## タスク
1. 回答本文の中で「説明されている理論・概念(theory / concept / law / mechanism / operator / observation)」を抽出し、
   再利用可能なコンポーネント候補として列挙してください。
2. 各コンポーネント候補について、その説明が依拠しうる既存 Claim を上記リストの id から選び、
   backing_claims に (claim_id, reason, confidence) の形で候補として提示してください。
   - リストに無い claim_id は絶対に作らないでください。該当が無ければ backing_claims は空にします。
   - これはあくまで「候補」です。最終確定は人間(教員)が行います。
3. 各候補には日本語の summary(1〜2文)と explanation_body(教材に載せられる説明文の草案)を付けてください。
4. reason には「なぜこの理論/概念を候補にしたか」、confidence には 0.0〜1.0 の確信度を入れてください。

必ず指定された JSON スキーマに従って出力してください。該当が無い場合は components を空配列にします。
"""


def _format_claims_block(existing_claims: list[dict]) -> str:
    if not existing_claims:
        return "(参照可能な Claim はありません)"
    lines = []
    for claim in existing_claims:
        cid = str(claim.get("id") or "").strip()
        text = str(claim.get("text") or "").strip().replace("\n", " ")
        if not cid:
            continue
        if len(text) > 300:
            text = text[:300] + "…"
        lines.append(f"- {cid}: {text}")
    return "\n".join(lines) if lines else "(参照可能な Claim はありません)"


def generate_component_candidates(
    question: str,
    answer_text: str,
    existing_claims: list[dict],
    *,
    model: str | None = None,
) -> CandidateGenerationResult:
    """質問+回答からコンポーネント候補を生成する。

    Parameters
    ----------
    question: 受講者の質問本文
    answer_text: AI 回答本文
    existing_claims: [{"id": str, "text": str}, ...] 紐づけ候補の母集団
    model: 使用する LLM モデル名(省略時は既定)
    """
    valid_ids = {str(c.get("id")) for c in existing_claims if c.get("id")}
    prompt = _PROMPT.format(
        question=(question or "").strip() or "(質問本文なし)",
        answer=(answer_text or "").strip() or "(回答本文なし)",
        claims_block=_format_claims_block(existing_claims),
    )
    try:
        result = generate_text_with_structured_output(
            messages=[{"role": "user", "content": prompt}],
            response_format=CandidateGenerationResult,
            model=model,
        )
    except Exception as exc:
        logger.exception("Component candidate generation failed")
        raise CandidateGenerationError("Component candidate generation failed") from exc

    # サニタイズ: component_type の正規化と、存在しない claim_id の除去。
    cleaned: list[CandidateComponent] = []
    for comp in result.components:
        ctype = (comp.component_type or "").strip().lower()
        if ctype not in ALLOWED_COMPONENT_TYPES:
            ctype = "concept"
        backing = [
            link
            for link in comp.backing_claims
            if link.claim_id and (not valid_ids or link.claim_id in valid_ids)
        ]
        if not (comp.name or "").strip():
            continue
        cleaned.append(
            CandidateComponent(
                name=comp.name.strip(),
                component_type=ctype,
                summary=comp.summary or "",
                explanation_body=comp.explanation_body or comp.summary or "",
                backing_claims=backing,
                reason=comp.reason or "",
                confidence=comp.confidence or 0.0,
            )
        )
    return CandidateGenerationResult(components=cleaned)
