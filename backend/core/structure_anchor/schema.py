"""StructureAnchorAgent の dataclass 定義・語彙定数（構造帰属型の問い記録）。

FastAPI には依存しない（core/ 規約）。全 dataclass は JSON シリアライズ可能。
帰属語彙は domain-independent（特定分野・特定論文の用語をハードコードしない）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

DETECTOR_VERSION = "structure-anchor/v1"

# 帰属先の構造粒度（設計書 §5）。細かい粒度を優先し、構造を持たない教材では
# claim → concept → chunk → segment の順で粗い粒度へ縮退させる。
ANCHOR_TYPES = (
    "claim",            # atomic claim（theory_claims）
    "equation",         # 数式ブロック（theory_claims の claim_type='equation'）
    "derivation_step",  # 式変形・導出ステップ（claim_type='derivation_step'）
    "concept",          # 概念ノード（theory_components）
    "stage",            # theory stage（TheoryOperationGraph の domain-neutral 語彙）
    "chunk",            # 教材チャンク（場所）
    "segment",          # レクチャーセグメント（最も粗い縮退先）
)

ANCHOR_TYPE_LABELS = {
    "claim": "主張",
    "equation": "数式",
    "derivation_step": "導出ステップ",
    "concept": "概念",
    "stage": "理論構成の段階",
    "chunk": "教材の箇所",
    "segment": "教材の区画",
}

# 疑いの様相タクソノミー（設計書 §4）。unclassified は分類不能の保持用（P4）。
DOUBT_TYPES = (
    "definition",         # 用語・記号の意味が不明
    "justification_gap",  # なぜ成り立つのか（導出の飛躍）
    "premise",            # 前提そのものへの疑い
    "prior_conflict",     # 既有知識との衝突（tension と連続）
    "scope",              # どこまで成り立つのか（適用範囲）
    "connection",         # 他の概念とどう繋がるのか
    "unclassified",       # 分類不能（削除しない）
)

DOUBT_TYPE_LABELS = {
    "definition": "定義がわからない",
    "justification_gap": "なぜ成り立つのか",
    "premise": "前提への疑い",
    "prior_conflict": "既有知識との衝突",
    "scope": "どこまで成り立つのか",
    "connection": "他とどう繋がるのか",
    "unclassified": "未分類の引っかかり",
}

# 帰属の確定状態（設計書 §5）。行の status とは独立に payload 内で管理する
# （問い自体は記録時点で確定済み。候補なのは帰属だけ）。
ATTRIBUTION_LEARNER_SELECTED = "learner_selected"  # 発話時の明示アンカー（同期・確定）
ATTRIBUTION_LLM_CANDIDATE = "llm_candidate"        # 非同期LLM帰属（本人未確定。P1）
ATTRIBUTION_CONFIRMED = "confirmed"                # 本人が確定/訂正した帰属
ATTRIBUTION_SOURCES = (
    ATTRIBUTION_LEARNER_SELECTED,
    ATTRIBUTION_LLM_CANDIDATE,
    ATTRIBUTION_CONFIRMED,
)

# structure_anchor.status（dismiss しても削除しない。P4）
ANCHOR_STATUS_ACTIVE = "active"
ANCHOR_STATUS_DISMISSED = "dismissed"

# theory stage の語彙は ComponentGraphAgent の schema.THEORY_STAGES を正とする
# （backend からは src パッケージが見えない環境があるためフォールバックを持つ。
#  フォールバック値は #308 の domain-neutral 語彙と同一に保つこと）。
try:  # pragma: no cover - 環境依存の import
    from episteme_graph.agents.component_graph.schema import THEORY_STAGES as _STAGES

    THEORY_STAGES = tuple(_STAGES)
except Exception:  # pragma: no cover
    THEORY_STAGES = (
        "theory_basis",
        "observation_model",
        "observable_construction",
        "equation_system",
        "elimination",
        "consistency_relation",
        "diagnostic_application",
    )

# 既存 LearningChatRequest.element_type（chunk_graph_mentions の語彙）→ anchor_type。
# relationship/keyword は最も近い concept へ、出典系は chunk へ丸める（方法A）。
ELEMENT_TYPE_TO_ANCHOR_TYPE = {
    "formula": "equation",
    "concept": "concept",
    "relationship": "concept",
    "keyword": "concept",
    "reference": "chunk",
    "citation": "chunk",
}


@dataclass
class QuestionRecord:
    """帰属対象の問い1件（interest_traces の kind='question' 行に対応）。"""

    trace_id: str
    text: str
    context_label: str = ""
    segment_id: int | None = None            # payload.position_anchor.segment_id
    cited_chunk_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AnchorContext:
    """Stage 2(B) への入力単位（未帰属の問い + 参照可能なアンカー候補ブロック）。

    アンカー候補は実在する id のみ渡す（id 捏造防止。tension の context blocks と同方式）。
    """

    course_id: str
    topic_id: str
    topic_title: str
    questions: list[QuestionRecord] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)            # [{"id":..., "text":...}]
    equations: list[dict] = field(default_factory=list)         # [{"id":..., "text":...}]
    derivation_steps: list[dict] = field(default_factory=list)  # [{"id":..., "text":...}]
    concepts: list[dict] = field(default_factory=list)          # [{"id":..., "label":...}]
    chunks: list[dict] = field(default_factory=list)            # [{"id":..., "head":...}]
    segments: list[dict] = field(default_factory=list)          # [{"id":..., "label":...}]
    stages: tuple = THEORY_STAGES

    def question_ids(self) -> set[str]:
        return {q.trace_id for q in self.questions}

    def question_by_id(self, trace_id: str) -> QuestionRecord | None:
        for q in self.questions:
            if q.trace_id == trace_id:
                return q
        return None

    def allowed_ids(self, anchor_type: str) -> set[str]:
        """anchor_type ごとに参照してよい id の集合（stage は語彙そのもの）。"""
        blocks = {
            "claim": self.claims,
            "equation": self.equations,
            "derivation_step": self.derivation_steps,
            "concept": self.concepts,
            "chunk": self.chunks,
            "segment": self.segments,
        }
        if anchor_type == "stage":
            return set(self.stages)
        return {str(b.get("id")) for b in blocks.get(anchor_type, []) if b.get("id")}

    def label_for(self, anchor_type: str, anchor_id: str) -> str:
        """context blocks からアンカー表示名を引く（LLM が label を省略した場合の補完）。"""
        if anchor_type == "stage":
            return anchor_id
        blocks = {
            "claim": self.claims,
            "equation": self.equations,
            "derivation_step": self.derivation_steps,
            "concept": self.concepts,
            "chunk": self.chunks,
            "segment": self.segments,
        }.get(anchor_type, [])
        for b in blocks:
            if str(b.get("id")) == str(anchor_id):
                return str(b.get("label") or b.get("text") or b.get("head") or anchor_id)[:80]
        return anchor_id

    def to_dict(self) -> dict:
        return {
            "course_id": self.course_id,
            "topic_id": self.topic_id,
            "topic_title": self.topic_title,
            "questions": [q.to_dict() for q in self.questions],
            "claims": self.claims,
            "equations": self.equations,
            "derivation_steps": self.derivation_steps,
            "concepts": self.concepts,
            "chunks": self.chunks,
            "segments": self.segments,
            "stages": list(self.stages),
        }


@dataclass
class AnchorCandidate:
    """LLM が提案する帰属候補1件（1問い=最大1アンカー。本人 confirm までは候補。P1）。"""

    trace_id: str
    anchor_type: str
    anchor_id: str
    anchor_label: str
    doubt_type: str
    evidence_quote: str   # 質問文中の該当箇所の逐語引用（部分文字列であること）
    confidence: float
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AnchorCandidate":
        return cls(
            trace_id=str(data.get("trace_id") or ""),
            anchor_type=str(data.get("anchor_type") or ""),
            anchor_id=str(data.get("anchor_id") or ""),
            anchor_label=str(data.get("anchor_label") or ""),
            doubt_type=str(data.get("doubt_type") or ""),
            evidence_quote=str(data.get("evidence_quote") or ""),
            confidence=float(data.get("confidence") or 0.0),
            reason=str(data.get("reason") or ""),
        )


@dataclass
class UnattributedQuestion:
    """帰属できなかった問い（縮退先すら無い場合。理由付きで保持する。P4）。"""

    trace_id: str
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AnchorMiningResult:
    """Stage 2(B) の出力。repair_failed=True は2回修復失敗（P4: 破棄せず痕跡に印を残す）。"""

    candidates: list[AnchorCandidate] = field(default_factory=list)
    unattributed: list[UnattributedQuestion] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    repair_failed: bool = False

    def to_dict(self) -> dict:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "unattributed": [u.to_dict() for u in self.unattributed],
            "warnings": list(self.warnings),
            "repair_failed": self.repair_failed,
        }


def build_anchor_payload(
    *,
    anchor_type: str,
    anchor_id: str,
    anchor_label: str,
    doubt_type: str,
    attribution_source: str,
    evidence_quote: str = "",
    reason: str = "",
    confidence: float = 0.0,
    status: str = ANCHOR_STATUS_ACTIVE,
) -> dict:
    """payload.structure_anchor の正規形（設計書 §5）。語彙外は unclassified 等へ丸める。"""
    return {
        "anchor_type": anchor_type if anchor_type in ANCHOR_TYPES else "segment",
        "anchor_id": str(anchor_id or ""),
        "anchor_label": str(anchor_label or "")[:80],
        "doubt_type": doubt_type if doubt_type in DOUBT_TYPES else "unclassified",
        "attribution_source": (
            attribution_source if attribution_source in ATTRIBUTION_SOURCES
            else ATTRIBUTION_LLM_CANDIDATE
        ),
        "evidence_quote": str(evidence_quote or "")[:300],
        "reason": str(reason or "")[:500],
        "confidence": round(float(confidence or 0.0), 3),
        "status": status if status in (ANCHOR_STATUS_ACTIVE, ANCHOR_STATUS_DISMISSED) else ANCHOR_STATUS_ACTIVE,
        "detector_version": DETECTOR_VERSION,
    }


def candidate_to_anchor_payload(candidate: AnchorCandidate) -> dict:
    """AnchorCandidate → payload.structure_anchor（attribution_source='llm_candidate'）。"""
    return build_anchor_payload(
        anchor_type=candidate.anchor_type,
        anchor_id=candidate.anchor_id,
        anchor_label=candidate.anchor_label,
        doubt_type=candidate.doubt_type,
        attribution_source=ATTRIBUTION_LLM_CANDIDATE,
        evidence_quote=candidate.evidence_quote,
        reason=candidate.reason,
        confidence=candidate.confidence,
    )


def anchor_type_for_element(element_type: str | None) -> str:
    """既存の element_type（グラフ要素タップ）→ anchor_type（方法A）。不明は concept。"""
    return ELEMENT_TYPE_TO_ANCHOR_TYPE.get((element_type or "").strip(), "concept")
