"""R層の dataclass と語彙の正本。

DB の CHECK 制約と対応する語彙・伏せフィールド・承認語彙をここに集約する。
theory_review_events.entity_type に CHECK 制約は無いため、R層の追加語彙
（'reconstruction_item' / 'reconstruction_response'）はここで管理する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core import schema as core_schema

# --- 語彙（DB CHECK 制約と対応） -------------------------------------------

ELICIT_MODES = ("predict", "restate", "symbol")
ITEM_STATUSES = ("auto", "flagged", "retired", "confirmed")
# 学習者に配信してよい item の status（P1: auto=candidate 相当も配信可、retire で回収）
DELIVERABLE_STATUSES = ("auto", "confirmed")
MACHINE_VERDICTS = ("match", "mismatch", "na")
SELF_CHECK_VALUES = ("agreed", "disagreed", "verdict_wrong")

# 監査 entity_type（theory_review_events）。R層の追加語彙。値の正本は
# core/schema.py の AUDIT_ENTITY_TYPES カタログ（全層横断の唯一の正本）。
ENTITY_ITEM = core_schema.AUDIT_ENTITY_RECONSTRUCTION_ITEM
ENTITY_RESPONSE = core_schema.AUDIT_ENTITY_RECONSTRUCTION_RESPONSE

# 出題対象とする claim の support_status / review_status。
# 未検証の構造で学習者を試さない（§2.2）。承認語彙は C層実装（teacher_approved が主。
# 一部で teacher_reviewed / endorsed も使われるため寛容に受ける）に合わせる。
SOURCE_BACKED = "source_backed"
APPROVED_REVIEW_STATUSES = ("teacher_approved", "teacher_reviewed", "endorsed")

# ELICIT で learner に返してはならない（=答え）claim フィールド（§3.2）。
HIDDEN_CLAIM_FIELDS = ("text", "normalized_text", "equation", "evidence_text")

# predict モードを検討する余地のある claim_type（関係を構造化しやすいもの）。
# これに該当しなければ restate へ縮退する（LLM は最終的に mode を下げられる）。
_RELATIONAL_CLAIM_TYPES = (
    "relation",
    "equation",
    "equation_relation",
    "equation_transformation",
    "result",
    "diagnostic_claim",
    "correction",
)


# --- dataclass ------------------------------------------------------------


@dataclass
class ResponseOption:
    """predict モードの選択肢。"""

    id: str
    label: str

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label}


@dataclass
class ReconstructionItem:
    """reconstruction_items 行の dataclass 表現。"""

    id: str = ""
    claim_id: str = ""
    document_id: str = ""
    elicit_mode: str = "predict"
    prompt: str = ""
    response_space: list[dict] = field(default_factory=list)
    expected: dict = field(default_factory=dict)
    claim_fields_used: list[str] = field(default_factory=list)
    author: str = "llm"
    author_confidence: float = 0.0
    status: str = "auto"


@dataclass
class ItemAuthoringResult:
    """item オーサリング worker（LLM）が返す検証済み出力。

    repair_failed=True のときは item を生成しない（=配信しない。§4.2 repair）。
    """

    elicit_mode: str = "restate"
    prompt: str = ""
    response_space: list[ResponseOption] = field(default_factory=list)
    expected: dict = field(default_factory=dict)
    claim_fields_used: list[str] = field(default_factory=list)
    author_confidence: float = 0.0
    reason: str = ""
    repair_failed: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class DiffResult:
    """実行時構造照合（非LLM・同期）の結果。

    machine_verdict は「仮説」であり authoritative ではない（権威は出典リビール）。
    数値スコアは持たない（P7）。
    """

    machine_verdict: str = "na"  # match | mismatch | na
    expected_concepts: list[str] = field(default_factory=list)
    covered_concepts: list[str] = field(default_factory=list)
    missing_concepts: list[str] = field(default_factory=list)
    # predict の型照合に使った情報（人間可読の事実断片。数値・順位を含めない）
    focus: str = ""

    def to_dict(self) -> dict:
        return {
            "machine_verdict": self.machine_verdict,
            "expected_concepts": list(self.expected_concepts),
            "covered_concepts": list(self.covered_concepts),
            "missing_concepts": list(self.missing_concepts),
            "focus": self.focus,
        }


def is_relational_claim_type(claim_type: str) -> bool:
    return str(claim_type or "").strip() in _RELATIONAL_CLAIM_TYPES


def subject_driver_concepts(claim: dict[str, Any]) -> list[str]:
    """claim.concepts から subject / driver 概念名を抽出する。

    concepts に role フィールドがあれば subject/driver を優先。無ければ
    先頭2つ（名前が取れるもの）を返す。domain-specific な語をハードコードしない。
    """
    concepts = claim.get("concepts") if isinstance(claim, dict) else None
    if not isinstance(concepts, list):
        return []
    named: list[str] = []
    roled: list[str] = []
    for c in concepts:
        if not isinstance(c, dict):
            if isinstance(c, str) and c.strip():
                named.append(c.strip())
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        role = str(c.get("role") or "").strip().lower()
        if role in ("subject", "driver", "variable", "observable"):
            roled.append(name)
        named.append(name)
    if roled:
        # 重複除去（順序保持）
        seen: set[str] = set()
        out = []
        for n in roled:
            if n not in seen:
                seen.add(n)
                out.append(n)
        return out
    return named[:2]
