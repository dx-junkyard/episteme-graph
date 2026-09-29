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
    "AI_READING_LABEL",
    "ANCHOR_LANDING_SCALE",
    "ANCHOR_LANDING_THRESHOLD_MID",
    "ANCHOR_LANDING_THRESHOLD_NEAR",
    "ANCHOR_NEARNESS_SCALE",
    "ANCHOR_NEARNESS_THRESHOLD_MID",
    "ANCHOR_NEARNESS_THRESHOLD_NEAR",
    "CHALLENGE_MODE_LABELS",
    "CITATION_INTENT_LABELS",
    "COMPLEMENT_SKY_THRESHOLD",
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
    "EVIDENCE_LINE_KIND_LABELS",
    "GradedScale",
    "LEARNING_STANCE_LABELS",
    "MATERIAL_STATE_LABELS",
    "RADAR_DISTANCE_LABEL_FAR",
    "RADAR_DISTANCE_LABEL_MID",
    "RADAR_DISTANCE_LABEL_NEAR",
    "RADAR_DISTANCE_SCALE",
    "RADAR_DISTANCE_THRESHOLD_MID",
    "RADAR_DISTANCE_THRESHOLD_NEAR",
    "RECONSTRUCTED_EQUATION_NOTE",
    "RECONSTRUCTED_EQUATION_MARK",
    "REVIEW_PENDING_LABEL",
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


# ── コーパスを補う論文 — 検証記録の無い前提との近さ（CC4: 数値非表示）─────────
# 台帳で ``untested`` かつスコープ空欄の前提文（assumption / claim 本文）と候補
# アブストラクトの **cosine 類似度**の足切り（``core/paper_discovery/complement.py``、
# 正本 ``docs/features/corpus_complement_design.md`` §5.2）。段階ラベルは作らず
# 「近い内容を扱っている可能性がある」の**有無だけ**を判定する（表は持たない）。
# 自然文×自然文の同一レジームなので :data:`DISCOVERY_RELEVANCE_THRESHOLD_HIGH`
# （help_kb が「提示してよい」とした水準）と同じ 0.45 を初期値に採用する（発明値・
# 実測見直し前提。変えるときは設計書 §5.2 も更新する）。未測定（``None``）は不一致扱い。
COMPLEMENT_SKY_THRESHOLD = 0.45


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


# ---------------------------------------------------------------------------
# 対話応答の姿勢ラベル（W層 / グラフ対話レビュー）
# ---------------------------------------------------------------------------
#
# 文ごとの留保（「〜の可能性があります」の反復）をやめ、**返答全体に1つ付く固定
# ラベル**で不確かさを示す（オーナー裁定 2026-09-10。正本は
# ``docs/features/graph_dialogue_review_design.md`` §15）。段階スケールではなく
# 単一の固定文字列なので表を作らない — この1箇所だけが正本で、
# ``core/deliberation/{dialogue,graph_dialogue}.py`` と route 層は import して使う。

AI_READING_LABEL = "AIの読み（未確認）"

#: 「このグラフにはまだ人が見ていない要素が残っている」ことを示す短いラベル
#: （2026-09-22・オーナー指摘。graph_dialogue_review_design.md §18）。
#:
#: 以前は AI が応答の末尾に「未レビューのノードや式詳細は多数ありますが……」という
#: 但し書きを散文で書いていた。この画面の目的はグラフの概要をつかむことなので、
#: 網羅性の但し書きは本文から外し、画面の1枚のラベルが引き受ける（件数は出さない = GR3）。
#: AI_READING_LABEL と同じく単一の固定文字列なので表は作らない。JS 側は逐語ミラー
#: （``frontend/public/js/admin-graph-review.js``。一致は ui_static テストが固定）。

REVIEW_PENDING_LABEL = "未レビューの要素あり"


# ---------------------------------------------------------------------------
# 復元された数式の事実文（学習者向け）
# ---------------------------------------------------------------------------
#
# PDF から取り出した数式テキストは記号・添字が壊れているため、パイプラインは
# LLM に LaTeX を**復元**させる（``src/episteme_graph/agents/equation_semantics``）。
# 復元式は原文の数式と機械照合できていないので、学習者に出すときは同じ顔で出さず
# 1行の事実として添える。数値（confidence）は出さない — 出るのはこの1文だけ。
#
# AI_READING_LABEL と同じく単一の固定文字列なので表は作らない。ここが正本で、
# ``core/{element_context,component_context,learner_context_common}.py`` が import
# して使う（JS 側に日本語をミラーしない）。

RECONSTRUCTED_EQUATION_NOTE = "AI が文脈から復元した式です（原文の数式とは未照合）"

#: 教材本文の式に添える短い印（バッジ）。文は ``RECONSTRUCTED_EQUATION_NOTE`` が言う。
#: ``core/lecture.py::annotate_reconstructed_formulas`` が chunks.formulas の投影に
#: ``reconstructed_mark`` / ``reconstructed_note`` として載せ、JS は素通しで描く
#: （JS に日本語を直書きしない — 2026-09-19 レビュー）。
RECONSTRUCTED_EQUATION_MARK = "AI復元"


# ---------------------------------------------------------------------------
# 教材がまだ解説になっていないときの事実文（IK-0375）
# ---------------------------------------------------------------------------
#
# ``GET /api/learning/courses/{id}/topics/{tid}/material`` は、トピックの解説
# （``student_material``）がまだ無いと論文の本文（PDF 由来チャンク）をそのまま返す。
# 何の説明も無く論文の表紙が出ると学習者はコースが壊れていると読むので、DTO の
# ``preparation_notice`` にどちらか1文を添える（数字を入れない）。
# 書き分けはコースの ``course_content_status.status`` だけで決める:
#   - 生成中（``COURSE_CONTENT_PREPARING_STATUSES``）→ 準備中
#   - それ以外（未記録・完了だがこのトピックの解説が無い・パイプライン待ち・失敗）→ 未生成
# 読み手は ``api/routes/learning.py::get_topic_material``。JS に日本語をミラーしない
# （サーバの文をそのまま描く）。

MATERIAL_PREPARING_NOTICE = (
    "この教材は準備中です。解説ができるまで、論文の本文をそのまま表示しています。"
)
MATERIAL_NOT_GENERATED_NOTICE = (
    "解説は生成されていません。論文の本文をそのまま表示しています。"
)

#: 教材本文の数式プレースホルダー（``[[FORMULA_N]]``）が、そのトピックに結びついた式の
#: どれにも引けなかったときに置き換える文（IK-0389。学習者に生のプレースホルダーを
#: 見せない）。数値・内部 ID は入れない。
UNRESOLVED_FORMULA_PLACEHOLDER_TEXT = "（この数式は教材に載せられていません）"  # 第 9 周: 「出典の区画」は画面に無い場所を指していた

#: 上の置き換えをしたトピックに残す事実文（``topic.grounding_note`` / ``coverage``）。
UNRESOLVED_FORMULA_GROUNDING_NOTE = (
    "本文の数式の一部は、このトピックに結びついた式から引けなかったため、"
    "出典の区画を参照する表示にしています。"
)

#: 教材本文の ``![[component:…]]`` / ``![[claim:…]]`` / ``![[source:…]]`` が、そのトピックに
#: 結びついた根拠のどれにも引けず、埋め込みを外したトピックに残す事実文（IK-0455。
#: 学習者に生の埋め込み記法・内部 ID を見せない）。件数・内部 ID は入れない。
UNRESOLVED_EMBED_GROUNDING_NOTE = (
    "本文が参照していた根拠の一部は、このトピックに結びついた根拠から引けなかったため、"
    "参照を外して表示しています。"
)

#: 教員が選んだ学ぶ単位のうち、論文の解析結果の主張・式・図・部品のどれにも
#: 結びつかなかったものがあるトピックに残す事実文（IK-0456）。章の本文（出典の区画）は
#: 結びついていても、構造化された根拠は無いことを正直に言う。件数は入れない。
UNITS_WITHOUT_KNOWLEDGE_NOTE = (
    "選んだ学ぶ単位の一部には、論文の解析結果の主張・式・図が対応付けられていません"
    "（その部分は原文の区画だけを根拠にしています）。"
)


# ---------------------------------------------------------------------------
# 受講登録が成立した事実文（IK-0385）
# ---------------------------------------------------------------------------
#
# ``POST /api/learning/courses/{id}/enroll`` の応答は一覧行と同じ投影で、登録後は
# ``is_enrollable: false`` になる（「まだ受講していない公開コース」ではなくなるため）。
# その値だけを見ると「受講できなかった」と読めるので、成立した事実を1文で添える。
# 読み手は ``api/routes/learning.py::enroll_course``（``LearningEnrollOut.notice``）。

COURSE_ENROLLED_NOTICE = "受講を開始しました。コースの最初のトピックから読めます。"


# ---------------------------------------------------------------------------
# 学習チャットの事実文（IK-0396 / IK-0397）
# ---------------------------------------------------------------------------
#
# 読み手は ``api/routes/learning.py``（学習チャット本体）。
# JS に日本語をミラーしない（サーバの文をそのまま描く）。数値を入れない。

#: 前提確認の逆質問に「理解している」と答えた往復で、記帳したうえで元の質問に答える
#: ときに回答の先頭へ添える1行（IK-0396）。
PREREQUISITE_ACK_RESUME_NOTICE = "前提の確認を記録しました。元の質問に答えます。"
#: 同上の英語（発話にかな・漢字を含まない往復だけ。IK-0450）。
PREREQUISITE_ACK_RESUME_NOTICE_EN = (
    "Your confirmation of the prerequisite has been recorded. Here is the answer to your original question."
)

#: 逆質問は (トピック, セッション) につき1回（IK-0422）。同じトピックで既に逆質問を出した
#: あと（書き直しを含む）、前提の確認が記録されていないまま次の問いが来たとき、逆質問を
#: 出し直さずに答える往復の先頭へ添える1行。``{prerequisite}`` は前提の表示名。
PREREQUISITE_GATE_SKIPPED_NOTICE = "前提「{prerequisite}」の確認はまだ記録していません。そのまま答えます。"
#: 同上の英語（発話の大半がラテン文字の往復だけ。IK-0424）。
PREREQUISITE_GATE_SKIPPED_NOTICE_EN = (
    'The prerequisite "{prerequisite}" has not been confirmed yet, so this answers your question directly.'
)
#: 逆質問（日本語の定型文は ``services.check_prerequisites`` が正本）に英語の発話で
#: 来たとき、末尾へ添える英語の1文（IK-0424）。答え方の例は英語の確認判定が受理する形。
PREREQUISITE_GATE_EN = (
    'This topic builds on the prerequisite "{prerequisite}" — reply "Yes, I understand" to continue, '
    'or "No, please explain {prerequisite} first".'
)

#: 学習者が自分の理解度を点数・割合で求めたときの固定応答（IK-0397。非LLM・
#: 数値非表示の原則を事実として述べる）。
UNDERSTANDING_SCORE_REQUEST_REPLY = (
    "このシステムは理解度を点数や割合では示しません。代わりに、いま説明できることを"
    "自分の言葉で書いてみると、どこが曖昧かが見えます。"
)
#: 同上の英語（発話にかな・漢字を含まない往復だけ。IK-0450）。
UNDERSTANDING_SCORE_REQUEST_REPLY_EN = (
    "This system does not express understanding as a score or percentage. Instead, try writing "
    "down in your own words what you can explain now; that shows where things are still unclear."
)

#: お礼・締めくくりだけの発話（「ありがとうございました、今日はここまでにします」
#: "Thank you, that is very helpful!"）への固定応答（IK-0434。非LLM・検索なし・出典なし）。
#: 問い返しを付けない（学習者が終えると言っている往復に次の問いを課さない）。
CLOSING_UTTERANCE_REPLY = "お疲れさまでした。続きはいつでも同じ画面から再開できます。"
#: 同上の英語（発話の大半がラテン文字の往復だけ。IK-0424 と同じ基準）。
CLOSING_UTTERANCE_REPLY_EN = "Thank you for today. You can pick up where you left off from this same screen at any time."

#: 引っかかりの候補（tension digest）が1件も無いときの事実文（IK-0437）。候補は会話の
#: あとに非同期で作られるので、「無い」ことと「これから作られうる」ことだけを言う（数値なし）。
TENSION_DIGEST_EMPTY_FACT = (
    "いま確認を待っている引っかかりの候補はありません。候補は会話のあと、少し時間をおいて作られることがあります。"
)
#: 問いの帰属の候補（anchors digest）が1件も無いときの事実文（IK-0437）。
ANCHOR_DIGEST_EMPTY_FACT = (
    "いま確認を待っている「問いがどこについてだったか」の候補はありません。"
    "候補は会話のあと、少し時間をおいて作られることがあります。"
)

#: 構造帰属（「この疑問は◯◯についてでしたか？」）を本人が確定・外したときの応答の
#: 事実文（IK-0411。数値・内部 ID・AI の理由文を返さない代わりに、何が起きたかだけを言う）。
ANCHOR_CONFIRMED_NOTICE = "この問いがどこについてだったかを記録しました。"
ANCHOR_DISMISSED_NOTICE = "この候補を外しました。問いそのものは残っています。"

#: 確認問題の自己確認で「違っていた」を押したときの事実文（IK-0399）。
#: 「違っていた」は本人の見立てが要件と合わなかったという申告で、完了の確定には使わない。
CHECK_SELF_CHECK_DISAGREED_NOTICE = (
    "見立てが違っていたことを記録しました。このトピックはまだ完了にしていません。"
    "要件と見比べて書き直すと、もう一度確かめられます。"
)
#: 同上の英語（このトピックの直近の発話にかな・漢字を含まない学習者だけ。IK-0450）。
CHECK_SELF_CHECK_DISAGREED_NOTICE_EN = (
    "Recorded that your reading did not match. This topic is not marked as complete yet. "
    "Compare it with the requirements and rewrite it to check again."
)


# ---------------------------------------------------------------------------
# リリース前の確認で「確認しなかった教材」がある理由の事実文（IK-0376）
# ---------------------------------------------------------------------------
#
# ``POST /api/admin/landscape/courses/{id}/placements/accept`` は、edit 権限の無い
# ソース論文を 403 にせず静かに対象外にする（RR7）。件数（``skipped_documents``）だけ
# だと「確認済み 0・飛ばし 2」を失敗と読まれるので、理由ごとの1文を
# ``skipped_note`` に添える（該当する理由があるときだけ・数字を入れない）。
# 読み手は ``api/routes/landscape.py::accept_course_landscape_placements``。

#: 閲覧はできるが編集権限の無い教材（例: 他の教員が所有する公開教材）を飛ばしたとき。
RELEASE_SKIPPED_NOT_EDITABLE_NOTE = (
    "あなたに編集権限の無い教材の位置づけは、ここでは確認できません（教材の所有者が確認します）。"
)
#: 閲覧できない、または見つからないソース教材を飛ばしたとき。
RELEASE_SKIPPED_NOT_VISIBLE_NOTE = (
    "閲覧できない教材、または見つからない教材の位置づけは、ここでは確認できません。"
)


# ---------------------------------------------------------------------------
# 学習チャットの様相ラベル（Phase 1 入口統合）
# ---------------------------------------------------------------------------
#
# 語彙（enum）の正本は ``core/learning_stance/schema.py`` の ``STANCES``、
# **表示ラベルの正本はここ**（正本設計書
# ``docs/features/learning_chat_entry_unification_design.md`` §5）。
# フロントは ``label + "答えました。"`` を描くだけで日本語表を持たない
# （JS 側にこの表をミラーしない = サーバが解決済みの文字列を返す）。
# 数値・confidence は持たない（LC7）。

LEARNING_STANCE_LABELS = MappingProxyType({
    "tutor": "ふつうの質問として",
    "casual_light": "気軽な調子で",
    "discuss": "議論として",
    "cycle_elicit": "予想を先に聞く形で",
    "cycle_diff": "予想と照らし合わせる形で",
})


# ---------------------------------------------------------------------------
# 学ぶ単位の種別ラベル（Phase 2 / learning_units）
# ---------------------------------------------------------------------------
#
# 語彙（enum）の正本は ``core/schema.py::LEARNING_UNIT_KINDS``、**表示ラベルの
# 正本はここ**（正本設計書 ``docs/features/learning_units_design.md`` §4.1）。
# migration 081 の ``knowledge_unit_kinds`` シードもこの文字列と一致させる
# （一致は ``backend/tests/test_learning_units_guardrails.py`` が固定する）。
# 数値・confidence は持たない（LU5）。

LEARNING_UNIT_KIND_LABELS = MappingProxyType({
    "section_block": "章立ての論理ブロック",
    "thesis_support": "中心命題と支持構造",
    "parent_component": "理論の部品（原案）",
    "dsl_node": "概念ノード",
    "figure": "図",
})


# ---------------------------------------------------------------------------
# D層・C層の表現語彙（知識の転用層 Phase 4 / migration 083）
# ---------------------------------------------------------------------------
#
# 語彙（enum）の正本は
#   - ``core/doubt/schema.py::CHALLENGE_MODES``（疑義の向き・X-6）
#   - ``core/doubt/schema.py::EVIDENCE_LINE_KINDS``（根拠の線・X-5）
#   - ``core/schema.py::CITATION_INTENTS``（引用の意図・X-7）
# **表示ラベルの正本はここ**（正本設計書
# ``docs/features/knowledge_transfer_design.md`` §8）。migration 083 の CHECK も
# 同じ文字列で書く（一致は ``backend/tests/test_doubt_citation_vocab_*.py`` が固定）。
# 数値・confidence は持たない（KT7）。フロント（doubt-atlas.js /
# admin-lecture-studio.js）は逐語ミラー + mirror テストで固定する（第2波）。

#: 疑義の向き（direct = 主張そのもの / undercut = 主張と根拠のつながり）。
#: 主語は常に「どこへ向けた疑義か」であって人ではない（D層 §8-3 を継承）。
CHALLENGE_MODE_LABELS = MappingProxyType({
    "direct": "主張そのものへ",
    "undercut": "主張と根拠のつながりへ",
})

#: 根拠の線の種別（どの経路で支えられているか）。
EVIDENCE_LINE_KIND_LABELS = MappingProxyType({
    "observation": "観測",
    "derivation": "導出",
    "external_reference": "外部文献",
    "consistency": "整合性",
})

#: 引用の意図（CiTO の最小語彙）。NULL = 記録なし（ラベルを持たない）。
CITATION_INTENT_LABELS = MappingProxyType({
    "uses_as_evidence": "根拠として使う",
    "extends": "発展させる",
    "qualifies": "条件を付ける",
    "contrasts_with": "対比する",
    "cites_for_background": "背景として引く",
})


# ---------------------------------------------------------------------------
# 分野の地図（atlas）学習者向けの語彙（IK-0412。ペルソナ通し受講 第 8 周）
# ---------------------------------------------------------------------------
# 台帳（コーパスの論文からの記帳）に何も無い場所の状態語。骨格 seed 由来の弱い
# 初期表示（``status_source='seed'``）と、コーパス由来の状態が無い場所をこの1語に
# 寄せ、「検証: 台帳に記帳なし」の文と ``ledger_status`` の値を食い違わせない。
#: ``GET /api/atlas`` の ``ledger_status`` に載る値（語彙の正本は ``core/atlas_state.py``）。
ATLAS_LEDGER_STATUS_UNRECORDED = "unrecorded"
#: 上の状態のピル表示。
ATLAS_PILL_UNRECORDED = "台帳に記帳なし"
#: 骨格 seed が「原文に裏付け」を初期表示に使っている場所のピル（台帳に引用が無いのに
#: 「原文に裏付け」と読ませない）。
ATLAS_PILL_SEED_VERIFIED = "骨格の初期表示（台帳に記帳なし）"

#: ピルの意味を1行で言う事実文（表示状態 → 文）。評価語・誘導を含めない
#: （``atlas_state.find_evaluative_language`` の対象）。
ATLAS_PILL_NOTES = MappingProxyType({
    "assumed": (
        "この概念を前提として使う議論がありますが、その前提自体を直接確かめた記録は"
        "台帳にありません。"
    ),
    "gap": "原文に対応する記述が無く、導出をつなぐために AI が補った箇所です。",
    "contested": "この概念について、帰属つきの解釈が複数並んでいます。",
    "unrecorded": "この場所について、コーパスの論文からの記帳はまだありません。",
    "fog": "この領域には、まだコーパスの論文が関係づけられていません。",
})

#: 分野（atlas ドメイン）の表示名が登録されていないときの学習者向けの呼び名。
#: domain_key（内部キー）を表示名の代わりに出さない。
ATLAS_DOMAIN_UNNAMED_LABEL = "名前が登録されていない分野"


# ---------------------------------------------------------------------------
# わたしの記録（主権台帳）の kind 別 status ラベル（IK-0415）
# ---------------------------------------------------------------------------
# ``TRACE_STATUS_LABELS`` は status 語だけで決まる共通表で、``open`` を一律「未解決」と
# 読ませていた。kind によって ``open`` の意味が違う（誤解の記録 = 回答が訂正を示した /
# 学習の意図 = 残してある / 軽量アンカー = 付けた）ため、kind ごとの上書きをここに置く。
# 載っていない (kind, status) は共通表へ落ちる。
TRACE_STATUS_LABELS_BY_KIND = MappingProxyType({
    "misconception": MappingProxyType({
        # 誤解の記録は、回答が訂正を示したときに残る AI 検出の記録。本人が確かめた
        # 「訂正済み」ではない（是正 F5 — 本人確定は誤解メモの側）。
        "open": "回答で訂正の指摘あり（あなたの確認前）",
    }),
    "intention": MappingProxyType({
        "open": "残してある",
        "superseded": "新しい記録で差し替え",
    }),
    "anchor_mark": MappingProxyType({"open": "付けた印"}),
    "frontier_interest": MappingProxyType({"open": "関心を記録"}),
    "help_usage": MappingProxyType({"open": "記録済み"}),
    "raw": MappingProxyType({"open": "記録済み"}),
})

#: 学習の意図（``interest_traces.kind='intention'``）の役割ラベル。役割語彙の正本は
#: ``core/cycle/schema.py::INTENTION_ROLES``（一致はテストで固定）。
INTENTION_ROLE_LABELS = MappingProxyType({
    "opening_motive": "開いた動機",
    "carryover_question": "持ち越した問い",
    "revisit_answer": "持ち越した問いへの答え",
    "leave_note": "次の自分への書き置き",
})


# ---------------------------------------------------------------------------
# 授業用ドラフトの事実文（IK-0440 / IK-0441 / IK-0443）
# ---------------------------------------------------------------------------
#
# 書き手は ``core/course_content_builder.py``。数値・内部 ID を入れない。いずれも生成の
# たびにいまのトピックから付け直す文で、次の生成の「現在の下書き」からは外す
# （``course_content_builder.GENERATED_DRAFT_NOTES``）。

#: トピックに結びついた式に AI が文脈から復元した式が含まれ、部品の注意書きがそれを
#: 述べているのに、生成された注意点に含まれていなかったときに足す1文（IK-0443）。
RECONSTRUCTED_TOPIC_CAUTION = (
    "このトピックの式には、AI が文脈から復元した式が含まれます（原文の数式とは未照合）。"
)

#: コース内の別のトピックとトピックの要約が同じ文のときに ``topic.coverage`` へ残す
#: 事実文（IK-0441。件数は書かない）。
DUPLICATE_TOPIC_SUMMARY_NOTE = (
    "この項目の要約は、コース内の別の項目と同じ文です。題名に沿って書き分けてください。"
)

#: 式の候補として取り込まれたが式として読めないもの（見出し行・軸ラベル・断片等）を
#: 授業用ドラフトの材料から外したときの事実文（IK-0459。件数・ID は書かない）。
EXCLUDED_EQUATION_CANDIDATES_NOTE = (
    "式として取り込まれたが式として読めない候補を、数式の一覧から外しています"
)

#: 外した理由の語 → 日本語（``course_content_builder.JUNK_EQUATION_REASONS`` とキー一致）。
JUNK_EQUATION_REASON_LABELS = MappingProxyType({
    "axis_ticks": "図の目盛り・短い断片",
    "document_header": "論文の見出し行",
    "axis_label": "図の軸ラベル",
    "empty_body": "式番号だけで本体が無いもの",
    "replacement_char": "文字化けを含む断片",
    "no_relation": "式の記号を含まない語句",
    "prose": "本文の文",
    "split_fragment": "1つの式が割れた断片",
})

#: 原文（PDF の文字層）は短いのに、復元された LaTeX がその先まで書かれている式に
#: 添える事実文（IK-0460）。
RECONSTRUCTED_BEYOND_RAW_NOTE = (
    "原文の該当箇所は raw_text の文字だけで、LaTeX は AI が文脈から復元したものです（原文とは未照合）"
)

#: 根拠候補の種類ごとの有無（IK-0461。件数は書かない。``{kind}`` は「主張」「図」等）。
GROUNDING_KIND_SUPPLIED_FACT = "このトピックには{kind}が根拠候補として供給されています。"
GROUNDING_KIND_NOT_SUPPLIED_FACT = "このトピックには{kind}が根拠候補として供給されていません。"
