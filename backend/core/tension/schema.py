"""TensionMiningAgent の dataclass 定義・語彙定数。

FastAPI には依存しない（core/ 規約）。全 dataclass は JSON シリアライズ可能。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

DETECTOR_VERSION = "tension-mining/v1"

# tension タクソノミー（設計書 §5.1）。unclassified は「明らかに tension だが分類不能」を
# 保持するための語彙（P4: 情報を落とさない）。
TENSION_TYPES = (
    "contradiction_sense",   # 2つの説明・出典が学習者には互いに矛盾して見える
    "definition_unease",     # 定義を復唱できるのに「しっくりこない」
    "boundary_probe",        # 適用範囲・妥当性の境界を突き続ける
    "analogy_itch",          # 別の何かとの構造的類似を感じている
    "source_doubt",          # 教材・論文そのものへの疑い
    "unstated_assumption",   # 暗黙の前提に引っかかっている
    "unclassified",          # 分類不能（削除しない）
)

TENSION_TYPE_LABELS = {
    "contradiction_sense": "矛盾の感覚",
    "definition_unease": "定義への引っかかり",
    "boundary_probe": "適用範囲への問い",
    "analogy_itch": "類似の直感",
    "source_doubt": "出典への疑い",
    "unstated_assumption": "暗黙の前提",
    "unclassified": "未分類の違和感",
}

# interest_traces.status のうち tension が使う語彙（services._TRACE_STATUSES と整合させる）
TENSION_STATUS_CANDIDATE = "candidate"      # LLM提案・本人未確定
TENSION_STATUS_DISMISSED = "dismissed"      # 本人が「違う」と判定（削除しない）
TENSION_STATUS_ARTICULATED = "articulated"  # 本人が自分の言葉で編集済み
TENSION_STATUS_CONNECTED = "connected"      # グラフ上の node/edge に接続済み
TENSION_STATUS_ABSTRACTED = "abstracted"    # 新しい抽象化・教材提案へ昇格

# 本人が引き受けた（＝集計対象になる）status（設計書 §10）
TENSION_OWNED_STATUSES = ("open", "articulated", "connected", "abstracted")


@dataclass
class ConversationTurn:
    """会話窓の1発話。turn_id は履歴配列 index から決定論的に振る（msg_0001 形式）。"""

    turn_id: str
    role: str                    # "learner" | "tutor"
    text: str
    tier: str | None = None      # tutor 発話の根拠 tier（あれば）
    tension_hint: bool = False   # Stage 0 prefilter が立てたヒント

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ConversationWindow:
    """Stage 1 への入力単位（ヒント発話を核にした会話窓 + 参照可能な context blocks）。"""

    course_id: str
    topic_id: str
    topic_title: str
    turns: list[ConversationTurn] = field(default_factory=list)
    # その窓で実際に検索ヒット・参照された component/chunk のみ（id 捏造防止）
    components: list[dict] = field(default_factory=list)  # [{"id": ..., "label": ...}]
    chunks: list[dict] = field(default_factory=list)      # [{"id": ..., "head": ...}]

    def turn_ids(self) -> set[str]:
        return {t.turn_id for t in self.turns}

    def learner_texts(self) -> list[str]:
        return [t.text for t in self.turns if t.role == "learner"]

    def allowed_component_ids(self) -> set[str]:
        return {str(c.get("id")) for c in self.components if c.get("id")}

    def allowed_chunk_ids(self) -> set[str]:
        return {str(c.get("id")) for c in self.chunks if c.get("id")}

    def to_dict(self) -> dict:
        return {
            "course_id": self.course_id,
            "topic_id": self.topic_id,
            "topic_title": self.topic_title,
            "turns": [t.to_dict() for t in self.turns],
            "components": self.components,
            "chunks": self.chunks,
        }


@dataclass
class TargetRefs:
    component_ids: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    topic_id: str = ""
    edge_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "TargetRefs":
        data = data or {}
        return cls(
            component_ids=[str(x) for x in (data.get("component_ids") or [])],
            chunk_ids=[str(x) for x in (data.get("chunk_ids") or [])],
            topic_id=str(data.get("topic_id") or ""),
            edge_ids=[str(x) for x in (data.get("edge_ids") or [])],
        )


@dataclass
class TensionCandidate:
    """LLM が提案する tension 候補1件（本人の confirm までは候補に留まる。P1）。"""

    tension_type: str
    evidence_quote: str          # 学習者発話の逐語引用（入力窓の部分文字列であること）
    turn_ids: list[str]
    target_refs: TargetRefs
    paraphrase: str              # 推量形・学習者の言語（P2）
    confidence: float
    reason: str                  # どのシグナル・どのターン・どのパターンか
    is_tension_not_gap: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["target_refs"] = self.target_refs.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "TensionCandidate":
        return cls(
            tension_type=str(data.get("tension_type") or ""),
            evidence_quote=str(data.get("evidence_quote") or ""),
            turn_ids=[str(x) for x in (data.get("turn_ids") or [])],
            target_refs=TargetRefs.from_dict(data.get("target_refs")),
            paraphrase=str(data.get("paraphrase") or ""),
            confidence=float(data.get("confidence") or 0.0),
            reason=str(data.get("reason") or ""),
            is_tension_not_gap=bool(data.get("is_tension_not_gap", True)),
        )


@dataclass
class RejectedGap:
    """tension ではなく理解ギャップと判定した箇所（既存 question/misconception 系の領分）。"""

    turn_ids: list[str]
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TensionMiningResult:
    """Stage 1 の出力。repair_failed=True は2回修復失敗（P4: 破棄せず1行残す）。"""

    candidates: list[TensionCandidate] = field(default_factory=list)
    rejected_as_gap: list[RejectedGap] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    repair_failed: bool = False

    def to_dict(self) -> dict:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "rejected_as_gap": [r.to_dict() for r in self.rejected_as_gap],
            "warnings": list(self.warnings),
            "repair_failed": self.repair_failed,
        }


def candidate_to_payload(
    candidate: TensionCandidate,
    context_label: str = "",
    overall_tier: str = "",
) -> dict:
    """TensionCandidate → interest_traces.payload（kind='tension'、設計書 §3.2）。"""
    return {
        "text": (candidate.evidence_quote or "")[:500],  # 500字上限は既存踏襲
        "context_label": context_label or "",
        "tension_type": candidate.tension_type,
        "paraphrase": candidate.paraphrase,
        "learner_text": "",
        "turn_ids": list(candidate.turn_ids),
        "target_refs": candidate.target_refs.to_dict(),
        "confidence": round(float(candidate.confidence), 3),
        "reason": candidate.reason,
        "detector_version": DETECTOR_VERSION,
        "overall_tier_at_detection": overall_tier or "",
    }
