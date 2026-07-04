"""分野の地図 — 状態導出バッチと atlas_overlay_cache (Issue E-1 / E-2)。

3層モデル(仕様書 §9.1)の C: コーパス層。骨格(カートリッジ同梱)の上に、
既存データからの**近似導出**で「灯り・状態」を重ね、キャッシュへ差分更新する。

設計上の重要事項:

- **導出規則の隔離**: 状態は既存テーブル(theory_components / theory_claims /
  theory_component_graphs / component_explanations / component_endorsements /
  document_analysis_runs)からの近似で開始する。将来 D-1 台帳へ置き換わる前提で、
  導出規則(§10)はこのモジュールに一箇所隔離する。
- **サーバ側で導出**: 状態判定ロジックをフロントへ複製しない。クライアントは描画のみ。
- **リアルタイム LLM 生成をしない**: 実行時は「キャッシュ読み出し + 個人層合成」のみ。
  このモジュールの refresh はイベント駆動の差分バッチとして走る。
- **評価語の排除**: 検証行・承認行は台帳由来の事実のみをテンプレート合成する。
  `EVALUATIVE_VOCABULARY` による静的チェックをテストで強制する(受け入れ条件6)。
- 本モジュールは FastAPI を import しない(テスタビリティ確保)。SQL 実行は
  session を引数で受け取り、commit は呼び出し側が行う(core/atlas_reports.py と同方針)。

未決事項の確定(issue E 内で決める、とされていたもの):

- §16-1: `assumed` と `contested` の併発は **assumed 優先**(導出順位で先に評価する。
  点線=暗黙の前提が主役のため)。
- §16-2: 行間(`inferred`)の蓄積が **3件** に達したら `assumed` 候補へ昇格させる
  (`ASSUMED_INFERRED_THRESHOLD`)。
- 埋め込み割当の距離閾値は core/atlas_placement.DISTANCE_THRESHOLD (0.5)。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import bindparam
from sqlalchemy import text as sa_text

from core import atlas_placement

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 状態語彙 (§10)
# ---------------------------------------------------------------------------

STATUS_FOG = "fog"              # 領域: コーパス被覆ゼロ
STATUS_LIT = "lit"              # 領域: 灯りあり
STATUS_GAP = "gap"              # 行間: inferred かつ原文対応なし
STATUS_ASSUMED = "assumed"      # 暗黙の前提
STATUS_CONTESTED = "contested"  # 解釈が分かれる
STATUS_VERIFIED = "verified"    # ソースバッキング + evidence
STATUS_UNKNOWN = "unknown"      # コーパス由来の状態なし (seed も無い)

NODE_STATUSES = (STATUS_GAP, STATUS_ASSUMED, STATUS_CONTESTED, STATUS_VERIFIED, STATUS_UNKNOWN)

STATUS_SOURCE_DERIVED = "derived"
STATUS_SOURCE_SEED = "seed"

# §16-2: 何件の inferred (行間) の蓄積で assumed 候補へ昇格させるか
ASSUMED_INFERRED_THRESHOLD = 3

# 前提・近似系の claim_type (これしか evidence が無い概念は「直接検証なし」とみなす)
_ASSUMPTION_CLAIM_TYPES = frozenset(
    {"assumption", "approximation", "limitation", "uncertainty"}
)

# 検証行・承認行のテンプレートから排除する評価語 (受け入れ条件6の静的チェック対象)。
# 台帳由来の事実(件数・式番号・節・承認者数)のみを述べ、評価・誘導をしない。
EVALUATIVE_VOCABULARY = (
    "重要",
    "重大",
    "素晴らし",
    "優れ",
    "優秀",
    "秀逸",
    "すごい",
    "推奨",
    "おすすめ",
    "お勧め",
    "べき",
    "簡単",
    "容易",
    "画期的",
    "強力",
    "最先端",
    "必須",
    "注目",
    "人気",
)


def find_evaluative_language(text: str) -> list[str]:
    """テキストに含まれる評価語を返す (空リスト = 合格)。"""
    return [word for word in EVALUATIVE_VOCABULARY if word in (text or "")]


# 詳細パネルの状態ピル文言 (§5 / §12)
PILL_LABELS = {
    STATUS_VERIFIED: "原文に裏付け",
    STATUS_CONTESTED: "解釈が分かれる",
    STATUS_ASSUMED: "暗黙の前提",
    STATUS_GAP: "行間 — AIが補完",
    STATUS_FOG: "論文未投入",
    STATUS_UNKNOWN: "記帳なし",
    "unvisited": "未訪問",
    "now": "いまここ",
}

# 霧領域の定型文 (§6.2 — 事実の提示のみ。投入の指示・催促はしない)
FOG_VERIFY_LINE = "この領域はAIの一般知識による地形。台帳に根拠の記帳がありません。"
FOG_ENDORSE_LINE = "論文を追加すると、この領域に灯りがつきます。"

# 行間ステップの定型文 (§12)
GAP_VERIFY_LINE = "原文に対応する記述が見つからず、導出を繋ぐためにAIが補完した行間です。"
GAP_ENDORSE_LINE = "先生に聞くポイント。確定すると暗黙前提の候補に昇格できます。"


# ---------------------------------------------------------------------------
# シグナル (既存データからの近似観測値)
# ---------------------------------------------------------------------------


@dataclass
class ConceptSignals:
    """1ノード分の台帳近似シグナル。導出規則 derive_node_status() の入力。"""

    key: str
    label: str = ""
    # コーパス被覆
    component_count: int = 0        # 紐づく theory_components 数
    has_binding: bool = False       # レビュー済み concept_binding の有無
    # 検証 (D-1 台帳の近似)
    evidence_count: int = 0         # source_backed かつ evidence_text 非空の claim 数
    evidence_refs: tuple[str, ...] = ()   # 式(12)・§3.2 等の台帳由来参照
    direct_verification: bool = False     # 前提・近似以外の型で evidence が記帳されている
    scope_text: str = ""            # 台帳スコープ欄 (source_scope 由来)
    # 行間・前提
    inferred_count: int = 0         # 依存する行間 (inferred ステップ) の蓄積
    inferred_without_source: bool = False  # このステップ自体が inferred (L3 の行間)
    confirmed_assumption: bool = False     # 確定された前提候補 (教員確定済み assumption)
    dependent_count: int = 0        # この概念に依存する導出ステップ数
    # C層 (解釈・承認)
    standard_explanation: bool = False
    personal_shared_count: int = 0  # 共有された教員独自解釈の数 (標準説明を除く)
    author_labels: tuple[str, ...] = ()
    endorser_count: int = 0
    strong_count: int = 0
    expertise_breadth: int = 0
    endorsement_split: bool = False  # 承認が複数の説明に割れている
    # 骨格 seed (reviewed のみ渡すこと)
    seed_status: str | None = None

    @property
    def has_corpus_state(self) -> bool:
        """コーパス由来の状態が導出できるか。無ければ seed の弱い初期表示へ。"""
        return (
            self.component_count > 0
            or self.evidence_count > 0
            or self.inferred_count > 0
            or self.inferred_without_source
            or self.confirmed_assumption
            or self.personal_shared_count > 0
            or self.endorser_count > 0
        )


# ---------------------------------------------------------------------------
# 導出規則 (§10) — 優先順位は上から評価し最初の該当を採用
# ---------------------------------------------------------------------------


def derive_node_status(signals: ConceptSignals) -> tuple[str, str]:
    """ノード状態を導出する。返り値は (status, status_source)。

    優先順位 (E-1):
      1. fog は領域単位 (derive_region_status) — ここでは扱わない
      2. gap: 該当ステップが inferred かつ原文対応なし
      3. assumed: 行間の蓄積 or 確定された前提候補 + 記帳された直接検証なし
      4. contested: C層の複数解釈の並存 or 承認の割れ
      5. verified: ソースバッキング + evidence あり
    §16-1: assumed と contested の併発は assumed 優先 (評価順で保証)。
    コーパス由来の状態が無い概念にのみ、reviewed な seed を弱い初期表示に使う。
    """
    if signals.inferred_without_source and not signals.evidence_count:
        return (STATUS_GAP, STATUS_SOURCE_DERIVED)
    if not signals.direct_verification and (
        signals.confirmed_assumption
        or signals.inferred_count >= ASSUMED_INFERRED_THRESHOLD
    ):
        return (STATUS_ASSUMED, STATUS_SOURCE_DERIVED)
    if signals.personal_shared_count >= 2 or signals.endorsement_split:
        return (STATUS_CONTESTED, STATUS_SOURCE_DERIVED)
    if signals.evidence_count > 0:
        return (STATUS_VERIFIED, STATUS_SOURCE_DERIVED)
    if not signals.has_corpus_state and signals.seed_status:
        # seed_status の適用規則: コーパス由来の状態が無い概念にのみ弱い初期表示。
        # コーパス状態が付いたら上書きされる (上の分岐が先に該当する)。
        return (signals.seed_status, STATUS_SOURCE_SEED)
    return (STATUS_UNKNOWN, STATUS_SOURCE_DERIVED)


def derive_region_status(coverage: int) -> str:
    """領域状態: コーパス被覆 = 0 (concept_bindings も紐づく概念も無い) → fog。"""
    return STATUS_LIT if coverage > 0 else STATUS_FOG


# ---------------------------------------------------------------------------
# 検証行・承認行のテキスト生成 (§6.1) — 台帳由来の事実のみをテンプレート合成
# ---------------------------------------------------------------------------


def _refs_fragment(refs: tuple[str, ...], limit: int = 2) -> str:
    if not refs:
        return ""
    shown = "・".join(refs[:limit])
    suffix = " ほか" if len(refs) > limit else ""
    return f"（{shown}{suffix}）"


def verify_line_for(status: str, signals: ConceptSignals) -> str:
    """検証行: 裏付け原文数・該当式/節・スコープ・「記帳なし」等の事実のみ。"""
    if status == STATUS_GAP:
        return GAP_VERIFY_LINE
    if status == STATUS_ASSUMED:
        parts = ["検証: 記帳された直接検証なし。"]
        if signals.dependent_count > 0:
            parts.append(f"{signals.dependent_count}件の導出が依存。")
        elif signals.inferred_count > 0:
            parts.append(f"行間の補完が{signals.inferred_count}件蓄積。")
        return "".join(parts)
    if status == STATUS_CONTESTED:
        n = max(signals.personal_shared_count, 2 if signals.endorsement_split else 0)
        line = f"検証: {n}系統の解釈が併存。"
        if signals.evidence_count > 0:
            line += f"原文{signals.evidence_count}本に記帳あり{_refs_fragment(signals.evidence_refs)}。"
        return line
    if status == STATUS_VERIFIED:
        line = f"検証: 原文{signals.evidence_count}本に裏付け{_refs_fragment(signals.evidence_refs)}。"
        if signals.scope_text:
            line += f"スコープ: {signals.scope_text}。"
        return line
    if signals.seed_status and not signals.has_corpus_state:
        return "検証: 台帳に記帳なし。骨格（教員レビュー済）の初期表示。"
    return "検証: 台帳に記帳がありません。"


def endorse_line_for(status: str, signals: ConceptSignals) -> str:
    """承認行: C層由来の事実のみ (承認者数・専門分野数・解釈の並存・記録なし)。"""
    if status == STATUS_GAP:
        return GAP_ENDORSE_LINE
    if status == STATUS_CONTESTED:
        n = max(signals.personal_shared_count + (1 if signals.standard_explanation else 0), 2)
        return f"承認: {n}つの解釈が帰属つきで並存"
    if signals.endorser_count > 0:
        # level='strong' は C層台帳の語彙 (component_endorsements.level) の表示
        prefix = "強い支持 — " if signals.strong_count > 0 else ""
        line = f"承認: {prefix}{signals.endorser_count}名の教員が承認"
        if signals.expertise_breadth >= 2:
            line += f"（専門{signals.expertise_breadth}分野）"
        return line
    if signals.personal_shared_count >= 1 and signals.standard_explanation:
        authors = "・".join(signals.author_labels[:2]) or "教員"
        return f"承認: 標準の説明と{authors}の説明が並存"
    if signals.standard_explanation or signals.personal_shared_count >= 1:
        return "承認: 標準の説明"
    if status == STATUS_ASSUMED:
        return "承認: 承認記録なし — 台帳のスコープ欄は空欄"
    return "承認: 承認記録なし"


def actions_for(status: str) -> tuple[bool, bool]:
    """(learn, evid): §6.2 — 霧・行間では「ここから学ぶ」「根拠を見る」を出さない。"""
    if status in (STATUS_FOG, STATUS_GAP):
        return (False, False)
    return (True, True)


# ---------------------------------------------------------------------------
# 既存データからのシグナル収集 (近似導出。D-1 台帳への置き換え点)
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return "".join(str(text or "").lower().split()).replace("・", "").replace("　", "")


def _as_json(value: Any) -> Any:
    """JSONB 列: psycopg2 は dict/list、FakeSession/テキスト経路は str で返るため両対応。"""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


_COMPONENTS_SQL = """
    SELECT DISTINCT tc.id::text, tc.name, tc.document_id, tc.review_status,
           tc.status, tc.evidence_claims, tc.source_scope
      FROM theory_components tc
      JOIN document_analysis_runs r
        ON r.document_id = tc.document_id
       AND r.cartridge_id = :cartridge_id
       AND r.status = 'completed'
     ORDER BY tc.id::text
"""

_CLAIMS_SQL = """
    SELECT DISTINCT c.id::text, c.document_id, c.claim_type, c.concepts, c.equation,
           c.support_status, c.evidence_text, c.review_status, c.source_scope
      FROM theory_claims c
      JOIN document_analysis_runs r
        ON r.document_id = c.document_id
       AND r.cartridge_id = :cartridge_id
       AND r.status = 'completed'
     ORDER BY c.id::text
"""

_GRAPHS_SQL = """
    SELECT DISTINCT g.document_id, g.graph_json
      FROM theory_component_graphs g
      JOIN document_analysis_runs r
        ON r.document_id = g.document_id
       AND r.cartridge_id = :cartridge_id
       AND r.status = 'completed'
     ORDER BY g.document_id
"""

_EXPLANATIONS_SQL = """
    SELECT e.component_id::text, e.id::text, e.kind, e.shared, e.review_status,
           COALESCE(u.username, '') AS author_name,
           COALESCE(s.endorser_count, 0) AS endorser_count,
           COALESCE(s.strong_count, 0) AS strong_count,
           COALESCE(s.expertise_breadth, 0) AS expertise_breadth
      FROM component_explanations e
      LEFT JOIN users u ON u.id = e.author_id
      LEFT JOIN component_explanation_endorsement_summary s ON s.explanation_id = e.id
     WHERE e.component_id IN (
           SELECT tc.id FROM theory_components tc
             JOIN document_analysis_runs r
               ON r.document_id = tc.document_id
              AND r.cartridge_id = :cartridge_id
              AND r.status = 'completed'
       )
     ORDER BY e.id::text
"""

_VECTORS_SQL = """
    SELECT DISTINCT tc.id::text, ch.embedding::text
      FROM theory_components tc
      JOIN document_analysis_runs r
        ON r.document_id = tc.document_id
       AND r.cartridge_id = :cartridge_id
       AND r.status = 'completed'
      LEFT JOIN chunks ch ON ch.id = tc.primary_chunk_id
     ORDER BY tc.id::text
"""


@dataclass
class CorpusSnapshot:
    """1カートリッジ分の既存データ近似スナップショット。"""

    components: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    graphs: list[dict] = field(default_factory=list)
    explanations: list[dict] = field(default_factory=list)
    vectors: dict[str, list[float]] = field(default_factory=dict)


def load_corpus_snapshot(session, cartridge_id: str) -> CorpusSnapshot:
    """導出に使う既存データを読み出す。テーブル欠損などは空として扱う(非致命)。"""
    snapshot = CorpusSnapshot()

    def _rows(sql: str) -> list:
        try:
            return session.execute(sa_text(sql), {"cartridge_id": cartridge_id}).fetchall()
        except Exception:  # noqa: BLE001
            logger.warning("atlas snapshot query failed (treated as empty)", exc_info=True)
            return []

    for row in _rows(_COMPONENTS_SQL):
        snapshot.components.append(
            {
                "id": row[0],
                "name": row[1] or "",
                "document_id": row[2] or "",
                "review_status": row[3] or "",
                "status": row[4] or "",
                "evidence_claims": _as_json(row[5]) or [],
                "source_scope": _as_json(row[6]) or {},
            }
        )
    for row in _rows(_CLAIMS_SQL):
        snapshot.claims.append(
            {
                "id": row[0],
                "document_id": row[1] or "",
                "claim_type": row[2] or "",
                "concepts": _as_json(row[3]) or [],
                "equation": _as_json(row[4]) or {},
                "support_status": row[5] or "",
                "evidence_text": row[6] or "",
                "review_status": row[7] or "",
                "source_scope": _as_json(row[8]) or {},
            }
        )
    for row in _rows(_GRAPHS_SQL):
        graph = _as_json(row[1]) or {}
        if isinstance(graph, dict):
            snapshot.graphs.append({"document_id": row[0] or "", "graph": graph})
    for row in _rows(_EXPLANATIONS_SQL):
        snapshot.explanations.append(
            {
                "component_id": row[0],
                "explanation_id": row[1],
                "kind": row[2] or "",
                "shared": bool(row[3]),
                "review_status": row[4] or "",
                "author_name": row[5] or "",
                "endorser_count": int(row[6] or 0),
                "strong_count": int(row[7] or 0),
                "expertise_breadth": int(row[8] or 0),
            }
        )
    for row in _rows(_VECTORS_SQL):
        vector = atlas_placement.parse_vector(row[1])
        if vector:
            snapshot.vectors[row[0]] = vector
    return snapshot


def _claim_concept_labels(claim: dict) -> list[str]:
    labels: list[str] = []
    for entry in claim.get("concepts") or []:
        if isinstance(entry, str):
            labels.append(entry)
        elif isinstance(entry, dict):
            labels.append(str(entry.get("label") or entry.get("name") or entry.get("id") or ""))
    return [l for l in labels if l]


def _claim_refs(claim: dict) -> list[str]:
    """claim から台帳由来の参照 (式番号・節) を取り出す。無ければ空。"""
    refs: list[str] = []
    equation = claim.get("equation") or {}
    if isinstance(equation, dict):
        eq_no = equation.get("eq_no") or equation.get("number") or equation.get("label")
        eq_id = equation.get("equation_id") or equation.get("id")
        if eq_no:
            refs.append(f"式({eq_no})")
        elif eq_id:
            refs.append(str(eq_id))
    scope = claim.get("source_scope") or {}
    if isinstance(scope, dict):
        section = scope.get("section") or scope.get("section_id")
        if section:
            text = str(section)
            refs.append(text if text.startswith("§") else f"§{text}")
    return refs


def _graph_nodes(snapshot: CorpusSnapshot) -> list[dict]:
    nodes: list[dict] = []
    for entry in snapshot.graphs:
        for node in (entry["graph"].get("nodes") or []):
            if isinstance(node, dict):
                node = dict(node)
                node["_document_id"] = entry["document_id"]
                nodes.append(node)
    return nodes


def build_concept_signals(
    snapshot: CorpusSnapshot,
    *,
    key: str,
    label: str,
    aliases: tuple[str, ...] = (),
    component_ids: tuple[str, ...] = (),
    has_binding: bool = False,
    seed_status: str | None = None,
) -> ConceptSignals:
    """骨格概念 or コーパス概念1つ分のシグナルを既存データから集計する。

    マッチングは近似: (a) 明示された component_ids、(b) 正規化ラベル一致、
    (c) claim.concepts のラベル一致。D-1 台帳が入ったらこの関数ごと置き換える。
    """
    targets = {_normalize(label)} | {_normalize(a) for a in aliases}
    targets.discard("")

    matched_components = []
    explicit = set(component_ids)
    for comp in snapshot.components:
        if comp["status"] == "rejected" or comp["review_status"] == "rejected":
            continue
        if comp["id"] in explicit or _normalize(comp["name"]) in targets:
            matched_components.append(comp)
    matched_component_ids = {c["id"] for c in matched_components}
    evidence_claim_ids: set[str] = set()
    for comp in matched_components:
        for cid in comp.get("evidence_claims") or []:
            evidence_claim_ids.add(str(cid))

    matched_claims = []
    for claim in snapshot.claims:
        if claim["id"] in evidence_claim_ids:
            matched_claims.append(claim)
            continue
        if targets & {_normalize(l) for l in _claim_concept_labels(claim)}:
            matched_claims.append(claim)

    evidence_claims = [
        c
        for c in matched_claims
        if c["support_status"] == "source_backed" and (c["evidence_text"] or "").strip()
    ]
    refs: list[str] = []
    for claim in evidence_claims:
        for ref in _claim_refs(claim):
            if ref not in refs:
                refs.append(ref)
    direct_verification = any(
        c["claim_type"] not in _ASSUMPTION_CLAIM_TYPES for c in evidence_claims
    )
    confirmed_assumption = any(
        c["claim_type"] == "assumption" and c["review_status"] == "teacher_approved"
        for c in matched_claims
    )
    scope_text = ""
    for comp in matched_components:
        scope = comp.get("source_scope") or {}
        if isinstance(scope, dict) and scope.get("scope"):
            scope_text = str(scope["scope"])
            break

    matched_claim_ids = {c["id"] for c in matched_claims}
    inferred_count = 0
    dependent_count = 0
    for node in _graph_nodes(snapshot):
        node_label = _normalize(node.get("label") or "")
        linked_claims = {str(c) for c in node.get("linked_claim_ids") or []}
        touches = bool(matched_claim_ids & linked_claims) or (
            node_label and any(t and t in node_label for t in targets)
        )
        if not touches:
            continue
        dependent_count += 1
        if str(node.get("source_backing_status") or "") == "inferred":
            inferred_count += 1

    standard_explanation = False
    personal_shared = 0
    authors: list[str] = []
    endorser_count = 0
    strong_count = 0
    expertise_breadth = 0
    endorsed_explanations = 0
    for exp in snapshot.explanations:
        if exp["component_id"] not in matched_component_ids:
            continue
        if exp["kind"] == "standard":
            standard_explanation = True
        elif exp["shared"] or exp["review_status"] == "teacher_approved":
            personal_shared += 1
            if exp["author_name"]:
                authors.append(exp["author_name"])
        endorser_count += exp["endorser_count"]
        strong_count += exp["strong_count"]
        expertise_breadth = max(expertise_breadth, exp["expertise_breadth"])
        if exp["endorser_count"] > 0:
            endorsed_explanations += 1

    return ConceptSignals(
        key=key,
        label=label,
        component_count=len(matched_components),
        has_binding=has_binding,
        evidence_count=len(evidence_claims),
        evidence_refs=tuple(refs[:4]),
        direct_verification=direct_verification,
        scope_text=scope_text,
        inferred_count=inferred_count,
        confirmed_assumption=confirmed_assumption,
        dependent_count=dependent_count,
        standard_explanation=standard_explanation,
        personal_shared_count=personal_shared,
        author_labels=tuple(authors[:3]),
        endorser_count=endorser_count,
        strong_count=strong_count,
        expertise_breadth=expertise_breadth,
        endorsement_split=endorsed_explanations >= 2,
        seed_status=seed_status,
    )


# ---------------------------------------------------------------------------
# L3: 導出チェーン (TheoryOperationGraph main 層 → 行間ステップの可視化)
# ---------------------------------------------------------------------------

# main graph の theory stage 並び (core/document_pipeline 側 schema.THEORY_STAGES と同じ
# domain-neutral 語彙。import はしない — A層のコードに依存しないため写しで持つ)
_STAGE_ORDER = (
    "theory_basis",
    "observation_model",
    "observable_construction",
    "equation_system",
    "elimination",
    "consistency_relation",
    "diagnostic_application",
)


def build_derivation_chain(snapshot: CorpusSnapshot, *, limit: int = 12) -> list[dict]:
    """L3 の導出チェーンを graph_json の main 層から決定論的に組む。

    - stage 順 (無ければ id 順) で並べ、上限 §13 (L3 ≤ 12) を守る
    - gap: source_backing_status が inferred / review_required のステップ
    - verified: source_backed (partially は verified 扱いで verify 行に部分裏付けを明記)
    """
    main_nodes: list[dict] = []
    for node in _graph_nodes(snapshot):
        layer = str(node.get("graph_layer") or "main")
        if layer != "main":
            continue
        main_nodes.append(node)

    def _order(node: dict) -> tuple:
        stage = str(node.get("stage") or "")
        stage_index = _STAGE_ORDER.index(stage) if stage in _STAGE_ORDER else len(_STAGE_ORDER)
        return (stage_index, str(node.get("component_id") or node.get("id") or ""))

    main_nodes.sort(key=_order)
    chain: list[dict] = []
    for node in main_nodes[:limit]:
        node_id = str(node.get("component_id") or node.get("id") or "")
        if not node_id:
            continue
        backing = str(node.get("source_backing_status") or "")
        eq_refs = [str(e) for e in (node.get("linked_equation_ids") or [])][:4]
        if backing in ("inferred", "review_required"):
            status = STATUS_GAP
            verify = GAP_VERIFY_LINE
            endorse = GAP_ENDORSE_LINE
        else:
            status = STATUS_VERIFIED
            refs = "・".join(eq_refs)
            if backing == "partially_source_backed":
                verify = f"検証: 部分的な原文対応の記帳あり{f'（{refs}）' if refs else ''}。"
            else:
                verify = f"検証: 原文の式に対応{f'（{refs}）' if refs else ''}。"
            endorse = "承認: 標準の説明"
        chain.append(
            {
                "id": f"step_{node_id}",
                "label": str(node.get("label") or node_id),
                "status": status,
                "verify": verify,
                "endorse": endorse,
                "evidence_refs": eq_refs,
                "backing": backing,
            }
        )
    return chain


# ---------------------------------------------------------------------------
# コーパス署名 (差分検知) と dirty マーカー
# ---------------------------------------------------------------------------

_SIGNATURE_SQL = """
    SELECT
      (SELECT COALESCE(max(r.completed_at)::text, '')
         FROM document_analysis_runs r
        WHERE r.cartridge_id = :cartridge_id AND r.status = 'completed'),
      (SELECT COALESCE(max(tc.updated_at)::text, '')
         FROM theory_components tc
         JOIN document_analysis_runs r ON r.document_id = tc.document_id
        WHERE r.cartridge_id = :cartridge_id AND r.status = 'completed'),
      (SELECT COALESCE(max(c.updated_at)::text, '')
         FROM theory_claims c
         JOIN document_analysis_runs r ON r.document_id = c.document_id
        WHERE r.cartridge_id = :cartridge_id AND r.status = 'completed'),
      (SELECT COALESCE(max(e.updated_at)::text, '')
         FROM component_explanations e
         JOIN theory_components tc ON tc.id = e.component_id
         JOIN document_analysis_runs r ON r.document_id = tc.document_id
        WHERE r.cartridge_id = :cartridge_id AND r.status = 'completed'),
      (SELECT COALESCE(max(en.updated_at)::text, '')
         FROM component_endorsements en
         JOIN component_explanations e ON e.id = en.explanation_id
         JOIN theory_components tc ON tc.id = e.component_id
         JOIN document_analysis_runs r ON r.document_id = tc.document_id
        WHERE r.cartridge_id = :cartridge_id AND r.status = 'completed')
"""


def compute_corpus_signature(session, cartridge_id: str) -> str:
    """更新契機の検知に使うコーパス透かし。

    論文取り込み完了 (document_analysis_runs.completed_at) / 承認・解釈追加
    (component_explanations / component_endorsements) / 行間確定
    (theory_claims.review_status 遷移 → updated_at) をすべて含む。
    """
    try:
        row = session.execute(
            sa_text(_SIGNATURE_SQL), {"cartridge_id": cartridge_id}
        ).fetchone()
    except Exception:  # noqa: BLE001
        logger.warning("atlas signature query failed", exc_info=True)
        return ""
    if row is None:
        return ""
    return "|".join(str(v or "") for v in row)


def mark_overlay_dirty(session, cartridge_id: str, reason: str = "") -> None:
    """イベント側からの差分更新要求 (冪等)。"""
    session.execute(
        sa_text(
            """
            INSERT INTO atlas_overlay_dirty (cartridge_id, reason, marked_at)
            VALUES (:cartridge_id, :reason, now())
            ON CONFLICT (cartridge_id)
            DO UPDATE SET reason = EXCLUDED.reason, marked_at = now()
            """
        ),
        {"cartridge_id": cartridge_id, "reason": reason[:200]},
    )


def is_overlay_stale(session, cartridge_id: str, skeleton_version: str) -> bool:
    """dirty マーカー or コーパス署名の変化でキャッシュ陳腐化を判定する。"""
    try:
        dirty = session.execute(
            sa_text("SELECT 1 FROM atlas_overlay_dirty WHERE cartridge_id = :cartridge_id"),
            {"cartridge_id": cartridge_id},
        ).fetchone()
    except Exception:  # noqa: BLE001
        dirty = None
    if dirty:
        return True
    cached = _cached_meta(session, cartridge_id, skeleton_version)
    if cached is None:
        return True
    return cached.get("signature", "") != compute_corpus_signature(session, cartridge_id)


def _cached_meta(session, cartridge_id: str, skeleton_version: str) -> dict | None:
    try:
        row = session.execute(
            sa_text(
                """
                SELECT evidence FROM atlas_overlay_cache
                 WHERE cartridge_id = :cartridge_id
                   AND skeleton_version = :skeleton_version
                   AND entry_type = 'meta' AND entry_id = 'signature'
                """
            ),
            {"cartridge_id": cartridge_id, "skeleton_version": skeleton_version},
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    return _as_json(row[0]) or {}


# ---------------------------------------------------------------------------
# キャッシュ更新 (差分バッチ本体)
# ---------------------------------------------------------------------------


def _skeleton_binding_maps(skeleton) -> tuple[dict[str, str], dict[str, str]]:
    """(binding graph_concept_id → 骨格概念 id, 骨格概念 id → 領域 id)。"""
    concept_region: dict[str, str] = {}
    for region in skeleton.regions:
        for concept in region.concepts:
            concept_region[concept.id] = region.id
    binding_to_concept: dict[str, str] = {}
    for binding in skeleton.concept_bindings:
        if binding.reviewed and binding.skeleton in concept_region:
            binding_to_concept[str(binding.graph_concept_id)] = binding.skeleton
    return (binding_to_concept, concept_region)


# ---------------------------------------------------------------------------
# topic ↔ 骨格概念 binding の DB 解決 (Issue: 個人層 binding の整備)
#
# 純粋な突き合わせ (core.atlas.match_topic_to_concept) の DB 依存部分:
# コース → カートリッジの導出 (gap3) と、topic の教材由来 component から
# concept_bindings 経由で骨格概念を引く corpus binding (gap1/gap2 の on-demand 経路)。
# ---------------------------------------------------------------------------


def _fallback_cartridge_id() -> str:
    """既定カートリッジ (Settings.default_cartridge_id → particle_physics)。"""
    try:
        from core import cartridges as cartridges_module

        return cartridges_module._default_cartridge_id()
    except Exception:  # noqa: BLE001
        return "particle_physics"


_COURSE_CARTRIDGE_SQL = """
    SELECT r.cartridge_id, count(*) AS n
      FROM document_analysis_runs r
     WHERE r.material_id IN :material_ids
       AND r.status = 'completed'
       AND COALESCE(r.cartridge_id, '') <> ''
     GROUP BY r.cartridge_id
     ORDER BY n DESC, r.cartridge_id
     LIMIT 1
"""


def resolve_course_cartridge(session, course_data: dict) -> str:
    """コースに対応するカートリッジ id を決定論的に導出する (gap3)。

    優先順:
      1. course_data.cartridge_id の明示指定
      2. course_data.sources[].material_id → document_analysis_runs.cartridge_id
         (status='completed' の最頻カートリッジ、同数なら id 昇順)
      3. 既定カートリッジへ縮退
    """
    if isinstance(course_data, dict):
        explicit = str(course_data.get("cartridge_id") or "").strip()
        if explicit:
            return explicit

    material_ids: list[str] = []
    if isinstance(course_data, dict):
        for source in course_data.get("sources") or []:
            if isinstance(source, dict) and source.get("material_id"):
                material_ids.append(str(source["material_id"]))
    if material_ids and session is not None:
        try:
            row = session.execute(
                sa_text(_COURSE_CARTRIDGE_SQL).bindparams(
                    bindparam("material_ids", expanding=True)
                ),
                {"material_ids": material_ids},
            ).fetchone()
            if row and row[0]:
                return str(row[0])
        except Exception:  # noqa: BLE001
            logger.warning("atlas course cartridge query failed", exc_info=True)
    return _fallback_cartridge_id()


def build_component_concept_map(snapshot: CorpusSnapshot, skeleton) -> dict[str, str]:
    """component_id → 骨格概念 id のマップ (reviewed concept_bindings + 正規化名一致)。"""
    binding_to_concept, _ = _skeleton_binding_maps(skeleton)
    concept_by_norm_label: dict[str, str] = {}
    for region in skeleton.regions:
        for concept in region.concepts:
            concept_by_norm_label.setdefault(_normalize(concept.label), concept.id)
            concept_by_norm_label.setdefault(_normalize(concept.id), concept.id)

    out: dict[str, str] = {}
    for comp in snapshot.components:
        if comp["status"] == "rejected" or comp["review_status"] == "rejected":
            continue
        target = binding_to_concept.get(comp["id"]) or concept_by_norm_label.get(
            _normalize(comp["name"])
        )
        if target:
            out[comp["id"]] = target
    return out


_TOPIC_COMPONENTS_SQL = """
    SELECT DISTINCT tc.id::text, tc.name
      FROM theory_components tc
      JOIN document_analysis_runs r
        ON r.document_id = tc.document_id
       AND r.cartridge_id = :cartridge_id
       AND r.status = 'completed'
     WHERE tc.primary_chunk_id IN :chunk_ids
       AND COALESCE(tc.status, '') <> 'rejected'
       AND COALESCE(tc.review_status, '') <> 'rejected'
"""


def resolve_topic_concept_via_corpus(session, skeleton, cartridge_id: str, topic: dict) -> str | None:
    """topic の教材由来 component → concept_bindings 経由で骨格概念 id を引く。

    hot path (チャット) では呼ばない (スナップショット読み込みを伴う)。トピック完了
    導線の focus 解決など on-demand の経路でのみ使用する。
    """
    if session is None or not isinstance(topic, dict):
        return None
    raw_chunk_ids = topic.get("material_chunk_ids") or []
    chunk_ids = [str(c) for c in raw_chunk_ids if c]
    if not chunk_ids:
        return None
    try:
        rows = session.execute(
            sa_text(_TOPIC_COMPONENTS_SQL).bindparams(bindparam("chunk_ids", expanding=True)),
            {"cartridge_id": cartridge_id, "chunk_ids": chunk_ids},
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.warning("atlas topic->component query failed", exc_info=True)
        return None
    if not rows:
        return None

    snapshot = load_corpus_snapshot(session, cartridge_id)
    comp_map = build_component_concept_map(snapshot, skeleton)
    concept_by_norm_label: dict[str, str] = {}
    for region in skeleton.regions:
        for concept in region.concepts:
            concept_by_norm_label.setdefault(_normalize(concept.label), concept.id)
    # id 昇順で決定論的に最初に骨格概念へ引ける component を採用する
    for comp_id, comp_name in sorted((str(r[0]), r[1] or "") for r in rows):
        target = comp_map.get(comp_id) or concept_by_norm_label.get(_normalize(comp_name))
        if target:
            return target
    return None


def compute_overlay_rows(
    skeleton,
    snapshot: CorpusSnapshot,
    *,
    cartridge_id: str,
    region_vector_fn: Callable[[str], list[float] | None] | None = None,
) -> list[dict]:
    """骨格 + スナップショット → キャッシュ行 (dict) の一覧。純導出 (DB 書き込みなし)。"""
    version = skeleton.version
    binding_to_concept, concept_region = _skeleton_binding_maps(skeleton)
    concept_bound_components: dict[str, tuple[str, ...]] = {}
    for component_key, concept_id in binding_to_concept.items():
        concept_bound_components.setdefault(concept_id, ())
        concept_bound_components[concept_id] += (component_key,)

    rows: list[dict] = []
    matched_component_ids: set[str] = set()
    region_member_vectors: dict[str, list[list[float]]] = {}
    concept_signal_map: dict[str, ConceptSignals] = {}

    # --- 骨格代表概念の状態 ---
    for region in skeleton.regions:
        for concept in region.concepts:
            signals = build_concept_signals(
                snapshot,
                key=concept.id,
                label=concept.label,
                aliases=(concept.id,),
                component_ids=concept_bound_components.get(concept.id, ()),
                has_binding=concept.id in concept_bound_components,
                seed_status=concept.display_seed_status,
            )
            concept_signal_map[concept.id] = signals
            status, source = derive_node_status(signals)
            for comp in snapshot.components:
                if comp["id"] in set(concept_bound_components.get(concept.id, ())) or _normalize(
                    comp["name"]
                ) == _normalize(concept.label):
                    matched_component_ids.add(comp["id"])
                    vector = snapshot.vectors.get(comp["id"])
                    if vector:
                        region_member_vectors.setdefault(region.id, []).append(vector)
            learn, evid = actions_for(status)
            rows.append(
                {
                    "entry_type": "node",
                    "entry_id": concept.id,
                    "region_id": region.id,
                    "label": concept.label,
                    "status": status,
                    "status_source": source,
                    "verify_line": verify_line_for(status, signals),
                    "endorse_line": endorse_line_for(status, signals),
                    "learn_enabled": learn,
                    "evid_enabled": evid,
                    "layout": (
                        {"x": concept.layout.x, "y": concept.layout.y}
                        if concept.layout is not None
                        else {}
                    ),
                    "placement": {"method": "skeleton"},
                    "evidence": {
                        "evidence_refs": list(signals.evidence_refs),
                        "evidence_count": signals.evidence_count,
                        "endorser_count": signals.endorser_count,
                        "strong_count": signals.strong_count,
                        "expertise_breadth": signals.expertise_breadth,
                        "interpretations": signals.personal_shared_count
                        + (1 if signals.standard_explanation else 0),
                    },
                }
            )

    # --- E-2: コーパス追加概念の配置 (骨格概念に紐づかなかった component) ---
    unmatched_vectors = {
        f"comp_{cid}": vec
        for cid, vec in snapshot.vectors.items()
        if cid not in matched_component_ids
    }
    prototypes: dict[str, list[float]] = {}
    for region in skeleton.regions:
        proto = atlas_placement.mean_vector(region_member_vectors.get(region.id, []))
        if proto is None and region_vector_fn is not None:
            proto = region_vector_fn(region.label)
        if proto:
            prototypes[region.id] = proto
    placements = atlas_placement.assign_concepts_to_regions(unmatched_vectors, prototypes)
    component_by_id = {c["id"]: c for c in snapshot.components}
    occupied: dict[str, list[tuple[float, float]]] = {}
    region_layout_map = {
        r.id: (
            {"x": r.layout.x, "y": r.layout.y, "w": r.layout.w, "h": r.layout.h}
            if r.layout is not None
            else {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        )
        for r in skeleton.regions
    }
    placed_count_by_region: dict[str, int] = {}
    for placement in placements:
        component_id = placement.concept_key.removeprefix("comp_")
        comp = component_by_id.get(component_id)
        if comp is None:
            continue
        signals = build_concept_signals(
            snapshot,
            key=placement.concept_key,
            label=comp["name"],
            component_ids=(component_id,),
        )
        status, source = derive_node_status(signals)
        layout: dict[str, float] = {}
        region_id = placement.region_id or ""
        if region_id:
            x, y = atlas_placement.layout_in_region(
                placement.concept_key,
                region_layout_map.get(region_id, {"x": 0, "y": 0, "w": 1, "h": 1}),
                skeleton_version=version,
                occupied=occupied.setdefault(region_id, []),
            )
            occupied[region_id].append((x, y))
            layout = {"x": x, "y": y}
            placed_count_by_region[region_id] = placed_count_by_region.get(region_id, 0) + 1
        learn, evid = actions_for(status)
        rows.append(
            {
                "entry_type": "node",
                "entry_id": placement.concept_key,
                "region_id": region_id,
                "label": comp["name"],
                "status": status,
                "status_source": source,
                "verify_line": verify_line_for(status, signals),
                "endorse_line": endorse_line_for(status, signals),
                "learn_enabled": learn,
                "evid_enabled": evid,
                "layout": layout,
                "placement": {
                    "method": placement.method,
                    "distance": placement.distance,
                    "unplaced": placement.region_id is None,
                },
                "evidence": {
                    "evidence_refs": list(signals.evidence_refs),
                    "evidence_count": signals.evidence_count,
                    "endorser_count": signals.endorser_count,
                    "component_id": component_id,
                },
            }
        )

    # --- 領域の被覆状態 (fog / lit) ---
    for region in skeleton.regions:
        coverage = placed_count_by_region.get(region.id, 0)
        for concept in region.concepts:
            signals = concept_signal_map.get(concept.id)
            if signals is not None and (signals.has_corpus_state or signals.has_binding):
                coverage += 1
        status = derive_region_status(coverage)
        rows.append(
            {
                "entry_type": "region",
                "entry_id": region.id,
                "region_id": region.id,
                "label": region.label,
                "status": status,
                "status_source": STATUS_SOURCE_DERIVED,
                "verify_line": FOG_VERIFY_LINE if status == STATUS_FOG else f"検証: この領域に{coverage}件の記帳あり。",
                "endorse_line": FOG_ENDORSE_LINE if status == STATUS_FOG else "",
                "learn_enabled": status != STATUS_FOG,
                "evid_enabled": status != STATUS_FOG,
                "layout": region_layout_map.get(region.id, {}),
                "placement": {"method": "skeleton"},
                "evidence": {"coverage": coverage},
            }
        )

    # --- L3 導出チェーン ---
    chain = build_derivation_chain(snapshot)
    for step in chain:
        learn, evid = actions_for(step["status"])
        rows.append(
            {
                "entry_type": "node",
                "entry_id": step["id"],
                "region_id": "",
                "label": step["label"],
                "status": step["status"],
                "status_source": STATUS_SOURCE_DERIVED,
                "verify_line": step["verify"],
                "endorse_line": step["endorse"],
                "learn_enabled": learn,
                "evid_enabled": evid,
                "layout": {},
                "placement": {"method": "derivation"},
                "evidence": {"evidence_refs": step["evidence_refs"], "backing": step["backing"]},
            }
        )
    if chain:
        rows.append(
            {
                "entry_type": "chain",
                "entry_id": "derivation",
                "region_id": "",
                "label": "導出チェーン",
                "status": "",
                "status_source": STATUS_SOURCE_DERIVED,
                "verify_line": "",
                "endorse_line": "",
                "learn_enabled": True,
                "evid_enabled": True,
                "layout": {},
                "placement": {},
                "evidence": {"chain": [s["id"] for s in chain]},
            }
        )
    return rows


def refresh_overlay_cache(
    session,
    skeleton,
    *,
    cartridge_id: str,
    region_vector_fn: Callable[[str], list[float] | None] | None = None,
) -> dict:
    """状態導出バッチ本体: スナップショット → 導出 → キャッシュ差し替え。

    (cartridge_id, skeleton_version) 単位で全行を入れ替える (キャッシュは高々
    数十行なので差分適用より単純さを優先)。commit は呼び出し側。
    """
    snapshot = load_corpus_snapshot(session, cartridge_id)
    rows = compute_overlay_rows(
        skeleton, snapshot, cartridge_id=cartridge_id, region_vector_fn=region_vector_fn
    )
    signature = compute_corpus_signature(session, cartridge_id)
    version = skeleton.version

    session.execute(
        sa_text(
            """
            DELETE FROM atlas_overlay_cache
             WHERE cartridge_id = :cartridge_id AND skeleton_version = :skeleton_version
            """
        ),
        {"cartridge_id": cartridge_id, "skeleton_version": version},
    )
    insert_sql = sa_text(
        """
        INSERT INTO atlas_overlay_cache (
            cartridge_id, skeleton_version, entry_type, entry_id, region_id, label,
            status, status_source, verify_line, endorse_line, learn_enabled,
            evid_enabled, layout, placement, evidence, updated_at
        ) VALUES (
            :cartridge_id, :skeleton_version, :entry_type, :entry_id, :region_id, :label,
            :status, :status_source, :verify_line, :endorse_line, :learn_enabled,
            :evid_enabled, CAST(:layout AS jsonb), CAST(:placement AS jsonb),
            CAST(:evidence AS jsonb), now()
        )
        """
    )
    for row in rows:
        session.execute(
            insert_sql,
            {
                "cartridge_id": cartridge_id,
                "skeleton_version": version,
                "entry_type": row["entry_type"],
                "entry_id": row["entry_id"],
                "region_id": row["region_id"],
                "label": row["label"],
                "status": row["status"],
                "status_source": row["status_source"],
                "verify_line": row["verify_line"],
                "endorse_line": row["endorse_line"],
                "learn_enabled": row["learn_enabled"],
                "evid_enabled": row["evid_enabled"],
                "layout": json.dumps(row["layout"], ensure_ascii=False),
                "placement": json.dumps(row["placement"], ensure_ascii=False),
                "evidence": json.dumps(row["evidence"], ensure_ascii=False),
            },
        )
    session.execute(
        insert_sql,
        {
            "cartridge_id": cartridge_id,
            "skeleton_version": version,
            "entry_type": "meta",
            "entry_id": "signature",
            "region_id": "",
            "label": "",
            "status": "",
            "status_source": STATUS_SOURCE_DERIVED,
            "verify_line": "",
            "endorse_line": "",
            "learn_enabled": True,
            "evid_enabled": True,
            "layout": "{}",
            "placement": "{}",
            "evidence": json.dumps({"signature": signature}, ensure_ascii=False),
        },
    )
    session.execute(
        sa_text("DELETE FROM atlas_overlay_dirty WHERE cartridge_id = :cartridge_id"),
        {"cartridge_id": cartridge_id},
    )
    node_count = sum(1 for r in rows if r["entry_type"] == "node")
    region_count = sum(1 for r in rows if r["entry_type"] == "region")
    logger.info(
        "atlas overlay refreshed: cartridge=%s version=%s nodes=%d regions=%d",
        cartridge_id,
        version,
        node_count,
        region_count,
    )
    return {
        "cartridge_id": cartridge_id,
        "skeleton_version": version,
        "nodes": node_count,
        "regions": region_count,
        "signature": signature,
    }


def fetch_overlay_rows(session, cartridge_id: str, skeleton_version: str) -> list[dict]:
    """キャッシュ読み出し (API 実行時パス)。"""
    try:
        result = session.execute(
            sa_text(
                """
                SELECT entry_type, entry_id, region_id, label, status, status_source,
                       verify_line, endorse_line, learn_enabled, evid_enabled,
                       layout, placement, evidence
                  FROM atlas_overlay_cache
                 WHERE cartridge_id = :cartridge_id
                   AND skeleton_version = :skeleton_version
                 ORDER BY entry_type, entry_id
                """
            ),
            {"cartridge_id": cartridge_id, "skeleton_version": skeleton_version},
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.warning("atlas overlay cache read failed", exc_info=True)
        return []
    rows = []
    for r in result:
        rows.append(
            {
                "entry_type": r[0],
                "entry_id": r[1],
                "region_id": r[2] or "",
                "label": r[3] or "",
                "status": r[4] or "",
                "status_source": r[5] or "",
                "verify_line": r[6] or "",
                "endorse_line": r[7] or "",
                "learn_enabled": bool(r[8]),
                "evid_enabled": bool(r[9]),
                "layout": _as_json(r[10]) or {},
                "placement": _as_json(r[11]) or {},
                "evidence": _as_json(r[12]) or {},
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 非同期リフレッシュ (イベント後数分以内の更新遅延目標)
# ---------------------------------------------------------------------------

# 更新契機の集約待ち時間 (秒)。イベントが連続しても1回の refresh にまとめる。
REFRESH_DELAY_SECONDS = float(os.getenv("ATLAS_REFRESH_DELAY_SECONDS", "60"))

_refresh_timers: dict[str, threading.Timer] = {}
_refresh_lock = threading.Lock()


def _run_background_refresh(cartridge_id: str) -> None:
    with _refresh_lock:
        _refresh_timers.pop(cartridge_id, None)
    try:
        from core import cartridges as cartridges_module
        from core.postgres import get_session

        cartridge = cartridges_module.load_cartridge(cartridge_id)
        skeleton = cartridge.learner_atlas_skeleton
        if skeleton is None:
            return
        session = get_session()
        try:
            refresh_overlay_cache(session, skeleton, cartridge_id=cartridge_id)
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.warning("atlas background refresh failed for %s", cartridge_id, exc_info=True)
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        logger.warning("atlas background refresh skipped for %s", cartridge_id, exc_info=True)


def schedule_overlay_refresh(cartridge_id: str, delay_seconds: float | None = None) -> None:
    """差分更新を遅延起動する (デバウンス。既にスケジュール済みなら何もしない)。

    トリガー: GET /api/atlas での陳腐化検知・dirty マーカー・管理操作。
    LLM は呼ばない (状態導出とキャッシュ書き込みのみ)。
    """
    delay = REFRESH_DELAY_SECONDS if delay_seconds is None else delay_seconds
    with _refresh_lock:
        if cartridge_id in _refresh_timers:
            return
        timer = threading.Timer(delay, _run_background_refresh, args=(cartridge_id,))
        timer.daemon = True
        _refresh_timers[cartridge_id] = timer
        timer.start()
