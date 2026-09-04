"""制度指標カタログ — 集約計器の**定義・用途・保持・非利用**の単一の真実源。

正本設計書: ``docs/features/indicator_governance_design.md``。
思想の出所は ``docs/vision.md`` §6 原則4（改訂「数値を見せない → 数値の用途と粒度を
統治する」）と §6.1「数値の宛先と用途」の「全当事者」行 —
**指標の定義・収集法・保持期間・意思決定への使い方・副作用レビューを読める**。

不変条項:

- **IG1 値の宛先は変えない・定義は全当事者に公開する** — 本カタログが公開するのは
  **定義**（何を数えるか / 何のために / どの粒度で / どこに保持するか）だけで、
  **値**（件数・トークン数・レンジ）は一切持たない。値の閲覧権限は各計器の既存
  API のロールゲートのまま（``values_audience`` はその**記述**であって強制点では
  ない）。定義そのものは学習者を含む全認証ユーザーが読める。
- **IG2 非利用4項目は全計器に必須** — 全 :class:`IndicatorSpec` は
  ``not_used_for`` に :data:`NON_USE_RANKING` / :data:`NON_USE_GRADING` /
  :data:`NON_USE_RECOMMENDATION` / :data:`NON_USE_AUTO_GATE` の4つを必ず含む
  （``__post_init__`` で構造強制。「この計器だけは成績に使う」を書けない）。
- **IG3 個人ランキング・自動ゲートを作らない** — 個人を比較・順位付けする集約を
  カタログに登録しない。閾値到達で何かが自動的に切り替わる入力にもしない
  （discuss 観測基盤の DO5「参考目安は自動ゲートにしない」の一般化）。
- **IG4 カタログに無い集約 API を新設しない** — 教員・システム管理者に見せる集約
  エンドポイントを足したら、本カタログにも1件足す。
  ``backend/tests/test_indicator_catalog_guardrails.py`` が既知の集約経路の網羅を
  固定する。
- **IG5 定義の変更は設計書とカタログの両方に記録する** — 数え方・粒度・宛先を
  変えるときは ``design_doc`` の設計書と本モジュールを同時に更新する（片方だけの
  変更は「同じ名前で別のものを数える」= 出所の不正直になる）。

粒度語彙（:data:`GRANULARITIES`）:

- ``aggregate_k_anonymous`` — 学習者由来の信号を k-匿名（k の正本は
  ``core/privacy.py::K_ANONYMITY``）で集約したもの。``k_anonymity=True`` と対応する。
- ``aggregate_system`` — 個人ではなくシステム全体の状態・消費を集約したもの。
- ``self_only`` — 本人の記録だけを本人に返すもの（他者は読めない）。
- ``per_item_no_person`` — 教材・論文など**物**の単位で、人に紐づかないもの。
- ``per_account_operational`` — **1アカウント単位の運用データ**（認証イベント時系列・
  LLM 利用実績）。学習データではなく、不正利用・休眠検知というアカウント運用の
  ためだけに SYSTEM_ADMIN が読む（AL6/AL7）。学習記録の三領域分離（vision §5.4）
  では**制度監査**側に属し、学習評価の入力にしない — 個人単位であることを隠さずに
  宣言するために、他の粒度に丸めず専用語彙を置く。

本モジュールは純宣言（FastAPI / sqlalchemy / LLM 非依存）。数値・値を一切持たない。
ガードレールは ``backend/tests/test_indicator_catalog_guardrails.py``。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

__all__ = [
    "AUDIENCES",
    "AUDIENCE_LABELS",
    "AUDIENCE_LEARNER_SELF",
    "AUDIENCE_SYSTEM_ADMIN",
    "AUDIENCE_TEACHER",
    "CATALOG_NOTE",
    "GRANULARITIES",
    "GRANULARITY_AGGREGATE_K_ANONYMOUS",
    "GRANULARITY_AGGREGATE_SYSTEM",
    "GRANULARITY_LABELS",
    "GRANULARITY_PER_ACCOUNT_OPERATIONAL",
    "GRANULARITY_PER_ITEM_NO_PERSON",
    "GRANULARITY_SELF_ONLY",
    "INDICATORS",
    "IndicatorSpec",
    "NON_USES",
    "NON_USE_AUTO_GATE",
    "NON_USE_GRADING",
    "NON_USE_LABELS",
    "NON_USE_RANKING",
    "NON_USE_RECOMMENDATION",
    "PUBLIC_VIEW_FIELDS",
    "SIDE_EFFECT_REVIEW_OWNER",
    "all_indicators",
    "catalog_public_view",
    "get_indicator",
    "indicators_for_route",
    "validate_catalog",
]


# ---------------------------------------------------------------------------
# 語彙（IG2 / 粒度 / 宛先）
# ---------------------------------------------------------------------------

#: 非利用宣言の4項目（IG2: 全計器に必須）。
NON_USE_RANKING = "ranking"
NON_USE_GRADING = "grading"
NON_USE_RECOMMENDATION = "recommendation"
NON_USE_AUTO_GATE = "auto_gate"

#: 全計器が必ず宣言する非利用の集合（この4つを削れる計器は存在しない）。
NON_USES: tuple[str, ...] = (
    NON_USE_RANKING,
    NON_USE_GRADING,
    NON_USE_RECOMMENDATION,
    NON_USE_AUTO_GATE,
)

#: 非利用項目の日本語説明（公開ビューに同梱。UI が独自の訳語表を持たないため）。
NON_USE_LABELS: Mapping[str, str] = MappingProxyType({
    NON_USE_RANKING: "個人のランキング・順位付けには使いません",
    NON_USE_GRADING: "成績評価には使いません",
    NON_USE_RECOMMENDATION: "個人向け推薦の入力には使いません",
    NON_USE_AUTO_GATE: "自動的な判定・切り替え（自動ゲート）には使いません",
})

AUDIENCE_SYSTEM_ADMIN = "system_admin"
AUDIENCE_TEACHER = "teacher"
AUDIENCE_LEARNER_SELF = "learner_self"

#: 値の閲覧者の語彙（定義の閲覧者ではない — 定義は全当事者が読める。IG1）。
AUDIENCES: tuple[str, ...] = (
    AUDIENCE_SYSTEM_ADMIN,
    AUDIENCE_TEACHER,
    AUDIENCE_LEARNER_SELF,
)

AUDIENCE_LABELS: Mapping[str, str] = MappingProxyType({
    AUDIENCE_SYSTEM_ADMIN: "システム管理者",
    AUDIENCE_TEACHER: "教員",
    AUDIENCE_LEARNER_SELF: "本人（学習者）",
})

GRANULARITY_AGGREGATE_K_ANONYMOUS = "aggregate_k_anonymous"
GRANULARITY_AGGREGATE_SYSTEM = "aggregate_system"
GRANULARITY_SELF_ONLY = "self_only"
GRANULARITY_PER_ITEM_NO_PERSON = "per_item_no_person"
GRANULARITY_PER_ACCOUNT_OPERATIONAL = "per_account_operational"

GRANULARITIES: tuple[str, ...] = (
    GRANULARITY_AGGREGATE_K_ANONYMOUS,
    GRANULARITY_AGGREGATE_SYSTEM,
    GRANULARITY_SELF_ONLY,
    GRANULARITY_PER_ITEM_NO_PERSON,
    GRANULARITY_PER_ACCOUNT_OPERATIONAL,
)

GRANULARITY_LABELS: Mapping[str, str] = MappingProxyType({
    GRANULARITY_AGGREGATE_K_ANONYMOUS: "k-匿名の集約（最小集計単位を下回るセルは非表示）",
    GRANULARITY_AGGREGATE_SYSTEM: "システム全体の集約（個人単位ではない）",
    GRANULARITY_SELF_ONLY: "本人の記録のみ（他者は読めない）",
    GRANULARITY_PER_ITEM_NO_PERSON: "教材・論文など物の単位（人に紐づかない）",
    GRANULARITY_PER_ACCOUNT_OPERATIONAL: "1アカウント単位の運用データ（学習データではない）",
})

#: 副作用（KPI 化・目標化への漂流）のレビュー主体。自動判定を置かないことを含めて
#: 事実文で書く（IG3: 閾値で何かが自動的に切り替わる仕組みを作らない）。
SIDE_EFFECT_REVIEW_OWNER = "オーナーの定期レビュー（自動判定なし）"

#: カタログ公開時に必ず添える固定の事実文（UI・API・マニュアルで同一文言）。
CATALOG_NOTE = (
    "これらは制度を観察するための計器です。"
    "個人の比較・成績・推薦・自動判定には使いません。"
    "定義の変更は設計書と本カタログの両方に記録します。"
)

#: 公開ビューに載せるフィールド（**定義のみ**。値・件数は1つも無い。IG1）。
PUBLIC_VIEW_FIELDS: tuple[str, ...] = (
    "id",
    "label",
    "definition",
    "purpose",
    "values_audience",
    "values_audience_label",
    "granularity",
    "granularity_label",
    "source",
    "retention",
    "k_anonymity",
    "route",
    "consumer",
    "not_used_for",
    "not_used_for_labels",
    "design_doc",
    "side_effect_review",
)


# ---------------------------------------------------------------------------
# 宣言
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndicatorSpec:
    """1つの制度指標の宣言。値は持たない（IG1）。"""

    id: str                       # kebab-case の識別子
    label: str                    # 日本語の計器名（マニュアル・UI の表示の正本）
    definition: str               # 何を数えるか（事実文）
    purpose: str                  # 何のために observe するか
    values_audience: str          # 値を読めるロール（AUDIENCES）
    granularity: str              # 粒度（GRANULARITIES）
    source: str                   # 元データ（テーブル / イベント）
    retention: str                # 保持（append-only なら明記）
    k_anonymity: bool             # k-匿名ゲートを通すか（granularity と対応）
    route: str                    # 公開 API パス（プレフィックス込みの実パス）
    consumer: str                 # 実装（module::function）
    design_doc: str               # 設計書の相対パス
    not_used_for: tuple[str, ...] = NON_USES
    side_effect_review: str = SIDE_EFFECT_REVIEW_OWNER

    def __post_init__(self) -> None:
        if not self.id or self.id != self.id.strip().lower():
            raise ValueError(f"indicator id は小文字 kebab-case: {self.id!r}")
        if " " in self.id or "_" in self.id:
            raise ValueError(f"indicator id は kebab-case（空白・アンダースコア禁止）: {self.id!r}")
        if self.values_audience not in AUDIENCES:
            raise ValueError(
                f"{self.id}: values_audience は {AUDIENCES} のいずれか"
                f"（受信値: {self.values_audience!r}）"
            )
        if self.granularity not in GRANULARITIES:
            raise ValueError(
                f"{self.id}: granularity は {GRANULARITIES} のいずれか"
                f"（受信値: {self.granularity!r}）"
            )
        # IG2: 非利用4項目は削れない。
        missing = [term for term in NON_USES if term not in self.not_used_for]
        if missing:
            raise ValueError(
                f"{self.id}: not_used_for に必須の非利用宣言が欠けています: {missing}"
                "（IG2 — 全計器がランキング・成績・推薦・自動ゲートへの非利用を宣言する）"
            )
        # k-匿名フラグと粒度の一致（片方だけ書き換えて意味を分裂させない）。
        expected_k = self.granularity == GRANULARITY_AGGREGATE_K_ANONYMOUS
        if self.k_anonymity is not expected_k:
            raise ValueError(
                f"{self.id}: k_anonymity={self.k_anonymity} と "
                f"granularity={self.granularity!r} が一致しません"
                f"（k_anonymity は granularity == {GRANULARITY_AGGREGATE_K_ANONYMOUS!r} と同値）"
            )
        for field_name in ("label", "definition", "purpose", "source", "retention",
                           "route", "consumer", "design_doc", "side_effect_review"):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{self.id}: {field_name} は必須です")
        if not self.route.startswith("/api/"):
            raise ValueError(f"{self.id}: route は実パス（/api/... ）で書く: {self.route!r}")

    def public_dict(self) -> dict:
        """公開ビュー1件分（定義のみ・値なし）。"""
        return {
            "id": self.id,
            "label": self.label,
            "definition": self.definition,
            "purpose": self.purpose,
            "values_audience": self.values_audience,
            "values_audience_label": AUDIENCE_LABELS[self.values_audience],
            "granularity": self.granularity,
            "granularity_label": GRANULARITY_LABELS[self.granularity],
            "source": self.source,
            "retention": self.retention,
            "k_anonymity": self.k_anonymity,
            "route": self.route,
            "consumer": self.consumer,
            "not_used_for": list(self.not_used_for),
            "not_used_for_labels": [
                NON_USE_LABELS.get(term, term) for term in self.not_used_for
            ],
            "design_doc": self.design_doc,
            "side_effect_review": self.side_effect_review,
        }


# ---------------------------------------------------------------------------
# カタログ本体（挿入順 = 公開ビューの並び順の正本）
#
# 各 definition は実装を読んで書いてある（推測で書かない）。数え方を変えるときは
# 実装と設計書と本文の3つを同時に直すこと（IG5）。
# ---------------------------------------------------------------------------

_SPECS: tuple[IndicatorSpec, ...] = (
    # -- U層（LLM トークン使用量推計） ------------------------------------
    IndicatorSpec(
        id="llm-usage-metrics",
        label="LLM使用量メトリクス",
        definition=(
            "LLM 呼び出し1回ごとに記録されたトークン数を、期間・集計軸"
            "（機能 / モデル / 日 / ユーザー など）で合算したものです。"
            "実測（プロバイダ申告）と推計（トークナイザ / ヒューリスティック）は"
            "合算せず分けて返し、記録バッファから溢れて記録できなかった件数"
            "（dropped_events）も隠さず返します。"
        ),
        purpose=(
            "システム全体の LLM 消費の規模と偏りを運営者が把握し、"
            "上限設定・モデル選択・機能の重さの見直しを判断するため。"
        ),
        values_audience=AUDIENCE_SYSTEM_ADMIN,
        granularity=GRANULARITY_AGGREGATE_SYSTEM,
        source="llm_usage_events（migration 043）",
        retention=(
            "append-only。削除・更新 API を持ちません（U6）。"
            "アカウント削除時の purge 対象としてのみ消えます。"
        ),
        k_anonymity=False,
        route="/api/admin/llm-usage/metrics",
        consumer="core/llm_usage/metrics.py::collect_metrics",
        design_doc="docs/features/llm_usage_metering_design.md",
    ),
    IndicatorSpec(
        id="llm-usage-forecast",
        label="AI利用枠の見通し",
        definition=(
            "パイプライン末端4ステージの**システム全体の**日次呼び出し上限に対する"
            "残数から、「この規模の処理は今日の枠に収まらない可能性がある」かどうかを"
            "判定した1行の事実文です。残回数・トークン数・金額は返しません"
            "（返すのは表示するかどうかの真偽値と固定文だけです）。"
            "カウンタは教員ごとではなくシステム全体で共有され、"
            "導出に失敗したときは何も表示しません。"
        ),
        purpose=(
            "大きな解析を始める前に、分けて実行する選択肢があることを教員に"
            "事実として伝えるため。処理をブロックするためではありません。"
        ),
        values_audience=AUDIENCE_TEACHER,
        granularity=GRANULARITY_AGGREGATE_SYSTEM,
        source="各ステージの日次カウンタ（CostGate / 当日 run の vision_calls 集計）",
        retention="保存しません（呼び出しのたびに現在のカウンタから導出します）。",
        k_anonymity=False,
        route="/api/admin/llm-usage/forecast",
        consumer="core/llm_usage/forecast.py::forecast_run_capacity",
        design_doc="docs/features/teacher_triage_instruments_design.md",
    ),
    IndicatorSpec(
        id="llm-usage-estimate",
        label="教材ごとの解析トークン見積り",
        definition=(
            "1つの教材（論文）を解析したときに消費するトークン量の事前見積りです。"
            "同種の解析の実績から導き、**レンジ（下限〜上限）のみ**を返します。"
            "点推定・金額は返しません。実績が無ければ「見積り不可」と正直に返します。"
        ),
        purpose="解析の実行前に、その教材の処理規模を教員が把握するため。",
        values_audience=AUDIENCE_TEACHER,
        granularity=GRANULARITY_PER_ITEM_NO_PERSON,
        source="llm_usage_events（feature が pipeline: で始まる行）を document 単位に合算",
        retention="保存しません（呼び出しのたびに実績から導出します）。",
        k_anonymity=False,
        route="/api/admin/llm-usage/estimate/documents/{document_id}",
        consumer="core/llm_usage/document_estimate.py::estimate_document_run",
        design_doc="docs/features/llm_usage_metering_design.md",
    ),
    # -- D層 KPI ------------------------------------------------------------
    IndicatorSpec(
        id="doubt-metrics",
        label="疑いと検証の制度KPI",
        definition=(
            "「システムが指摘した数」ではなく「人間が行為した数」を数えます。"
            "認識的地位台帳の被覆（負荷の高いノードのうちスコープが記帳されている割合）、"
            "スコープ未記帳の台帳行の数、教員による記帳・疑義・検証提案の行為数などを、"
            "監査台帳と台帳テーブルの再集計だけで算出します"
            "（専用のカウンタテーブルを持ちません）。"
        ),
        purpose=(
            "疑いと検証の制度が実際に使われているか（人が記帳しているか）を"
            "運営者が確認し、機能の作り直しを判断するため。"
        ),
        values_audience=AUDIENCE_SYSTEM_ADMIN,
        granularity=GRANULARITY_AGGREGATE_SYSTEM,
        source="theory_review_events / epistemic_ledger / assumption_nodes ほか D層テーブル",
        retention="保存しません（呼び出しのたびに再集計します）。元の監査台帳は append-only。",
        k_anonymity=False,
        route="/api/admin/doubt/metrics",
        consumer="core/doubt/metrics.py::collect_doubt_metrics",
        design_doc="docs/features/doubt_layer_issues.md",
    ),
    # -- discuss 観測基盤 ---------------------------------------------------
    IndicatorSpec(
        id="discuss-observation-status",
        label="論文ディスカッションの観測状況",
        definition=(
            "「論文と話す」モードの利用がどれだけ蓄積されたか（イベント種別ごとの件数と"
            "期間）と、分析に入るかどうかの参考目安への到達状況を返します。"
            "観測イベントには**発話の本文・逐語・引用を一切含めず**（DO1）、"
            "持ち出し用のダンプでは利用者 ID を復元不可能な仮名に置き換えます（DO2）。"
        ),
        purpose=(
            "この対話モードを次の段階へ進めるかどうかを、印象ではなく蓄積量の実測で"
            "運営者が判断するため。"
        ),
        values_audience=AUDIENCE_SYSTEM_ADMIN,
        granularity=GRANULARITY_AGGREGATE_SYSTEM,
        source="discuss_metric_events（migration 060）",
        retention="append-only。削除 API を持ちません（DO4）。",
        k_anonymity=False,
        route="/api/admin/discuss/observation-status",
        consumer="core/discuss/observation.py::build_observation_status",
        design_doc="docs/features/discuss_observation_design.md",
    ),
    # -- B層 教員向け集約 ---------------------------------------------------
    IndicatorSpec(
        id="interest-dashboard",
        label="関心集約ダッシュボード",
        definition=(
            "1コースの中で、どのトピックに問い・引っかかりが集まっているかの集団集計です。"
            "違和感・帰属のヒートマップのセルは、関わった人数が最小集計単位に満たなければ"
            "表示しません。トピック単位の件数はコース全体の粗い集計で、"
            "**個々の記録・発話の本文・利用者 ID は一切返しません**。"
            "本人が引き受けていない候補（AI が提案しただけのもの）は数えません。"
            "本人専用の記録（学習の意図・軽量アンカー・使い方の質問・楽屋の質問）は"
            "この集計から構造的に除外されています。"
        ),
        purpose=(
            "クラス全体でどこが難所になっているかを教員が知り、教材と授業を"
            "改善するため。個々の受講者の状態を知るためではありません。"
        ),
        values_audience=AUDIENCE_TEACHER,
        granularity=GRANULARITY_AGGREGATE_K_ANONYMOUS,
        source="interest_traces（痕跡 kind の登録簿は core/trace_registry.py）",
        retention=(
            "元の痕跡は行削除せず状態遷移でのみ変わります（本人は「地図に反映しない」等で"
            "訂正できます）。集計自体は保存しません。"
        ),
        k_anonymity=True,
        route="/api/admin/interest-dashboard",
        consumer="api/services.py::aggregate_interest_dashboard",
        design_doc="docs/features/admin.md",
    ),
    IndicatorSpec(
        id="bridge-insights",
        label="橋の候補（学習者の重ね合わせ）",
        definition=(
            "受講者が自分の引っかかりを公共の構造（理論の部品・関係）へ**自分で**"
            "結びつけた「橋」を、結び先ごとに人数で数えたものです。"
            "人数が最小集計単位に満たない結び先は返さず、"
            "人数は具体的な数ではなくレンジで表示します。"
            "個々の受講者・個々の痕跡は返しません。"
        ),
        purpose=(
            "多くの受講者が同じ場所に橋を架けているという事実を教員が知り、"
            "教材の説明順や補足を検討するため。"
        ),
        values_audience=AUDIENCE_TEACHER,
        granularity=GRANULARITY_AGGREGATE_K_ANONYMOUS,
        source="interest_traces（kind='tension' かつ本人の結び付け操作が記録された行）",
        retention="元の痕跡は行削除せず状態遷移のみ。集計は保存しません。",
        k_anonymity=True,
        route="/api/admin/courses/{course_id}/bridge-insights",
        consumer="core/personal_graph/bridges.py::aggregate_bridge_candidates",
        design_doc="docs/features/knowledge_network_vision.md",
    ),
    IndicatorSpec(
        id="anchor-insights",
        label="問いの帰属インサイト",
        definition=(
            "受講者の問いが「理論構成のどの段階に、どういう型の引っかかりとして」"
            "向けられたかを、段階×型の粗い断面で数えたものです。"
            "対象は**本人が確定した帰属だけ**（AI の候補は数えません）。"
            "人数が最小集計単位に満たないセルは返さず、"
            "質問の原文・確信度・個々の受講者は一切返しません。"
        ),
        purpose="どの段階で説明が足りていないかを教員が知り、教材を改善するため。",
        values_audience=AUDIENCE_TEACHER,
        granularity=GRANULARITY_AGGREGATE_K_ANONYMOUS,
        source="interest_traces の帰属情報（payload.structure_anchor）",
        retention="元の痕跡は行削除せず状態遷移のみ。集計は保存しません。",
        k_anonymity=True,
        route="/api/admin/courses/{course_id}/anchor-insights",
        consumer="core/structure_anchor/insights.py::aggregate_anchor_insights",
        design_doc="docs/features/structure-anchored-questions.md",
    ),
    IndicatorSpec(
        id="naive-signals",
        label="素朴な問いの集計",
        definition=(
            "「複数の受講者が独立に同じ前提の手前でつまずいている」という事実を、"
            "前提（アンカー）の単位で数えたものです。"
            "対象は**本人が引き受けた記録だけ**（AI が候補として出しただけのものは"
            "数えません）。人数が最小集計単位に満たないセルは返さず、"
            "件数はレンジ表示のみで生の数値は返しません。"
        ),
        purpose=(
            "どの暗黙の前提が説明を必要としているかを、前提の側から教員が"
            "見つけられるようにするため。"
        ),
        values_audience=AUDIENCE_TEACHER,
        granularity=GRANULARITY_AGGREGATE_K_ANONYMOUS,
        source="interest_traces（問い / 誤解 / 引っかかりのうち本人が引き受けた行）",
        retention="元の痕跡は行削除せず状態遷移のみ。集計は保存しません。",
        k_anonymity=True,
        route="/api/admin/doubt/courses/{course_id}/naive-signals",
        consumer="core/doubt/naive_signal.py::aggregate_naive_signals",
        design_doc="docs/features/doubt_layer_issues.md",
    ),
    IndicatorSpec(
        id="frontier-interest",
        label="地図の端への関心",
        definition=(
            "受講者が分野の地図の「この先を知りたい」を1タップで示した回数を、"
            "分野×領域×輪の単位で数えたものです。"
            "タップには**質問文・本文が一切含まれません**。"
            "人数が最小集計単位に満たない行は返さず、"
            "件数はレンジ表示のみ。個人・時系列・順位は返しません。"
        ),
        purpose=(
            "分野のどの縁に関心が向いているかという需要を教員が知り、"
            "次に取り込む論文を検討するため。取り込みは常に教員の明示操作です。"
        ),
        values_audience=AUDIENCE_TEACHER,
        granularity=GRANULARITY_AGGREGATE_K_ANONYMOUS,
        source="interest_traces（kind='frontier_interest'）",
        retention=(
            "取り消しても行は消さず「取り下げ」状態へ遷移します（取り下げた分は"
            "集計に数えません）。"
        ),
        k_anonymity=True,
        route="/api/admin/discovery/frontier-interest",
        consumer="api/services.py::aggregate_frontier_interest",
        design_doc="docs/features/corpus_roaming_design.md",
    ),
    # -- R層（再構成ループ） ------------------------------------------------
    IndicatorSpec(
        id="reconstruction-review-queue",
        label="再構成の出題レビューキュー",
        definition=(
            "再構成（予測・言い直し）の出題のうち「怪しいもの」を、"
            "誤り率・判定と自己確認の乖離・異議の有無から並べ替えた一覧です。"
            "各シグナルは関わった**人数**が最小集計単位に満たなければ"
            "「まだデータなし」に伏せられ、率は段階ラベル、件数はレンジで表示されます。"
            "個々の受講者の解答・正誤の履歴は返しません。"
        ),
        purpose=(
            "AI が自動生成した出題のうち、出題そのものが壊れている可能性が高いものを"
            "教員が事後に監査するため。受講者を評価するためではありません。"
        ),
        values_audience=AUDIENCE_TEACHER,
        granularity=GRANULARITY_AGGREGATE_K_ANONYMOUS,
        source="reconstruction_items / learner_reconstructions（集計ビュー）",
        retention="出題も解答も行削除せず状態遷移で保持します。集計は保存しません。",
        k_anonymity=True,
        route="/api/admin/reconstruction/items/review-queue",
        consumer="core/reconstruction/health.py::get_review_queue",
        design_doc="docs/features/reconstruction_loop_design.md",
    ),
    IndicatorSpec(
        id="claims-stumble-summary",
        label="主張ごとのつまづきサマリー",
        definition=(
            "1つの教材の主張ごとに、誤り率・記号への降下頻度・判定と自己確認の乖離・"
            "よくある質問の4つを段階ラベルとレンジで示します。"
            "各セルは関わった**人数**が最小集計単位に満たなければ「まだデータなし」に"
            "なります（データが少ない初期はほとんどが「まだデータなし」になります）。"
            "生の数値・個々の受講者は返しません。"
        ),
        purpose="どの主張の説明が足りていないかを、原稿を書く場で教員が確認するため。",
        values_audience=AUDIENCE_TEACHER,
        granularity=GRANULARITY_AGGREGATE_K_ANONYMOUS,
        source="learner_reconstructions と interest_traces（当該主張に帰属した問い）",
        retention="元の記録は行削除せず保持。集計は保存しません。",
        k_anonymity=True,
        route="/api/admin/documents/{document_id}/claims/stumble-summary",
        consumer="core/reconstruction/stumble.py::get_stumble_summary",
        design_doc="docs/features/reconstruction_loop_design.md",
    ),
    # -- G層（次にやること）に相乗りする需要側計器 --------------------------
    IndicatorSpec(
        id="help-gaps-pending",
        label="使い方の質問の未整備箇所",
        definition=(
            "使い方に関する質問のうち「マニュアルに一致する説明が見つからなかった」"
            "「該当する節はあるが内容が未整備だった」ものを、節（アンカー）の単位で"
            "数えたものです。**質問の文面は集計に使いません**（数だけを読みます）。"
            "件数が最小集計単位に満たない箇所は点灯せず、"
            "点灯するときも件数はレンジ表示です。"
        ),
        purpose=(
            "マニュアルのどこが足りていないかを、需要の側から見つけて"
            "書き足せるようにするため。"
        ),
        values_audience=AUDIENCE_TEACHER,
        granularity=GRANULARITY_AGGREGATE_K_ANONYMOUS,
        source="interest_traces（kind='help_usage' の無ヒット / 未整備フラグ）",
        retention=(
            "元の痕跡は行削除せず保持。この項目は完了フラグを持たず、"
            "マニュアルを書き足せば自動的に消えます。"
        ),
        k_anonymity=True,
        route="/api/admin/assistant/next-steps",
        consumer="core/admin_assistant/next_steps.py::_eval_manual_help_gaps_pending",
        design_doc="docs/features/manual_help_kb_design.md",
    ),
    # -- AL層（アカウント運用） --------------------------------------------
    IndicatorSpec(
        id="account-activity",
        label="アカウント個票（認証・利用実績）",
        definition=(
            "1つのアカウントの認証イベント（ログイン成功・失敗・停止による拒否など）の"
            "時系列と、そのアカウントに帰属した LLM 利用量のサマリです。"
            "**これは1人単位の運用データであり、集約でも匿名化でもありません**。"
            "学習の記録（問い・引っかかり・再構成・意図）は含まれず、"
            "学習記録とは別の領域として分離されています。"
            "教員は閲覧できません（システム管理者のみ）。"
        ),
        purpose=(
            "不正利用・乗っ取り・休眠アカウントの検知というアカウント運用のため。"
            "学習の評価・比較には使いません。"
        ),
        values_audience=AUDIENCE_SYSTEM_ADMIN,
        granularity=GRANULARITY_PER_ACCOUNT_OPERATIONAL,
        source="auth_events（migration 068）と llm_usage_events（migration 043）",
        retention=(
            "auth_events は append-only（削除 API なし）。"
            "アカウント削除時に purge 対象として消えます。"
        ),
        k_anonymity=False,
        route="/api/admin/users/{user_id}/activity",
        consumer="api/routes/admin.py::get_user_activity",
        design_doc="docs/features/account_lifecycle_management_design.md",
    ),
    # -- 本人（主権台帳） ---------------------------------------------------
    IndicatorSpec(
        id="my-records",
        label="わたしの記録",
        definition=(
            "自分が残した痕跡（問い・引っかかり・誤解の記録・学習の意図など）を"
            "系統ごとに一望し、それぞれが「誰に、どう見えるか」を事実文で示します。"
            "**本人にしか見えません**（教員・管理者が他人の記録を開く経路はありません）。"
            "件数バッジ・進捗率・スコアは表示しません。"
        ),
        purpose=(
            "自分について何が記録され、どこまで他者に見えるのかを本人が確認し、"
            "訂正・持ち出しができるようにするため。"
        ),
        values_audience=AUDIENCE_LEARNER_SELF,
        granularity=GRANULARITY_SELF_ONLY,
        source="interest_traces（kind の登録簿は core/trace_registry.py）",
        retention=(
            "行削除せず状態遷移で保持します。本人は「地図に反映しない」等で"
            "見え方を訂正でき、全件を JSON で持ち出せます。"
        ),
        k_anonymity=False,
        route="/api/me/records",
        consumer="core/trace_ledger.py::build_ledger_overview",
        design_doc="docs/features/trace_registry_sovereignty_ledger_design.md",
    ),
)


#: id → spec（挿入順 = 公開ビューの並び順）。
INDICATORS: Mapping[str, IndicatorSpec] = MappingProxyType(
    {spec.id: spec for spec in _SPECS}
)


# ---------------------------------------------------------------------------
# 参照ヘルパー
# ---------------------------------------------------------------------------


def all_indicators() -> tuple[IndicatorSpec, ...]:
    """宣言順の全 spec。"""
    return _SPECS


def get_indicator(indicator_id: str) -> IndicatorSpec | None:
    """id で1件引く（未知は None）。"""
    return INDICATORS.get(str(indicator_id or "").strip())


def indicators_for_route(path: str) -> tuple[IndicatorSpec, ...]:
    """公開 API パスに対応する spec（0件以上）。"""
    target = str(path or "").strip()
    return tuple(spec for spec in _SPECS if spec.route == target)


def catalog_public_view() -> list[dict]:
    """カタログの公開表現（**定義のみ**・値を1つも含まない。IG1）。"""
    return [spec.public_dict() for spec in _SPECS]


def validate_catalog() -> None:
    """カタログ全体の整合を検証する（重複 id・重複 route・語彙違反）。

    個々の spec の検証は ``IndicatorSpec.__post_init__`` が行う（宣言時点で落ちる）。
    ここでは**集合としての**整合だけを見る。
    """
    seen_ids: set[str] = set()
    for spec in _SPECS:
        if spec.id in seen_ids:
            raise ValueError(f"indicator id が重複しています: {spec.id!r}")
        seen_ids.add(spec.id)

    seen_routes: dict[str, str] = {}
    for spec in _SPECS:
        if spec.route in seen_routes:
            raise ValueError(
                f"route が重複しています: {spec.route!r} "
                f"（{seen_routes[spec.route]!r} と {spec.id!r}）"
            )
        seen_routes[spec.route] = spec.id

    if set(INDICATORS) != seen_ids:
        raise ValueError("INDICATORS と宣言列が一致していません")


validate_catalog()
