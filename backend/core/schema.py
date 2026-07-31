"""Pydantic schemas for extracted problem structures."""

from __future__ import annotations

from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class OntologyType(str, Enum):
    """ナレッジグラフのノード型。

    OSL (.isom) 準拠の汎用 4 型に加え、素粒子物理学（場の量子論等）の
    学習支援に必要なドメイン固有型を定義する。
    """

    # --- 汎用 (OSL 準拠) ---
    AGENT = "Agent"
    EVENT = "Event"
    RESOURCE = "Resource"
    INTENTIONAL_MOMENT = "Intentional Moment"

    # --- 素粒子物理学向け拡張 ---
    MATHEMATICAL_OBJECT = "MathematicalObject"       # テンソル、群、多様体、作用素など
    PHYSICAL_PHENOMENON = "PhysicalPhenomenon"       # 相転移、散乱、崩壊、輻射補正など
    THEORETICAL_FRAMEWORK = "TheoreticalFramework"   # QFT、QED、QCD、標準模型など
    THEOREM = "Theorem"                               # Noetherの定理、Ward恒等式、LSZ公式など
    SYMMETRY = "Symmetry"                             # ゲージ対称性、ローレンツ対称性、CPT対称性など
    PARTICLE = "Particle"                             # クォーク、レプトン、ゲージボソン、ヒッグス場など


class CorePredicate(str, Enum):
    """分野横断検索を可能にする標準化されたエッジ述語（Core Predicate）。

    ドメイン固有の動詞（domain_verb）の上位に位置する抽象述語であり、
    異分野間の Structural Isomorphism 検索を PostgreSQL 上の概念構造
    （`theory_components` / `theory_claims` / `theory_component_graphs`）で
    実現するために使用する。
    """

    CAUSES = "CAUSES"
    INHIBITS = "INHIBITS"
    CORRELATES = "CORRELATES"
    DEFINES = "DEFINES"
    MEASURES = "MEASURES"
    TRANSFORMS = "TRANSFORMS"
    REQUIRES = "REQUIRES"
    CONTAINS = "CONTAINS"
    EQUIVALENT = "EQUIVALENT"


class MetaIssueCategory(str, Enum):
    """メタ提案の問題分類。表現モデル自体の限界に関するカテゴリ。"""

    MISSING_EDGE_TYPE = "missing_edge_type"
    MISSING_ONTOLOGY_LEVEL = "missing_ontology_level"
    TEMPORAL_LIMITATION = "temporal_limitation"
    MULTI_SCALE_LIMITATION = "multi_scale_limitation"
    BIDIRECTIONAL_LIMITATION = "bidirectional_limitation"
    OTHER = "other"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProblemStatement(BaseModel):
    """Background context and the core problem to be solved."""

    background: str = Field(default="", description="Background context of the research")
    problem: str = Field(default="", description="Core problem the paper addresses")


class Hypothesis(BaseModel):
    """Research hypothesis or conjecture."""

    statement: str = Field(default="", description="Main hypothesis")
    rationale: str = Field(default="", description="Rationale behind the hypothesis")


class Methodology(BaseModel):
    """Approach and methods used in the research."""

    approach: str = Field(default="", description="High-level approach")
    techniques: list[str] = Field(default_factory=list, description="Specific techniques or tools used")


class Constraints(BaseModel):
    """Constraints, assumptions, and limitations."""

    assumptions: list[str] = Field(default_factory=list, description="Underlying assumptions")
    limitations: list[str] = Field(default_factory=list, description="Known limitations")


class CausalEdge(BaseModel):
    """A directed edge in the causal/relational graph."""

    source: str = Field(description="Source variable")
    target: str = Field(description="Target variable")
    core_predicate: CorePredicate = Field(
        default=CorePredicate.CAUSES,
        description=(
            "Standardized predicate for cross-domain search over PostgreSQL-backed "
            "concept structures "
            "(CAUSES, INHIBITS, CORRELATES, DEFINES, MEASURES, TRANSFORMS, "
            "REQUIRES, CONTAINS, EQUIVALENT)"
        ),
    )
    domain_verb: str = Field(
        default="causes",
        description="Domain-specific verb describing the relation (e.g., operationalizes, structures, quantifies, contains, equals)",
    )
    polarity: str = Field(
        default="+",
        description=(
            "Edge polarity: '+' (positive/促進), '-' (negative/抑制), "
            "'+/-' (conditional/bidirectional — context-dependent positive or negative), "
            "'?' (unknown direction — impact exists but polarity is unclear)"
        ),
    )

    @field_validator("polarity")
    @classmethod
    def validate_polarity(cls, v: str) -> str:
        allowed = {"+", "-", "+/-", "?"}
        if v not in allowed:
            raise ValueError(f"polarity must be one of {allowed}, got '{v}'")
        return v
    ontology_level: str = Field(default="", description="Ontology relation type (e.g., Intentional Moment)")
    is_core: bool = Field(
        default=True,
        description="True for backbone/core mechanism edges, False for peripheral/supplementary edges",
    )


class AbstractStructure(BaseModel):
    """Abstract structure extracted from the paper: variables and causal edges."""

    variables: list[str] = Field(default_factory=list, description="Extracted variables / key concepts")
    edges: list[CausalEdge] = Field(default_factory=list, description="Causal or relational edges")
    smiles_dsl: str = Field(default="", description="Episteme Graph-SMILES format (e.g., (a:Agent:Organization) ==[CAUSES:operationalizes:+]=> (r:Resource:Profit))")


class VariableDefinition(BaseModel):
    """数式中の変数とその物理的意味の対応。"""

    symbol: str = Field(description="LaTeX記法の変数シンボル (例: '$L$', '$\\\\psi$')")
    description: str = Field(description="変数の物理的意味 (例: 'ラグランジアン密度', 'ディラック場')")


class KeyEquation(BaseModel):
    """論文中の重要な数式とその変数定義。"""

    latex: str = Field(description="LaTeX形式の数式 (例: '$\\\\mathcal{L} = \\\\bar{\\\\psi}(i\\\\gamma^\\\\mu D_\\\\mu - m)\\\\psi$')")
    description: str = Field(default="", description="数式の物理的意味・役割の説明")
    variables: list[VariableDefinition] = Field(
        default_factory=list,
        description="数式中の各変数とその物理的意味",
    )
    context: str = Field(default="", description="数式が登場する文脈 (例: 'QEDラグランジアン', 'ファインマンルール導出')")


class PaperStructure(BaseModel):
    """Full extracted structure for a single paper."""

    paper_id: str = Field(description="Unique identifier (e.g. arXiv ID)")
    title: str = Field(default="")
    authors: list[str] = Field(default_factory=list, description="List of author names")
    year: Optional[int] = Field(default=None, description="Publication year")
    domain: str = Field(default="", description="Target academic domain (e.g. 'ecology', 'economics')")
    problem: ProblemStatement = Field(default_factory=ProblemStatement)
    hypothesis: Hypothesis = Field(default_factory=Hypothesis)
    methodology: Methodology = Field(default_factory=Methodology)
    constraints: Constraints = Field(default_factory=Constraints)
    abstract_structure: AbstractStructure = Field(default_factory=AbstractStructure)
    key_equations: list[KeyEquation] = Field(
        default_factory=list,
        description="論文中の重要な数式（LaTeX）とその変数定義のリスト",
    )
    license: str = Field(default="", description="The license of the paper (e.g., from arXiv metadata)")
    review_status: ReviewStatus = Field(default=ReviewStatus.PENDING)
    reviewer_notes: str = Field(default="")


# ---------------------------------------------------------------------------
# Auth & proposal schemas (Private layer)
# ---------------------------------------------------------------------------

class User(BaseModel):
    """A registered user of Episteme Graph."""

    id: str = Field(description="Unique user identifier")
    username: str = Field(description="Display name")
    email: str = Field(description="Email address")


class StructureProposal(BaseModel):
    """A user-submitted proposal to modify a paper's canonical structure."""

    proposal_id: str = Field(description="Unique identifier for this proposal")
    arxiv_id: str = Field(description="arXiv paper identifier the proposal targets")
    user_id: str = Field(description="ID of the proposing user")
    proposed_structure: PaperStructure = Field(description="The proposed PaperStructure")
    status: ReviewStatus = Field(default=ReviewStatus.PENDING, description="Review status of the proposal")
    meta_feedback: str = Field(
        default="",
        description="User's free-text feedback about expression model limitations",
    )


class SystemMetaProposal(BaseModel):
    """LLM が自動生成するシステムレベルのメタ提案。

    ユーザーの meta_feedback を分析し、現在の表現モデル（SMILES DSL）の
    構造的限界に関する体系的な課題を抽出・分類する。
    """

    meta_proposal_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for this meta-proposal",
    )
    category: MetaIssueCategory = Field(
        default=MetaIssueCategory.OTHER,
        description="Classification of the expression model limitation",
    )
    description: str = Field(
        default="",
        description="Detailed description of the expression limitation",
    )
    suggested_solution: str = Field(
        default="",
        description="Proposed approach to address the limitation",
    )
    source_proposal_id: str = Field(
        default="",
        description="ID of the StructureProposal that triggered this meta-proposal",
    )
    arxiv_id: str = Field(
        default="",
        description="arXiv ID of the paper where the limitation was observed",
    )


# ---------------------------------------------------------------------------
# LLM merge result schema (Gateway layer)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Abstraction Pattern schemas (Public layer)
# ---------------------------------------------------------------------------

class AbstractionPattern(BaseModel):
    """A cross-domain problem-solving pattern extracted from a paper."""

    pattern_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for this pattern",
    )
    name: str = Field(description="Short, descriptive name of the pattern")
    description: str = Field(description="Explanation of the pattern in general terms")
    variables_template: list[str] = Field(
        default_factory=list,
        description="Abstract variables (X, Y, Z, …) used in the pattern",
    )
    structural_rules: list[str] = Field(
        default_factory=list,
        description="Rules describing how the variables interact (e.g. 'X inhibits Y')",
    )
    source_arxiv_id: str = Field(
        default="",
        description="arXiv ID of the paper from which this pattern was extracted",
    )
    smarts_regex: str = Field(
        default="",
        description="このパターンを捕捉するためのSMILES DSL正規表現（SMARTS検索用。例: '\\[.*:Agent:.*\\] ==\\[CAUSES:.*\\]=>' ）",
    )
    unresolved_limitations: list[str] = Field(
        default_factory=list,
        description="このパターン化を試みた際にLLMが感じた現行表現の限界（メタ課題の種）",
    )


class PatternMatch(BaseModel):
    """A record that a pattern matches (is isomorphic to) a target paper."""

    match_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for this match",
    )
    pattern_id: str = Field(description="ID of the AbstractionPattern")
    target_arxiv_id: str = Field(description="arXiv ID of the matched paper")
    mapping_explanation: str = Field(
        default="",
        description="Natural-language explanation of how the pattern maps to the paper",
    )
    confidence_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence score of the match (0.0–1.0)",
    )


# ---------------------------------------------------------------------------
# Missing Link Suggestion schemas (v2 feature)
# ---------------------------------------------------------------------------

class FieldSuggestion(BaseModel):
    """A single field suggestion for a Missing Link search."""

    field: str = Field(description="Recommended academic field or domain")
    reasoning: str = Field(description="Why this field might exhibit the same structural pattern")
    keywords: list[str] = Field(
        default_factory=list,
        description="Suggested arXiv search keywords combining pattern structure with field terminology",
    )


class MissingLinkSuggestion(BaseModel):
    """LLM-generated suggestions for structural holes in the Pattern Library."""

    pattern_id: str = Field(description="ID of the source AbstractionPattern")
    pattern_name: str = Field(default="", description="Name of the pattern for display")
    suggestions: list[FieldSuggestion] = Field(
        default_factory=list,
        description="List of field-specific search suggestions",
    )


# ---------------------------------------------------------------------------
# Export schemas (3 Zones + Gateway)
# ---------------------------------------------------------------------------


class DraftEntry(BaseModel):
    """A single draft entry for private backup export."""

    arxiv_id: str = Field(description="arXiv paper identifier")
    structure: PaperStructure = Field(description="Draft PaperStructure")


class ChatHistoryEntry(BaseModel):
    """A single chat history entry for private backup export."""

    arxiv_id: str = Field(description="arXiv paper identifier")
    messages: list[dict] = Field(default_factory=list, description="Chat messages")


class PrivateBackupExport(BaseModel):
    """Private Zone のフルバックアップスキーマ。

    ユーザー個人の生データ（ドラフト、チャット履歴、抽出途中のノード等）を
    すべて保持する。文字数制限なし。外部共有は厳禁。
    """

    user_id: str = Field(description="Exporting user's identifier")
    exported_at: str = Field(description="ISO 8601 timestamp of export")
    drafts: list[DraftEntry] = Field(default_factory=list, description="All user drafts")
    chat_histories: list[ChatHistoryEntry] = Field(
        default_factory=list, description="All user chat histories"
    )


class PublicDSLExport(BaseModel):
    """Public Zone / GitHub 公開用の厳格なエクスポートスキーマ。

    Gateway を通過し、著者の「表現」を完全に除去した純粋な DSL のみを保持する。
    ライセンス汚染（CC BY-NC, ND 等）のレコードは事前に除外済みであること。
    """

    title: str = Field(description="Paper title")
    authors: list[str] = Field(default_factory=list, description="Author names")
    source_url: str = Field(default="", description="Original paper URL")
    doi: str = Field(default="", description="Digital Object Identifier")
    original_license: str = Field(description="License of the original paper")
    smiles_dsl: str = Field(description="SMILES DSL string")
    is_derived_data: bool = Field(
        default=True,
        description="Flag indicating this is derived/extracted data, not original content",
    )
    disclaimer_implementation: str = Field(
        default="本データは論文の論理構造を抽出したものであり、記述された技術の実施（商業利用等）に関する特許等の実施権を保証するものではありません。",
        description="Patent/implementation rights disclaimer",
    )
    context_summary: str = Field(
        default="",
        description="200文字以内の事実の概要（脱表現化済み）",
    )

    @field_validator("context_summary")
    @classmethod
    def truncate_context_summary(cls, v: str) -> str:
        """Enforce the 200-character hard limit for de-expression compliance."""
        if len(v) > 200:
            return v[:199] + "…"
        return v


# ---------------------------------------------------------------------------
# 監査 entity_type カタログ（theory_review_events, 調査レポート Tier2 提案7）
# ---------------------------------------------------------------------------
# ``theory_review_events`` テーブルに entity_type の CHECK 制約はない（各層が
# 独自に追記してきた経緯のため）。ここを唯一の正本（single source of truth）とし、
# 新しい entity_type を追加するときは必ずここに追記すること。
#
# 各層固有の語彙モジュール（core/doubt/schema.py の LEDGER_REVIEW_ENTITY_TYPES,
# core/versioning/schema.py の AUDIT_RELEASE 等, core/reconstruction/schema.py の
# ENTITY_ITEM/ENTITY_RESPONSE）は、既存の参照元を壊さないようここの値を再エクスポート
# する（値の重複定義はしない）。core/ モジュールが本ファイルを import するのは問題ない
# （core → core の依存で、core → api の逆方向にはならない）。

# C層（承認・共有レイヤー）
AUDIT_ENTITY_COMPONENT = "component"
AUDIT_ENTITY_CLAIM = "claim"
AUDIT_ENTITY_EXPLANATION = "explanation"
AUDIT_ENTITY_ENDORSEMENT = "endorsement"
AUDIT_ENTITY_CITATION = "citation"

# B層（違和感・構造帰属型の問い）
AUDIT_ENTITY_TENSION = "tension"
AUDIT_ENTITY_STRUCTURE_ANCHOR = "structure_anchor"

# 分野の地図（Field Atlas）
AUDIT_ENTITY_ATLAS_SKELETON = "atlas_skeleton"
AUDIT_ENTITY_ATLAS_ASSIST = "atlas_assist"
AUDIT_ENTITY_ATLAS_BINDING = "atlas_binding"
AUDIT_ENTITY_ATLAS_REPORT = "atlas_report"

# パイプライン成果のグループ共有 / L層ライブラリ
AUDIT_ENTITY_DOCUMENT_SHARE = "document_share"
AUDIT_ENTITY_LIBRARY_ENTRY = "library_entry"

# 横断ユーティリティ層（Admin Copilot）/ ガイダンス層（G層）
AUDIT_ENTITY_ASSISTANT_ACTION = "assistant_action"
AUDIT_ENTITY_NEXT_STEP = "next_step"

# D層（Doubt Layer）
AUDIT_ENTITY_LEDGER = "ledger"
AUDIT_ENTITY_ASSUMPTION = "assumption"
AUDIT_ENTITY_CHALLENGE = "challenge"
AUDIT_ENTITY_VERIFICATION_PROPOSAL = "verification_proposal"
AUDIT_ENTITY_COUNTERFACTUAL_SESSION = "counterfactual_session"

# 再構成ループ（R層）
AUDIT_ENTITY_RECONSTRUCTION_ITEM = "reconstruction_item"
AUDIT_ENTITY_RECONSTRUCTION_RESPONSE = "reconstruction_response"

# 共有物のバージョン管理（V層）
AUDIT_ENTITY_SHARED_RELEASE = "shared_release"
AUDIT_ENTITY_SHARED_DELETION = "shared_deletion"
AUDIT_ENTITY_SHARED_SUBSCRIPTION = "shared_subscription"

# 教材リビジョン（document_pipeline/revision）
AUDIT_ENTITY_REVISION_RUN = "revision_run"

# W層（要素検討ワークスペース / 同一性リンク, Phase W-β）
AUDIT_ENTITY_DELIBERATION = "deliberation"

# 図表示モード（AI候補 → 教員 reviewed override、issue #496）
AUDIT_ENTITY_FIGURE_PRESENTATION = "figure_presentation"

# 上位・下位概念を活用した説明付与（要素説明の二層台帳, Phase 2, migration 056）
AUDIT_ENTITY_ELEMENT_EXPLANATION = "element_explanation"

# 利用者マニュアル KB（help_kb, Phase 2 手動 refresh トリガー）
AUDIT_ENTITY_MANUAL = "manual"

# discuss 観測基盤（Observation Layer for Phase 3 Gate, migration 060）
AUDIT_ENTITY_DISCUSS_OBSERVATION = "discuss_observation"

# M層（LLM モデル選択, migration 061）
AUDIT_ENTITY_LLM_MODEL_POLICY = "llm_model_policy"

# カタログ本体（新規 entity_type はここへの追記が必須。ガードレールテスト対象）。
AUDIT_ENTITY_TYPES = (
    AUDIT_ENTITY_COMPONENT,
    AUDIT_ENTITY_CLAIM,
    AUDIT_ENTITY_EXPLANATION,
    AUDIT_ENTITY_ENDORSEMENT,
    AUDIT_ENTITY_CITATION,
    AUDIT_ENTITY_TENSION,
    AUDIT_ENTITY_STRUCTURE_ANCHOR,
    AUDIT_ENTITY_ATLAS_SKELETON,
    AUDIT_ENTITY_ATLAS_ASSIST,
    AUDIT_ENTITY_ATLAS_BINDING,
    AUDIT_ENTITY_ATLAS_REPORT,
    AUDIT_ENTITY_DOCUMENT_SHARE,
    AUDIT_ENTITY_LIBRARY_ENTRY,
    AUDIT_ENTITY_ASSISTANT_ACTION,
    AUDIT_ENTITY_NEXT_STEP,
    AUDIT_ENTITY_LEDGER,
    AUDIT_ENTITY_ASSUMPTION,
    AUDIT_ENTITY_CHALLENGE,
    AUDIT_ENTITY_VERIFICATION_PROPOSAL,
    AUDIT_ENTITY_COUNTERFACTUAL_SESSION,
    AUDIT_ENTITY_RECONSTRUCTION_ITEM,
    AUDIT_ENTITY_RECONSTRUCTION_RESPONSE,
    AUDIT_ENTITY_SHARED_RELEASE,
    AUDIT_ENTITY_SHARED_DELETION,
    AUDIT_ENTITY_SHARED_SUBSCRIPTION,
    AUDIT_ENTITY_REVISION_RUN,
    AUDIT_ENTITY_DELIBERATION,
    AUDIT_ENTITY_FIGURE_PRESENTATION,
    AUDIT_ENTITY_ELEMENT_EXPLANATION,
    AUDIT_ENTITY_MANUAL,
    AUDIT_ENTITY_DISCUSS_OBSERVATION,
    AUDIT_ENTITY_LLM_MODEL_POLICY,
)
