"""段階ラベル（graded scale）と共有語彙訳の**正本**（提案 §2-2）。

``core/privacy.py``（k-匿名ゲート）/ ``core/element_vocab.py``（統制語彙の訳語）に
並ぶ第3の正本。ここに集約するのは

1. **生値 → 段階ラベル**の変換規則（境界値と、情報が無いときにどのラベルへ倒すか）
2. **複数レイヤーで同一の日本語表**（バイト一致していた表）

の2つだけ。生値そのものを API / UI へ出さない原則（W8 / LS5 / FG8 / P7）は各層の
契約のままで、このモジュールは「どの数値をどのラベルに写すか」を1箇所に持つ。

方針:

* **不変条項「情報が無いことを高確度に見せない」**: ``None`` / 数値化できない値は
  必ず**最も慎重な末尾ラベル**へ倒す（``GradedScale.label_for``）。各所に散っていた
  同じ try/except が同じ向きに倒れていることをここで一度だけ保証する。
* **正規化（クランプ / 破棄）は持ち込まない**。生値の正規化は範囲外の扱いが層ごとに
  違う（``core/teaching_figures/schema.py`` は範囲外を ``None`` = 破棄、
  ``core/atlas_gaps/schema.py`` と ``core/landscape/schema.py`` は ``[0, 1]`` へ
  クランプ）ため、意図的に各層の別実装のままにしてある。段階ラベル化と正規化は
  別の判断なので混ぜない。
* **宛先ごとに文言が違う表は統合しない**。``VERIFICATION_STATUS_LABELS_LEDGER``
  （D層 API: 「記帳がある / ない」を主語にする）と
  ``VERIFICATION_STATUS_LABELS_LENS``（W層 位置づけレンズ: 短い状態名）は
  同じキー・別の値であり、**意図された差分**なので別名2表として並べて可視化する
  （どちらかへ寄せると出力文字列が変わる）。
* パーセンタイル型（``core/doubt/schema.py::load_level_for_score``）・k-匿名複合型
  （``core/reconstruction/health.py::rate_level``）・閉世界語彙の事実文
  （``core/doubt/support_paths.py`` の FACT_LINE_*）は段階の決め方が構造的に違うため
  ここへは寄せない。

本モジュールは純粋なデータ + 純粋関数のみ（FastAPI / DB / LLM / A層 agents を
import しない）。唯一の内部依存は ``core.status.schema``（状態語彙の定数。純データ
モジュール同士）で、推移的な純粋性はガードレールの subprocess import 検査が守る。
共有表は ``MappingProxyType`` で不変化してある（別名共有による書き換え事故の防止）。
ガードレールは ``backend/tests/test_label_vocab_guardrails.py``。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from core.status import schema as status_schema

__all__ = [
    "ANCHOR_LANDING_SCALE",
    "ANCHOR_LANDING_THRESHOLD_MID",
    "ANCHOR_LANDING_THRESHOLD_NEAR",
    "ANCHOR_NEARNESS_SCALE",
    "ANCHOR_NEARNESS_THRESHOLD_MID",
    "ANCHOR_NEARNESS_THRESHOLD_NEAR",
    "AUDIO_STATUS_LABELS",
    "CONFIDENCE_LABELS_LOW_MED_HIGH",
    "CONFIDENCE_LABEL_HIGH",
    "CONFIDENCE_LABEL_LOW",
    "CONFIDENCE_LABEL_MEDIUM",
    "CONFIDENCE_LABEL_REFERENCE",
    "CONFIDENCE_LABEL_TENTATIVE",
    "CONFIDENCE_LABEL_TENTATIVE_HIGH",
    "CONFIDENCE_LOW_MED_HIGH",
    "CONFIDENCE_TENTATIVE_REFERENCE_HIGH",
    "CONFIDENCE_THRESHOLD_HIGH",
    "CONFIDENCE_THRESHOLD_MEDIUM",
    "DISCOVERY_RELEVANCE_LABEL_HIGH",
    "DISCOVERY_RELEVANCE_LABEL_LOW",
    "DISCOVERY_RELEVANCE_LABEL_MEDIUM",
    "DISCOVERY_RELEVANCE_SCALE",
    "DISCOVERY_RELEVANCE_THRESHOLD_HIGH",
    "DISCOVERY_RELEVANCE_THRESHOLD_MEDIUM",
    "EDGE_KIND_LABELS",
    "GradedScale",
    "MATERIAL_STATE_LABELS",
    "RADAR_DISTANCE_LABEL_FAR",
    "RADAR_DISTANCE_LABEL_MID",
    "RADAR_DISTANCE_LABEL_NEAR",
    "RADAR_DISTANCE_SCALE",
    "RADAR_DISTANCE_THRESHOLD_MID",
    "RADAR_DISTANCE_THRESHOLD_NEAR",
    "SCRIPT_STATUS_LABELS",
    "SUPPORT_SECTION_LABELS",
    "TRACE_STATUS_LABELS",
    "VERIFICATION_STATUS_LABELS_LEDGER",
    "VERIFICATION_STATUS_LABELS_LENS",
    "WEIGHT_LABELS",
    "WEIGHT_LEVEL_SCALE",
    "WEIGHT_RELATION",
    "WEIGHT_THRESHOLD_MEDIUM",
    "WEIGHT_THRESHOLD_STRONG",
    "WM_INTERACTION_DENSITY",
    "WM_INTERACTION_LABELS",
    "WM_INTERACTION_LEVEL_SCALE",
    "WM_INTERACTION_THRESHOLD_MANY",
    "WM_INTERACTION_THRESHOLD_VERY_MANY",
]


# ---------------------------------------------------------------------------
# 段階スケール
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GradedScale:
    """降順のしきい値と、上から並べたラベルの組。

    ``thresholds`` は降順（``(0.75, 0.5)``）、``labels`` はそれより1つ多く、
    **上位から** 並べる（``("高", "中", "低")``）。末尾ラベルは「最も慎重な」段階で、
    未測定（``None``）・数値化できない値もここへ倒す。
    """

    thresholds: tuple[float, ...]
    labels: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.labels) != len(self.thresholds) + 1:
            raise ValueError("labels は thresholds より1つ多く必要（段階数 = 境界数 + 1）")
        if list(self.thresholds) != sorted(self.thresholds, reverse=True):
            raise ValueError("thresholds は降順で与えること")

    @property
    def cautious_label(self) -> str:
        """最も慎重な段階（未測定・変換不能の行き先）。"""
        return self.labels[-1]

    def label_for(self, value: object) -> str:
        """生値を段階ラベルへ変換する。

        ``None`` / 数値化できない値は :attr:`cautious_label` を返す
        （情報が無いことを高確度に見せない — 全レイヤー共通の不変条項）。
        """
        try:
            if value is None:
                return self.cautious_label
            numeric = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return self.cautious_label
        for threshold, label in zip(self.thresholds, self.labels):
            if numeric >= threshold:
                return label
        return self.cautious_label


# ── confidence（W8 / FG8 / LS5: 生値を出さない）────────────────────────────────
#: 段階の境界。移行前の4実装（teaching_figures / atlas_gaps / identity_links /
#: landscape の weight を除く3つ）はすべて同じ 0.75 / 0.5 を使っていた。
CONFIDENCE_THRESHOLD_HIGH = 0.75
CONFIDENCE_THRESHOLD_MEDIUM = 0.5

CONFIDENCE_LABEL_LOW = "低"
CONFIDENCE_LABEL_MEDIUM = "中"
CONFIDENCE_LABEL_HIGH = "高"
CONFIDENCE_LABELS_LOW_MED_HIGH = (
    CONFIDENCE_LABEL_LOW,
    CONFIDENCE_LABEL_MEDIUM,
    CONFIDENCE_LABEL_HIGH,
)

#: 「低 / 中 / 高」語彙（``core/teaching_figures`` / ``core/atlas_gaps``）。
CONFIDENCE_LOW_MED_HIGH = GradedScale(
    (CONFIDENCE_THRESHOLD_HIGH, CONFIDENCE_THRESHOLD_MEDIUM),
    (CONFIDENCE_LABEL_HIGH, CONFIDENCE_LABEL_MEDIUM, CONFIDENCE_LABEL_LOW),
)

CONFIDENCE_LABEL_TENTATIVE = "暫定"
CONFIDENCE_LABEL_REFERENCE = "参考"
CONFIDENCE_LABEL_TENTATIVE_HIGH = "確度高"

#: 「暫定 / 参考 / 確度高」語彙（W層の同一性リンク）。同じ境界・別の語彙で、
#: 「低」と言い切らずに確度の低さを表す（``core/deliberation/identity_links.py``）。
CONFIDENCE_TENTATIVE_REFERENCE_HIGH = GradedScale(
    (CONFIDENCE_THRESHOLD_HIGH, CONFIDENCE_THRESHOLD_MEDIUM),
    (
        CONFIDENCE_LABEL_TENTATIVE_HIGH,
        CONFIDENCE_LABEL_REFERENCE,
        CONFIDENCE_LABEL_TENTATIVE,
    ),
)


# ── 論文ディスカバリーの関連度（PD4: 数値スコアを教員にも見せない）──────────────
# 候補論文のアブストラクト埋め込みと、分野の取り込み済みコーパス重心との
# **cosine 類似度**（-1〜1）の段階化。生値は API / UI へ出さず、並び順とこのラベル
# だけを見せる（``core/paper_discovery/ranking.py``、設計書 §6）。
#
# 閾値は発明値（実測データ非由来）。参考にしたのは help_kb ベクトル補助層の
# ``_MAX_COSINE_DISTANCE = 0.55``（= cosine 類似度 0.45 未満は「なんとなく関連」で
# 捏造に見えるため足切りする、という同一モデル族での保守的な判断）。ここでは
# 足切りはせず（PD6 — 候補を黙って消さない）、
#   * 0.45 以上 = help_kb が「提示してよい」とした水準       → 「関連: 高」
#   * 0.30 以上 = 同語彙圏だが主題が離れうる水準             → 「関連: 中」
#   * それ未満・未測定                                       → 「関連: 低」
# とする。実測での見直し前提（変えるときは設計書 §6 も更新する）。
DISCOVERY_RELEVANCE_THRESHOLD_HIGH = 0.45
DISCOVERY_RELEVANCE_THRESHOLD_MEDIUM = 0.30

DISCOVERY_RELEVANCE_LABEL_HIGH = "関連: 高"
DISCOVERY_RELEVANCE_LABEL_MEDIUM = "関連: 中"
DISCOVERY_RELEVANCE_LABEL_LOW = "関連: 低"

DISCOVERY_RELEVANCE_SCALE = GradedScale(
    (DISCOVERY_RELEVANCE_THRESHOLD_HIGH, DISCOVERY_RELEVANCE_THRESHOLD_MEDIUM),
    (
        DISCOVERY_RELEVANCE_LABEL_HIGH,
        DISCOVERY_RELEVANCE_LABEL_MEDIUM,
        DISCOVERY_RELEVANCE_LABEL_LOW,
    ),
)


# ── 論文レーダーの距離帯（PR2: 段階ラベルのみ・測れないものにラベルを付けない）────
# seed 教材のチャンク重心（または seed 論文要旨）と候補アブストラクトの **cosine
# 類似度**の段階化（``core/paper_discovery/ranking.py::band_candidates``、正本
# ``docs/features/paper_radar_design.md`` §5.2）。分野重心の代わりに教材1件の重心を
# 使うだけで、写す数値の意味は :data:`DISCOVERY_RELEVANCE_SCALE` と同じなので、
# **閾値も同じ 0.45 / 0.30 を初期値に採用する**（発明値・実測見直し前提。ヒストグラムを
# 見て変えるときは設計書 §9 も更新する）。
#
# **未測定（``None``）はこのスケールに通さない**（PR2）。:class:`GradedScale` の慎重側
# フォールバックはここでは偽装になる — 「測れなかった」と「遠い」は別の事実であり、
# 未測定候補には ``distance_label`` キー自体を付けない（呼び出し側
# ``ranking.band_candidates`` が ``None`` を弾いてからラベルを引く）。
RADAR_DISTANCE_THRESHOLD_NEAR = 0.45
RADAR_DISTANCE_THRESHOLD_MID = 0.30

RADAR_DISTANCE_LABEL_NEAR = "近い"
RADAR_DISTANCE_LABEL_MID = "中間"
RADAR_DISTANCE_LABEL_FAR = "遠い"

RADAR_DISTANCE_SCALE = GradedScale(
    (RADAR_DISTANCE_THRESHOLD_NEAR, RADAR_DISTANCE_THRESHOLD_MID),
    (
        RADAR_DISTANCE_LABEL_NEAR,
        RADAR_DISTANCE_LABEL_MID,
        RADAR_DISTANCE_LABEL_FAR,
    ),
)


# ── 骨格アンカーへの近さ（分野マップのベクトル係留層 VA2）──────────────────────
# 骨格ノード（region / concept）の**プロトタイプベクトル**と、論文重心 / 候補
# アブストラクト / ギャップ候補ラベルとの **cosine 類似度**の段階化（正本
# ``docs/features/atlas_vector_anchoring_design.md`` §9、算出は
# ``core/atlas_vectors/query.py``）。cosine の生値は DB / 内部計算に留め、外へ出るのは
# このスケールのラベルだけ（VA2 数値非表示）。
#
# 閾値は :data:`DISCOVERY_RELEVANCE_SCALE` より**高く**取る（0.55 / 0.40）。あちらは
# 「候補を捨てずに並べ替える」ための相対順位づけだが、こちらは「地図のこのノードの
# 近くに落ちる」という**係留の言明**であり、外すと閉世界の正直さ（VA8）を損なうため。
# 0.55 は help_kb ベクトル補助層の保守的足切り（``_MAX_COSINE_DISTANCE = 0.55``）と
# 同じ「提示してよい」水準に合わせた発明値で、実測での見直し前提
# （変えるときは設計書 §9 も更新する）。
#
# 使い分け（設計書 §9）: ギャップ近傍注記は最上位帯（NEAR 以上）のみ表示、着地予測は
# 上位2帯（MID 以上）を表示し、最下帯は表示しない（「なんとなく関連」を出さない）。
ANCHOR_NEARNESS_THRESHOLD_NEAR = 0.55
ANCHOR_NEARNESS_THRESHOLD_MID = 0.40

ANCHOR_NEARNESS_SCALE = GradedScale(
    (ANCHOR_NEARNESS_THRESHOLD_NEAR, ANCHOR_NEARNESS_THRESHOLD_MID),
    ("かなり近い", "近い可能性", "遠い"),
)

# ── アンカー着地予測（論文テキスト × アンカープロトタイプ）───────────────────
#
# :data:`ANCHOR_NEARNESS_SCALE` と**レジームが違う**ための別表。あちらは
# ラベル×ラベル（gap クラスタ label とアンカー合成テキスト — 双方日本語の短文）で、
# 0.55/0.40 が妥当。こちらは論文由来テキスト（英語アブスト・チャンク重心）×
# アンカープロトタイプ（日本語ラベル中心の合成テキスト）の**言語間・長短文比較**で、
# cosine の絶対水準が一段下がる。2026-08-29 の実測校正（astrophysics 骨格 59 アンカー ×
# 実レーダー候補20件）: 主題が合う候補の最良アンカー cosine は 0.34〜0.38 で、
# 最近接アンカーは意味的に正しかった（CMB複屈折→cmb / LSS重力→cosmology）。
# 主題が違う候補は 0.21〜0.29。旧閾値 0.55/0.40 ではこのレジームで一度も発火しない。
# 閾値は境界の雑音帯（0.28〜0.34）を「近い可能性」止まりにする保守側で置く。
# 実測での見直し前提は継承（変えるときは atlas_vector_anchoring_design.md §9 も更新）。
ANCHOR_LANDING_THRESHOLD_NEAR = 0.36
ANCHOR_LANDING_THRESHOLD_MID = 0.30

ANCHOR_LANDING_SCALE = GradedScale(
    (ANCHOR_LANDING_THRESHOLD_NEAR, ANCHOR_LANDING_THRESHOLD_MID),
    ("かなり近い", "近い可能性", "遠い"),
)

# ── 骨格の辺種別（SkeletonEdge.kind → 日本語）──────────────────────────────────
#
# 正本は core/atlas.py::EDGE_KINDS（adjacent / depends / related）。表示語彙は
# ここが唯一の定義（atlas_relation_edges_design.md §8。フロントはミラー規律で追随）。
EDGE_KIND_LABELS = MappingProxyType(
    {
        "adjacent": "隣接",
        "depends": "依存",
        "related": "関連",
    }
)


# ── 関連の強さ（知識ランドスケープの weight, LS5）──────────────────────────────
WEIGHT_THRESHOLD_STRONG = 0.7
WEIGHT_THRESHOLD_MEDIUM = 0.4

#: 段階キー側（``strong / medium / weak``）。DTO のキーは日本語にしないため、
#: ラベル表（:data:`WEIGHT_RELATION`）と対で持つ。
WEIGHT_LEVEL_SCALE = GradedScale(
    (WEIGHT_THRESHOLD_STRONG, WEIGHT_THRESHOLD_MEDIUM),
    ("strong", "medium", "weak"),
)

#: 表示側（「強い関連 / 関連 / 弱い関連」）。
WEIGHT_RELATION = GradedScale(
    (WEIGHT_THRESHOLD_STRONG, WEIGHT_THRESHOLD_MEDIUM),
    ("強い関連", "関連", "弱い関連"),
)

WEIGHT_LABELS = MappingProxyType(dict(zip(WEIGHT_LEVEL_SCALE.labels, WEIGHT_RELATION.labels)))


# ── 要素相互作用性（WMレンズ, 教員支援 Phase 4 §3.2）─────────────────────────────
# スライド内で同時に現れる「相互依存する記号 + 数式」の密度（決定論スコア =
# 突合できた distinct 記号数 + 数式件数, ``core/lecture_wm.py``）の段階化。
# 固定閾値型なのでここが正本（パーセンタイル型の D層 load は ``core/doubt/schema.py``
# 側 — §27 の住み分けどおり寄せない）。末尾（few = 少ない）が最も慎重な段階で、
# 未測定はここへ倒れ、WMレンズは few のとき表示自体を省略する（「平常時は視界に無い」）。
# 閾値は発明値（実測データ非由来 — 設計書 §6②の宣言）: ワーキングメモリ容量の目安
# ~4±1 チャンクを超え始める 5 を many、その約2倍（レビューで見直す上位段）の 9 を
# very_many とした。実測での見直し前提。値を変えるときは設計書 §6 も更新する。
WM_INTERACTION_THRESHOLD_VERY_MANY = 9
WM_INTERACTION_THRESHOLD_MANY = 5

#: 段階キー側（``very_many / many / few``）。DTO のキーは日本語にしない。
WM_INTERACTION_LEVEL_SCALE = GradedScale(
    (WM_INTERACTION_THRESHOLD_VERY_MANY, WM_INTERACTION_THRESHOLD_MANY),
    ("very_many", "many", "few"),
)

#: 表示側（「非常に多い / 多い / 少ない」）。
WM_INTERACTION_DENSITY = GradedScale(
    (WM_INTERACTION_THRESHOLD_VERY_MANY, WM_INTERACTION_THRESHOLD_MANY),
    ("非常に多い", "多い", "少ない"),
)

WM_INTERACTION_LABELS = MappingProxyType(
    dict(zip(WM_INTERACTION_LEVEL_SCALE.labels, WM_INTERACTION_DENSITY.labels))
)


# ---------------------------------------------------------------------------
# 共有語彙表（複数レイヤーでバイト一致していたもの）
# ---------------------------------------------------------------------------

#: ``ThesisReconstructionAgent`` の ``support_structure`` セクション名
#: （``agents/thesis_reconstruction/schema.py::SUPPORT_SECTIONS`` が語彙の正本）の
#: 日本語ラベル。W層 位置づけ / W層 文脈レンズ / discuss 開幕の3箇所が同じ表を
#: 持っていた（未知のセクション名はそのまま表示する — 呼び出し側の ``.get`` 既定）。
SUPPORT_SECTION_LABELS = MappingProxyType({
    "direct_supports": "直接支持",
    "assumptions": "前提",
    "derivation_core": "導出の核",
    "correction_sources": "訂正の源",
    "uncertainty_sources": "不確実性の源",
    "diagnostic_consequences": "診断的帰結",
    "future_requirements": "将来要件",
})

#: ``interest_traces.status``（語彙の正本は ``api/services.py::_TRACE_STATUSES``、
#: kind ごとの使用宣言は ``core/trace_registry.py``）の日本語ラベル。
#: 台帳「わたしの記録」（``core/trace_ledger.py``）の status 表示が使う。
#: ``dismissed`` / ``superseded`` は行削除ではなく保持を明示する文言（P4）、
#: ``revisited`` / ``abstracted`` は書き込み経路が現存しない dead 語彙だが
#: 既存行の表示のために保持する（TR3）。
TRACE_STATUS_LABELS = MappingProxyType({
    "open": "未解決",
    "revisited": "再訪",
    "resolved": "解決済み",
    "candidate": "AIの候補",
    "dismissed": "見送り（保持）",
    "articulated": "言葉にした",
    "connected": "つないだ",
    "abstracted": "抽象化",
    "superseded": "書き直しで差し替え",
})

#: ``epistemic_ledger.verification_status``（migration 029 の CHECK 語彙）の
#: **D層 API 向け**文言。SL1 の閉世界語彙に合わせ「記帳がある / ない」を主語にする
#: （``api/routes/doubt.py``）。
VERIFICATION_STATUS_LABELS_LEDGER = MappingProxyType({
    "directly_verified": "直接検証の記帳あり",
    "indirectly_supported": "間接的な支持あり",
    "untested": "未検証",
    "refuted": "反証の記帳あり",
    "unknown": "検証情報なし",
})

#: 同じキーの **W層 位置づけレンズ向け**文言（短い状態名）。台帳画面ではなく要素の
#: 周辺情報として1行に添えるため語が短い（``core/deliberation/positioning.py``）。
#: :data:`VERIFICATION_STATUS_LABELS_LEDGER` との差は**意図された宛先差**なので
#: 統合しない（統合すると既存の出力文字列が変わる）。
VERIFICATION_STATUS_LABELS_LENS = MappingProxyType({
    "directly_verified": "直接検証済み",
    "indirectly_supported": "間接的に支持",
    "untested": "未検証",
    "refuted": "反証あり",
    "unknown": "不明",
})


# ── 状態投影（core/status/schema.py の語彙）の日本語訳 ─────────────────────────
# 語彙の正本は ``core/status/schema.py``、訳語の正本はここ。Admin Copilot の
# guidance 応答（``api/routes/admin_assistant.py``）が使う。

MATERIAL_STATE_LABELS = MappingProxyType({
    status_schema.MATERIAL_STATE_UPLOADED: "アップロード済み（未解析）",
    status_schema.MATERIAL_STATE_CHUNKING: "解析待ち",
    status_schema.MATERIAL_STATE_ANALYZING: "解析実行中",
    status_schema.MATERIAL_STATE_ANALYZED: "解析完了",
    status_schema.MATERIAL_STATE_ANALYSIS_FAILED: "解析失敗",
    status_schema.MATERIAL_STATE_UNKNOWN: "状態不明",
})

SCRIPT_STATUS_LABELS = MappingProxyType({
    status_schema.SCRIPT_STATUS_DRAFT: "未生成",
    status_schema.SCRIPT_STATUS_PARTIAL: "一部生成",
    status_schema.SCRIPT_STATUS_GENERATED: "生成済み",
})

AUDIO_STATUS_LABELS = MappingProxyType({
    status_schema.AUDIO_STATUS_NONE: "未生成",
    status_schema.AUDIO_STATUS_PARTIAL: "一部生成",
    status_schema.AUDIO_STATUS_GENERATED: "生成済み",
})
