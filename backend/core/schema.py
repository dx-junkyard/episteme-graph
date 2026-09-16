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
    # 知識オブジェクト層（knowledge_objects_design.md §7）: dsl_linking の
    # CORE_PREDICATES（10 語彙）と正本を揃える。src 側は非改変で、包含はテストで固定。
    PRODUCES = "PRODUCES"


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

# 誤解メモの本人レビュー（是正 F5 / 六つのレンズ 提案3, 2026-09-10）。
# AI が候補として書いた誤解メモに対する本人の3択（agreed / disagreed / verdict_wrong）を
# candidate → confirmed / dismissed の状態遷移として記帳する（tension と同型）。
# 本人の逐語・訂正文そのものは載せない（metadata は decision / course_id / topic_id のみ）。
AUDIT_ENTITY_MISCONCEPTION = "misconception"

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

# 教材図スタジオ（生成図の採用・retire / 提案の accept・dismiss・生成, migration 063）
AUDIT_ENTITY_TEACHING_FIGURE = "teaching_figure"

# 知識ランドスケープ（論文 ⇄ 基準地図の配置。status 遷移・手動再提案, migration 065）
AUDIT_ENTITY_LANDSCAPE_PLACEMENT = "landscape_placement"

# カテゴリギャップ候補（検出 detect / 採用 accept / 見送り dismiss / 復帰 restore /
# 統合 merge / 下書き取り込み incorporate。metadata.action で区別, migration 066）
AUDIT_ENTITY_CATEGORY_GAP = "category_gap"

# アカウントライフサイクル管理（停止 suspend / 再開 restore / パスワードリセット
# password_reset / 削除予約 schedule_deletion・取消 cancel_deletion / purge / 所有物移管
# transfer_ownership。metadata.action で区別, migration 068）
AUDIT_ENTITY_USER_ACCOUNT = "user_account"

# URL指定による教材取得の取得先ドメイン許可リスト（登録 create / 解除 delete。
# metadata.action で区別, migration 070）
AUDIT_ENTITY_URL_FETCH_DOMAIN = "url_fetch_domain"

# 論文ディスカバリー層（arXiv 分野購読。購読の作成・更新 subscribe / 取り込み実行 ingest /
# 見送り dismiss / 復帰 restore。metadata.action で区別, migration 071）
AUDIT_ENTITY_PAPER_DISCOVERY = "paper_discovery"

# 分野マップのベクトル係留層（アンカーベクトルの再構築 vectors_refresh / 別名の登録
# alias_register・見送り alias_dismiss。metadata.action で区別, migration 074）
AUDIT_ENTITY_ATLAS_VECTOR = "atlas_vector"

# 分野マップの辺候補レビュー（atlas_relation_edges_design.md §8、migration 076）。
# 辺候補の accept / dismiss / restore / mark_incorporated を記帳する。
AUDIT_ENTITY_ATLAS_EDGE = "atlas_edge"

# 開示範囲（visibility）の変更 — 教材（documents）とコース（learning_courses）の
# public / group / private 切替。entity_id は material_id または course_id、
# old_status / new_status に旧・新 visibility を入れる（metadata.object_type で区別）。
# 公開は取り消しの効かない操作（一度出た資料は戻らない）なので、誰がいつどこへ開いたかを
# 記帳する（原則14 監査可能性）。平文の資料本文・受講者情報は載せない。
AUDIT_ENTITY_VISIBILITY = "visibility"

# 教材の物理削除 — `DELETE /api/admin/materials/{material_id}`。教材本体とチャンク・
# 解析成果・巻き添えコースを DB から実際に消す**不可逆**な操作で、V層の版・購読の
# 後始末（teardown_versioning）はその後に走る。原則14（監査可能性）の穴として
# 六つのレンズ 是正 F11 で塞いだ。entity_id は material_id、new_status="deleted"、
# metadata に document_id / 巻き添えで消えたコース id を入れる（資料本文・タイトルは
# 載せない — 監査は「誰が何を消したか」であって内容の写しではない）。
AUDIT_ENTITY_MATERIAL = "material"

# 原稿スタジオのコーストピック保存 — `PUT /api/admin/courses/{id}/lecture-studio/
# course-topics/{topic_id}`。学習者に配信される授業用教材・読み上げ原稿を上書きし、
# 副作用として当該トピックの生成済み音声を無効化する（是正 F11）。entity_id は
# course_id、metadata に topic_id / 変更されたフィールド名の列挙を入れる
# （本文そのものは載せない）。
AUDIT_ENTITY_COURSE_TOPIC = "course_topic"

# 外部への書き出し（export bundle）— `POST /api/{courses|documents}/{id}/export-bundle`。
# 束は PDF 逐語の evidence スニペットと（オプションで）LLM 生出力を含んだまま
# システムの外へ出て行き戻ってこないため、誰がいつ何を持ち出したかを記帳する
# （六つのレンズ 提案4 = 是正 F10。entity_id は course_id / document_id、
# new_status="exported"、metadata に export_id / object_type / document_ids /
# options を入れる。資料本文・逐語引用そのものは監査に載せない）。
AUDIT_ENTITY_EXPORT = "export"

# 知識オブジェクト層（knowledge_objects_design.md KO10）: 再解析の supersede / 参照の
# 再係留 / 語彙外型の丸めを記帳する。entity_id は document_id（run 単位の要約1行）。
AUDIT_ENTITY_KNOWLEDGE_OBJECT = "knowledge_object"

# 知識の転用層（knowledge_transfer_design.md KT8 / P4-1）: export bundle の取り込み。
# entity_id は取り込み先 document_id、new_status="imported"、metadata に export_id /
# 出所 document_ids / 束の sha256 / 件数（sync 統計）を入れる。束の本文・逐語引用は
# 監査に載せない。取り込んだ人は束の中には書かず、この行の changed_by だけに残る。
AUDIT_ENTITY_IMPORT = "import"

# カタログ本体（新規 entity_type はここへの追記が必須。ガードレールテスト対象）。
AUDIT_ENTITY_TYPES = (
    AUDIT_ENTITY_COMPONENT,
    AUDIT_ENTITY_CLAIM,
    AUDIT_ENTITY_EXPLANATION,
    AUDIT_ENTITY_ENDORSEMENT,
    AUDIT_ENTITY_CITATION,
    AUDIT_ENTITY_TENSION,
    AUDIT_ENTITY_STRUCTURE_ANCHOR,
    AUDIT_ENTITY_MISCONCEPTION,
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
    AUDIT_ENTITY_TEACHING_FIGURE,
    AUDIT_ENTITY_LANDSCAPE_PLACEMENT,
    AUDIT_ENTITY_CATEGORY_GAP,
    AUDIT_ENTITY_USER_ACCOUNT,
    AUDIT_ENTITY_URL_FETCH_DOMAIN,
    AUDIT_ENTITY_PAPER_DISCOVERY,
    AUDIT_ENTITY_ATLAS_VECTOR,
    AUDIT_ENTITY_ATLAS_EDGE,
    AUDIT_ENTITY_VISIBILITY,
    AUDIT_ENTITY_MATERIAL,
    AUDIT_ENTITY_COURSE_TOPIC,
    AUDIT_ENTITY_EXPORT,
    AUDIT_ENTITY_KNOWLEDGE_OBJECT,
    AUDIT_ENTITY_IMPORT,
)


# ---------------------------------------------------------------------------
# 知識オブジェクト層の型語彙（knowledge_objects_design.md §7 / KO7）
#
# DB 側は CHECK ではなく語彙表（knowledge_claim_types / knowledge_component_types）への
# FK で守り、その語彙表は migration が **ここと同じ列挙** をシードする（一致は
# test_knowledge_objects_vocab.py が固定）。新しい型を足すときはここに足し、同じ値を
# migration の seed にも足す。LLM の自称は claim_type_text / component_type_text に残す。
# ---------------------------------------------------------------------------

#: theory_claims.claim_type の語彙（旧 CHECK 17 ∪ claim_object_builder の CLAIM_TYPE_ONTOLOGY
#: ∪ equation_claim_synthesis の 4 型 ∪ unknown）。
CLAIM_TYPES: tuple[str, ...] = (
    "definition", "assumption", "approximation", "equation", "relation",
    "derivation_step", "observable_definition", "correction", "uncertainty",
    "limitation", "result", "diagnostic_claim", "equation_definition",
    "equation_relation", "equation_transformation", "equation_approximation",
    "equation_constraint", "criterion", "setup", "operator_relation",
    "measurement_or_update", "causal_or_dependency_claim",
    "incompatibility_or_constraint", "comparison", "conclusion", "method_choice",
    "background", "prior_work", "meta", "problem_statement", "method_motivation",
    "theory_encoding", "method", "structural_property", "derivation_result",
    "main_result", "interpretation",
    # 式由来の合成 claim（src/episteme_graph/agents/claim_object_builder/
    # equation_claim_synthesis.py が出す 4 型）。語彙に無かったため実データで 84 件が
    # 全部 unknown に丸められていた（2026-09-13 の実データ検証 V-4）。
    "definition_claim", "dependency_claim", "equation_system_claim", "result_claim",
    "unknown",
)

#: claim の階層（claim_qualification.schema.CLAIM_TIERS と同じ列挙・src 側非改変）。
CLAIM_TIERS: tuple[str, ...] = ("paper_core", "paper_supporting", "background", "prior_work", "meta")

#: theory_components.component_type の語彙（旧 CHECK 9 ∪ cartridge component_types.json ∪ unknown）。
COMPONENT_TYPES: tuple[str, ...] = (
    "theory", "concept", "law", "mechanism", "operator", "observation",
    "apparatus", "instrument", "part",
    "DomainConceptComponent", "DomainTheoryComponent", "DomainMethodComponent",
    "DomainAssumptionComponent", "DomainObservableComponent", "PaperClaimComponent",
    "PaperHypothesisComponent", "PaperRelationComponent", "PaperCorrectionComponent",
    "PaperUncertaintyComponent", "PaperEvidenceComponent", "unknown",
)

#: theory_claims.origin — この claim 行がどの経路で生まれたか（§5.4）。
CLAIM_ORIGIN_SPAN = "span"
CLAIM_ORIGIN_CLAIM_OBJECT = "claim_object"
CLAIM_ORIGIN_ATOMIC_REWRITE = "atomic_rewrite"
CLAIM_ORIGIN_EQUATION_SYNTHESIS = "equation_synthesis"
CLAIM_ORIGINS: tuple[str, ...] = (
    CLAIM_ORIGIN_SPAN,
    CLAIM_ORIGIN_CLAIM_OBJECT,
    CLAIM_ORIGIN_ATOMIC_REWRITE,
    CLAIM_ORIGIN_EQUATION_SYNTHESIS,
)

#: element_id_remap.object_kind / stable_key の種別接頭辞。
KNOWLEDGE_OBJECT_KINDS: tuple[str, ...] = (
    "claim", "component", "equation", "evidence", "derivation_step", "symbol",
)

#: 学ぶ単位（learning_units.unit_kind）の語彙（learning_units_design.md §4 / 親文書 P2-1）。
#: DB は CHECK ではなく語彙表 ``knowledge_unit_kinds`` への FK（KO7 と同じ作法）。
#: - section_block: paper_skeleton の logical_block（章立ての論理ブロック）
#: - thesis_support: thesis_reconstruction の central_thesis / support_structure の 1 項
#: - parent_component: component_assembly の LLM 原案 component（決定論分割前の親）
#: - dsl_node: dsl_linking のノード（Phase 3 概念レジストリの材料。コースビルダーには出さない）
#: - figure: figure_table_semantics の図
LEARNING_UNIT_KINDS: tuple[str, ...] = (
    "section_block", "thesis_support", "parent_component", "dsl_node", "figure",
)

#: 学ぶ単位の確定状態（人間の確定列・supersede 時に保持）。candidate 始まり・行削除なし。
LEARNING_UNIT_REVIEW_STATUSES: tuple[str, ...] = ("candidate", "confirmed", "dismissed")

#: コースビルダーへ候補として提示する unit 種別（dsl_node は概念レジストリ側の材料で、
#: 章立て候補としては粒度が細かすぎるため出さない）。
LEARNING_UNIT_KINDS_FOR_COURSE: tuple[str, ...] = (
    "section_block", "thesis_support", "parent_component", "figure",
)


# ---------------------------------------------------------------------------
# 概念レジストリの語彙（concept_registry_design.md §4.1 / KR2・KR4）
#
# DB 側は CHECK ではなく語彙表（knowledge_entry_types / knowledge_label_kinds /
# knowledge_relation_kinds / knowledge_mapping_justifications）への FK で守り、その
# 語彙表は migration 082 が **ここと同じ列挙** をシードする（一致は
# test_concept_registry_vocab.py が固定）。日本語ラベルの正本は
# core/library/schema.py の 5 表（フロントは逐語ミラー）。
#
# 新しい監査 entity_type は作らない — 概念レジストリの記帳は既存の
# AUDIT_ENTITY_LIBRARY_ENTRY を流用する（action 語彙は core/library/schema.py の
# REGISTRY_AUDIT_ACTIONS）。
# ---------------------------------------------------------------------------

#: library_entries.entry_type の語彙（既存2 + 概念レジストリの7）。
#: core/library/schema.py::ENTRY_TYPES はここからの再エクスポート（二重定義しない）。
LIBRARY_ENTRY_TYPES: tuple[str, ...] = (
    "apparatus", "theory_component", "concept", "theory", "method",
    "observable", "assumption", "quantity", "process",
)

#: library_entry_labels.kind（SKOS の prefLabel / altLabel / hiddenLabel）。
#: preferred は library_entries.name が正本なので**行にしない**（語彙としては持つ）。
CONCEPT_LABEL_KINDS: tuple[str, ...] = ("preferred", "alternate", "hidden")

#: library_entry_relations.kind / library_atlas_node_links.kind（SKOS の broader /
#: related / exactMatch / closeMatch）。node リンクは exact_match / close_match のみを
#: コード側（core/library/registry.py）が強制する。
CONCEPT_RELATION_KINDS: tuple[str, ...] = (
    "broader", "related", "exact_match", "close_match",
)

#: 「なぜ同じと言えたか」の語彙（KR4）。候補・確定の書き込みは必ずこれを伴う。
#: llm_candidate は「既存の LLM 由来候補に付ける語彙」であって、本層が LLM を呼ぶ
#: ことではない（KR5: 概念レジストリのコードは core.llm を import しない）。
MAPPING_JUSTIFICATIONS: tuple[str, ...] = (
    "manual_curation", "lexical_match", "vector_similarity",
    "cartridge_declared", "corpus_cooccurrence", "llm_candidate",
)


# ---------------------------------------------------------------------------
# 引用の意図（knowledge_transfer_design.md §8 / X-7・CiTO の最小語彙）
#
# `component_citations.citation_intent`（migration 083）の許可語彙。教員が引用時に
# 任意で選ぶ。NULL = 記録なし（既存行は推測で埋めない）。日本語ラベルは
# `core/label_vocab.py::CITATION_INTENT_LABELS`。
# ---------------------------------------------------------------------------
CITATION_INTENTS: tuple[str, ...] = (
    "uses_as_evidence",      # 根拠として使う
    "extends",               # 発展させる
    "qualifies",             # 条件を付ける・限定する
    "contrasts_with",        # 対比する
    "cites_for_background",  # 背景として引く
)

#: library_entries.review_status（候補 → 教員の確定。status='retired' とは別軸）。
#: candidate の行は凍結できない（= パイプライン retrieval にも学習者にも届かない。KR2）。
CONCEPT_REVIEW_STATUSES: tuple[str, ...] = ("candidate", "confirmed", "dismissed")
