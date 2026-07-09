"""D層スキーマ語彙と認識的地位台帳のデータモデル (D1-1)。

「合意の強さ」と「検証の強さ」をデータ構造のレベルで混同不可能にする。
検証は単一ブール値にせず **スコープの配列** で持つ（構想の心臓部）:
「この前提は◯◯の条件・領域・精度・系で検証されている」を 1 件ずつ記帳し、
スコープ 0 件（空欄）を正常状態（=発見）として表現できるようにする。

語彙の正本はこのモジュール。theory_review_events.entity_type の D層拡張語彙
（LEDGER_REVIEW_ENTITY_TYPES）もここで管理する（テーブル側に CHECK 制約はない）。

不変条項（§0, 抜粋）:
  - `directly_verified` は人間の記帳専用。ledger_builder（非LLM バックフィル）や
    LLM 候補経路からは絶対に設定しない。
  - `directly_verified` への昇格はスコープが 1 件以上あるときのみ許可
    （全称検証の構造的禁止, D1-3 の API 層で 422）。
  - LLM スコープ候補（ScopeCandidate）は scope_candidates 列に status='candidate'
    で保持し、教員確定まで verification_scopes 本体に入らない。
  - load_score / confidence の生数値は API/UI に出さない。段階ラベル
    （load_level_label / count range）へ変換して返す。
"""

from __future__ import annotations

import uuid
from enum import Enum

from pydantic import BaseModel, Field

SCHEMA_VERSION = "doubt-layer/v1"

# ---------------------------------------------------------------------------
# 語彙 Enum
# ---------------------------------------------------------------------------


class TargetType(str, Enum):
    """台帳の対象種別。"""

    CLAIM = "claim"
    ASSUMPTION = "assumption"
    EQUATION = "equation"
    COMPONENT = "component"


class VerificationStatus(str, Enum):
    """検証の強さ（合意の強さとは独立の軸）。

    directly_verified は人間の記帳のみで到達できる（自動昇格禁止）。
    「原文に書いてある」≠「検証されている」なので、evidence 付き source_backed
    claim でも indirectly_supported 止まり（ledger_builder）。
    """

    DIRECTLY_VERIFIED = "directly_verified"
    INDIRECTLY_SUPPORTED = "indirectly_supported"
    UNTESTED = "untested"
    REFUTED = "refuted"
    UNKNOWN = "unknown"


# 人間の記帳でのみ設定できる状態（自動処理・LLM 候補経路からの設定は禁止）
HUMAN_ONLY_VERIFICATION_STATUSES = (VerificationStatus.DIRECTLY_VERIFIED,)


class AssumptionOrigin(str, Enum):
    """暗黙前提候補の出所（D2-2 / D3-5 / D1-5 / 手動）。"""

    MINED_GAP = "mined_gap"          # 経路A: 導出の隙間の反転
    MINED_CORPUS = "mined_corpus"    # 経路B: コーパス横断の監査
    NAIVE_AGGREGATE = "naive_aggregate"  # 部品6: 素朴な問いの集約から
    MANUAL = "manual"                # 教員の直接明示化


class AssumptionStatus(str, Enum):
    """前提の確定状態。candidate → confirmed / dismissed は人間のみが遷移させる。"""

    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    OPERATIONALIZED = "operationalized"
    DISMISSED = "dismissed"  # P4: 削除せず保持


class ChallengeType(str, Enum):
    """疑義の型（D3-1）。疑義カードの主語は常に型（人格対立にしない, §8-3）。"""

    SCOPE_EXTRAPOLATION = "scope_extrapolation"    # 検証スコープ外への外挿
    UNTESTED_IN_DOMAIN = "untested_in_domain"      # この領域では未検証
    DEFINITIONAL = "definitional"                  # 定義の曖昧さ・循環
    HIDDEN_LEMMA = "hidden_lemma"                  # 暗黙の補題に依存


class ChallengeStatus(str, Enum):
    OPEN = "open"
    ANSWERED = "answered"
    WITHDRAWN = "withdrawn"              # P4: 行削除ではなく状態遷移
    LED_TO_VERIFICATION = "led_to_verification"  # 検証提案へ昇格済み


class ProposalStatus(str, Enum):
    """検証提案（D3-2）の状態。"""

    PROPOSED = "proposed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    WITHDRAWN = "withdrawn"


class CounterfactualRegion(str, Enum):
    """反実仮想の伝播 3 区分（D3-3）。

    判定不能は崩壊側に倒さず indeterminate で保持する（情報を落とさない）。
    """

    COLLAPSED = "collapsed"
    SURVIVING = "surviving"
    INDETERMINATE = "indeterminate"


# theory_review_events.entity_type の D層拡張語彙（テーブルに CHECK はないため
# ここが正本）。既存語彙: component / claim / explanation / endorsement /
# citation / tension / structure_anchor / atlas_skeleton / atlas_binding など。
LEDGER_REVIEW_ENTITY_TYPES = (
    "ledger",
    "assumption",
    "challenge",
    "verification_proposal",
    "counterfactual_session",
)

# 負荷度の段階ラベル（D2-1）。生数値は DB のみに保持し、API/UI はこの語彙で返す。
LOAD_LEVELS = ("low", "medium", "high", "highest")
LOAD_LEVEL_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "highest": "最高位",
}

# 学習者シグナルの件数レンジ表示（D1-5, k-匿名 k=3: 3 未満は非表示なのでレンジ最小は 3-5）
NAIVE_SIGNAL_COUNT_RANGES = ("3-5", "6-10", "11+")
NAIVE_SIGNAL_K_ANONYMITY = 3


def count_range_label(count: int) -> str:
    """つまずき件数を段階レンジに変換する（生数値を出さない）。

    k-匿名の閾値未満（n < 3）は呼び出し側で行ごと除外すること。
    """
    if count < NAIVE_SIGNAL_K_ANONYMITY:
        return ""
    if count <= 5:
        return "3-5"
    if count <= 10:
        return "6-10"
    return "11+"


# ---------------------------------------------------------------------------
# Pydantic モデル
# ---------------------------------------------------------------------------


class VerificationScope(BaseModel):
    """検証スコープ 1 件 = 「どの条件・領域・精度・系で検証されているか」の記帳。

    condition / domain / precision / system のうち 1 つ以上 + evidence_ids（根拠）
    + reason（理由）が必須（D1-3 の API 層で強制）。帰属（recorded_by）必須。
    """

    scope_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    condition: str = ""   # 例: 「低エネルギー極限で」
    domain: str = ""      # 例: 「電磁相互作用の領域で」
    precision: str = ""   # 例: 「一次摂動の精度で」
    system: str = ""      # 例: 「水素原子系で」
    evidence_ids: list[str] = Field(default_factory=list)
    recorded_by: str = ""  # user_id（帰属必須・匿名記帳なし）
    reason: str = ""
    recorded_at: str = ""

    def has_axis(self) -> bool:
        """4 軸のうち 1 つ以上が埋まっているか。"""
        return bool(
            (self.condition or "").strip()
            or (self.domain or "").strip()
            or (self.precision or "").strip()
            or (self.system or "").strip()
        )


class ScopeCandidate(BaseModel):
    """LLM が提案する検証スコープ **候補**（D1-4）。

    status は candidate / dismissed のみ。「確定」は候補の値を教員帰属で
    VerificationScope に転記する行為であり、候補行自体は昇格しない
    （確定なしに verification_scopes 本体へ入らない、が構造的に守られる）。
    候補は必ず逐語 evidence_quote・reason・confidence を持つ（P5）。
    """

    candidate_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    condition: str = ""
    domain: str = ""
    precision: str = ""
    system: str = ""
    evidence_quote: str = ""   # 出典からの逐語引用（P5, validator で強制）
    reason: str = ""
    confidence: float = 0.0
    status: str = "candidate"  # candidate | dismissed（P4: dismissed も保持）
    detector_version: str = SCHEMA_VERSION
    created_at: str = ""


class EpistemicLedgerRecord(BaseModel):
    """認識的地位台帳の 1 行（UNIQUE(target_id, target_type)）。

    consensus_explicit（明示的合意 = C層承認集計の参照値）と
    consensus_behavioral（行動上の合意 = コーパス内依存頻度）は
    verification_status / verification_scopes（検証の強さ）とは**別の軸**として
    別フィールドで持つ。表示も別々の行で行う（D1-4）。
    """

    id: str = ""
    target_id: str
    target_type: TargetType
    document_id: str = ""
    course_id: str = ""
    verification_status: VerificationStatus = VerificationStatus.UNKNOWN
    verification_scopes: list[VerificationScope] = Field(default_factory=list)
    scope_candidates: list[ScopeCandidate] = Field(default_factory=list)
    consensus_explicit: dict = Field(default_factory=dict)
    consensus_behavioral: int = 0
    # 生数値。API/UI へは load_level（段階ラベル）のみ出す。
    load_score: float | None = None
    load_computed_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    def has_scopes(self) -> bool:
        return len(self.verification_scopes) > 0


class AssumptionRecord(BaseModel):
    """暗黙前提ノード（assumption_nodes, migration 030）。"""

    id: str = ""
    statement: str
    origin: AssumptionOrigin = AssumptionOrigin.MINED_GAP
    status: AssumptionStatus = AssumptionStatus.CANDIDATE
    cluster_key: str = ""
    created_from: dict = Field(default_factory=dict)
    evidence_quote: str = ""
    reason: str = ""
    confidence: float = 0.0
    course_id: str = ""
    document_ids: list[str] = Field(default_factory=list)
    confirmed_by: str = ""
    operationalized_by: str = ""
    created_at: str = ""
    updated_at: str = ""


class ChallengeRecord(BaseModel):
    """疑義（challenges, migration 031）。帰属必須・匿名不可。"""

    id: str = ""
    target_id: str
    target_type: str  # assumption | claim
    challenger_id: str
    challenge_type: ChallengeType
    reason: str  # 本人の言葉。空は 422（API 層で強制 + NOT NULL/CHECK）
    status: ChallengeStatus = ChallengeStatus.OPEN
    course_id: str = ""
    created_at: str = ""
    updated_at: str = ""


class VerificationProposalRecord(BaseModel):
    """検証提案（verification_proposals, migration 032）。"""

    id: str = ""
    challenge_id: str
    proposal: str  # どの実験・どの計算で検証可能か
    proposer_id: str
    status: ProposalStatus = ProposalStatus.PROPOSED
    created_at: str = ""


class CounterfactualResult(BaseModel):
    """反実仮想の伝播計算結果（D3-3）。

    「外した後に何が再構築できるか」は**計算しない**（そこが人間の創造の場所）。
    このモデルは崩れる領域・生き残る領域・判定不能領域の 3 区分のみを表す。
    """

    toggled_assumption_ids: list[str] = Field(default_factory=list)
    collapsed_node_ids: list[str] = Field(default_factory=list)
    surviving_node_ids: list[str] = Field(default_factory=list)
    indeterminate_node_ids: list[str] = Field(default_factory=list)
    graph_snapshot_key: str = ""


# ---------------------------------------------------------------------------
# 段階ラベル変換（数値非表示の原則）
# ---------------------------------------------------------------------------


def load_level_for_score(score: float | None, p50: float, p90: float, p99: float) -> str:
    """load_score 生値 → 段階ラベル。閾値はコーパス内パーセンタイル。

    生値そのものは API レスポンスに含めないこと（DX-1 の契約テスト対象）。
    """
    if score is None:
        return ""
    if score >= p99 and p99 > 0:
        return "highest"
    if score >= p90 and p90 > 0:
        return "high"
    if score >= p50 and p50 > 0:
        return "medium"
    return "low"


def scope_coverage_level(scope_count: int) -> str:
    """検証スコープ被覆の段階（Assumption Atlas の x 軸, D2-3）。"""
    if scope_count <= 0:
        return "none"      # 空欄 = 塗りなし・点線輪郭の点
    if scope_count == 1:
        return "single"
    if scope_count <= 3:
        return "several"
    return "broad"
