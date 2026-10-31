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
    "VERSION_AUDIENCE_NOTE_COURSE",
    "VERSION_CHANGED_TOPICS_LABEL",
    "AI_READING_LABEL",
    "ORIGINAL_TEXT_MARK",
    "REWRITTEN_TEXT_MARK",
    "SYNTHESIZED_TEXT_MARK",
    "original_text_mark_for_origin",
    "ANCHOR_LANDING_SCALE",
    "ANCHOR_LANDING_THRESHOLD_MID",
    "ANCHOR_LANDING_THRESHOLD_NEAR",
    "ANCHOR_NEARNESS_SCALE",
    "ANCHOR_NEARNESS_THRESHOLD_MID",
    "ANCHOR_NEARNESS_THRESHOLD_NEAR",
    "CHALLENGE_MODE_LABELS",
    "CITATION_INTENT_LABELS",
    "COMPLEMENT_SKY_THRESHOLD",
    "KEYPHRASE_SOURCE_LABELS",
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
    "EDGE_DIRECTION_UNRECORDED_SUFFIX",
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
    "SIMULATION_AGREEMENT_LABELS",
    "SIMULATION_AGREEMENT_LEVEL_SCALE",
    "SIMULATION_AGREEMENT_THRESHOLDS",
    "SIMULATION_AXIS_LABELS",
    "SIMULATION_SEPARATION_LABELS",
    "SIMULATION_SEPARATION_LEVEL_SCALE",
    "SIMULATION_SEPARATION_THRESHOLDS",
    "simulation_agreement_level",
    "simulation_separation_level",
    "SUPPORT_SECTION_LABELS",
    "TRACE_STATUS_LABELS",
    "TRACE_SUPERSEDED_REASON_LABELS",
    "TRACE_SUPERSEDED_REASONS",
    "CHAT_TURN_WITHDRAWN_FACT",
    "LANDSCAPE_STATUS_LABELS",
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


#: IK-1072（第 25 周 第 9 波）: 関係の向きの句の正本（依存だけが向きを持つ — 骨格の辺の from → to）。
#: 反映の確認・カード（``atlas_review_display.edge_direction_line``）・凍結の影響
#: （``atlas_lifecycle.edge_change_label``）・学生の L2 と右パネル（``routes/atlas_view.py``）が
#: すべてこの句を通す（面ごとに向きの言い方を作らない）。時制や「関係として…」の結びは各面が足す。
EDGE_DIRECTION_PHRASE_DEPENDS = "「{from_label}」が「{to_label}」に依存する"
#: 向きの無い種別（隣接・関連）の句。種別の表示語は :data:`EDGE_KIND_LABELS`。
EDGE_UNDIRECTED_PHRASE = "「{from_label}」と「{to_label}」（{kind_label}）"


#: 向きを持つ種別（骨格の辺の from → to が意味を持つもの）。
EDGE_DIRECTED_KINDS = frozenset({"depends"})

#: IK-1149（第 25 周 第 11 波）: 有向の関係の向きを、採用のときに教員が選んだ記録が無い（migration 096 より前の
#: 採用 — 記載の順は 2 つの項目の内部の並び）ことを、向きの句の直後に添える事実（学習者・教員の両方の面）。
EDGE_DIRECTION_UNRECORDED_SUFFIX = "（向きは、採用のときに選ばれた記録がありません）"


def edge_direction_with_record(phrase: object, direction_unrecorded: object = False) -> str:
    """向きの句に「選ばれた記録がない」事実を添える（IK-1149）。句が空・記録ありならそのまま。"""
    text = str(phrase or "")
    if text and direction_unrecorded:
        return text + EDGE_DIRECTION_UNRECORDED_SUFFIX
    return text


def edge_kind_is_directed(kind: object) -> bool:
    """種別が向きを持つか（依存だけ）。"""
    return str(kind or "") in EDGE_DIRECTED_KINDS


def edge_direction_phrase(kind: object, from_label: object, to_label: object) -> str:
    """依存の向きの句（「A」が「B」に依存する）。依存以外・語彙外は空文字（向きを言わない）。"""
    if not edge_kind_is_directed(kind):
        return ""
    return EDGE_DIRECTION_PHRASE_DEPENDS.format(
        from_label=str(from_label or ""), to_label=str(to_label or "")
    )


def edge_relation_phrase(kind: object, from_label: object, to_label: object) -> str:
    """関係 1 本の句。依存は向きの句、隣接・関連は「「A」と「B」（隣接）」。語彙外は空文字。"""
    kind_text = str(kind or "")
    if kind_text not in EDGE_KIND_LABELS:
        return ""
    directed = edge_direction_phrase(kind_text, from_label, to_label)
    if directed:
        return directed
    return EDGE_UNDIRECTED_PHRASE.format(
        from_label=str(from_label or ""),
        to_label=str(to_label or ""),
        kind_label=EDGE_KIND_LABELS[kind_text],
    )


# ── キーフレーズの供給元（論文ディスカバリー / 論文レーダー, PD3）─────────────────
#
# 語彙の正本は core/paper_discovery/schema.py::KEYPHRASE_SOURCES。表示語彙はここが
# 唯一の定義で、admin-paper-discovery.js / admin-paper-radar.js の SOURCE_LABELS は
# 逐語ミラー（test_paper_radar_ui_static.py が一致を固定）。チップの title 属性に出る。
KEYPHRASE_SOURCE_LABELS = MappingProxyType(
    {
        "skeleton": "分野の地図の概念から",
        "cartridge": "カートリッジ語彙から",
        "component": "承認済み理論部品から",
        "alias": "教員が確定した別名から",
        "concept": "主張の概念から",
        "title": "題名から",
        "thesis": "論文の中心命題・要旨から",
        "manual": "手動",
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


# ── 検証シミュレーション（SB層 比較器, simulation_bridge_design.md §5.3）────────────
# 理論の出力と模擬観測の**相対差**（小さいほど良い）と、効果量 / ばらつきの**分離比**
# （大きいほど良い）を段階化する。閾値は発明値（設計書 §5.3 の宣言。実測での見直し前提）。
# 本層の数値は教員向け計器（制度指標カタログ ``simulation.agreement``）として metrics に
# 残すが、段階キーとラベルの正本はここ。**未測定（None）は段階に倒さず ``unmeasured``**
# にする — 「一致」を測れなかった事実を「乖離」とも「一致」とも見せない（SB1）。
#: 相対差の境界（昇順: これ以下なら match / rough、超えれば diverge）。
SIMULATION_AGREEMENT_THRESHOLDS = (0.05, 0.20)
#: 分離比の境界（降順: これ以上なら clear / border、未満は buried）。
SIMULATION_SEPARATION_THRESHOLDS = (3.0, 1.0)

#: 相対差は「小さいほど上位」なので、符号を反転した値（``-rel_diff``）を渡す降順スケール。
#: 末尾（diverge）が最も慎重な段階（一致を言い過ぎない）。直接呼ばず
#: :func:`simulation_agreement_level` を使う（None → unmeasured の分岐を持つ）。
SIMULATION_AGREEMENT_LEVEL_SCALE = GradedScale(
    tuple(-t for t in SIMULATION_AGREEMENT_THRESHOLDS),
    ("match", "rough", "diverge"),
)
#: 分離比の段階キー。末尾（buried）が最も慎重な段階。
SIMULATION_SEPARATION_LEVEL_SCALE = GradedScale(
    SIMULATION_SEPARATION_THRESHOLDS,
    ("clear", "border", "buried"),
)

SIMULATION_AGREEMENT_LABELS = MappingProxyType({
    "match": "一致",
    "rough": "おおむね一致",
    "diverge": "乖離",
    "unmeasured": "未測定",
})
SIMULATION_SEPARATION_LABELS = MappingProxyType({
    "clear": "分離できる",
    "border": "境界",
    "buried": "埋もれる",
    "unmeasured": "未測定",
})
#: 段階ラベル 3 つの軸名（第 21 周 R5: 「未測定 / 未測定 / 未測定」が何の 3 つか読めなかった）。
#: 表示は ``"{軸名}: {段階}"``（``simulation_bridge.service.run_pipeline`` の ``result.labels``）。
SIMULATION_AXIS_LABELS = MappingProxyType({
    "agreement": "一致（理論の出力と模擬観測）",
    "separation": "分離（効果とばらつき）",
    "reference": "記載値との一致",
})


def _finite_or_none(value: object) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return None
    return numeric


def simulation_agreement_level(relative_difference: object) -> str:
    """相対差 → ``match`` / ``rough`` / ``diverge``。測れない値は ``unmeasured``。"""
    numeric = _finite_or_none(relative_difference)
    if numeric is None or numeric < 0:
        return "unmeasured"
    return SIMULATION_AGREEMENT_LEVEL_SCALE.label_for(-numeric)


def simulation_separation_level(ratio: object) -> str:
    """分離比 → ``clear`` / ``border`` / ``buried``。測れない値は ``unmeasured``。"""
    numeric = _finite_or_none(ratio)
    if numeric is None or numeric < 0:
        return "unmeasured"
    return SIMULATION_SEPARATION_LEVEL_SCALE.label_for(numeric)


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
    # 理由（``payload.superseded_reason``）の無い旧行の既定。書き直しと取り消しを
    # 区別できない行に「書き直し」と断言しない（第 23 波 F2）。
    "superseded": "書き直しか取り消しで差し替え",
})

#: 学習チャットの往復を書き直し・取り消ししたとき、そこから作られた痕跡に残す理由語彙
#: （``interest_traces.payload.superseded_reason``。書き手は
#: ``api/services.py::truncate_chat_and_supersede``）。``superseded`` という1語に
#: 書き直しと取り消しの2つの原因を畳まない（第 23 波 F2）。
TRACE_SUPERSEDED_REASONS = ("rewrite", "delete")

#: 上の理由ごとの「わたしの記録」の状態ラベル（読み手は ``core/trace_ledger.py``）。
TRACE_SUPERSEDED_REASON_LABELS = MappingProxyType({
    "rewrite": "書き直しで差し替え",
    "delete": "取り消し（記録には残しています）",
})

#: 学習画面のコース目次のトピック状態（``LearningTopic.status``）の表示語（第 23 周）。
#: ``locked`` は「順番に学ぶ」の推奨順であって禁止ではない（ロック中のトピックも
#: 開ける — ``_overlay_learner_progress`` の docstring）。「鍵がかかっている」と読ませず
#: 「開ける」ことを言う。読み手は ``api/routes/learning.py::_project_topics_for_learner``
#: （``topics[].status_label``）。JS は ``status_label`` を描く（第 2 波）。
TOPIC_STATUS_LABELS = MappingProxyType({
    "completed": "確認済み",
    "in_progress": "学習中",
    "locked": "まだ順番が来ていません（開けます）",
})

#: 「問いの軌跡」（``GET /api/learning/courses/{id}/interest-traces`` →
#: ``core/learning_experience.py::build_traces_view`` の ``kind_label``）で各行に付ける
#: 種別の表示語。第 23 周（st-06「質問なのに誤答と言われるのは引っかかる」）:
#: ``misconception`` の行の本文は**学習者の質問そのもの**で、AI の回答に訂正の言い回しが
#: あったことを示すだけなので、「誤答」という評価語を付けない（vision §3.2 採点しない）。
#: 事実語「訂正あり」にする。JS 側（app.js ``TRACE_KIND_LABEL``）はサーバの ``kind_label``
#: を描くよう移す（第 2 波）。
TRACE_TRAJECTORY_KIND_LABELS = MappingProxyType({
    "question": "問い",
    "detour": "寄り道",
    "misconception": "訂正あり",
    "raw": "記録",
    "tension": "違和感",
    "backstage_question": "楽屋の質問",
})

#: 「再訪のころ合い」の文中で行を指す語。``misconception`` の行も中身は問いなので
#: 「問い」と呼ぶ（「前に引っかかった誤答」と言わない — 第 23 周）。
TRACE_TRAJECTORY_KIND_WORDS = MappingProxyType({
    "question": "問い",
    "detour": "寄り道",
    "misconception": "問い",
    "backstage_question": "楽屋の質問",
})

#: 🗑（このメッセージ以降を取り消す）の直後に会話欄へ出す事実文。往復は会話の履歴から
#: 外れるが、そこから作られた問い・引っかかりの記録は行を消さず状態を変えて残る（P4）。
CHAT_TURN_WITHDRAWN_FACT = (
    "この往復を会話から取り消しました。ここから作られた問い・引っかかりの記録は、"
    "「わたしの記録」に取り消し済みとして残ります。"
)

#: 反実仮想セッションの共有範囲（``counterfactual_sessions.shared_scope`` = 既存の
#: Visibility 語彙 private / group / public）の日本語。IK-0854: 画面に内部値 ``private`` を
#: 出さない。文言はコース管理の公開範囲の選択肢（``admin.js``）と同じ語を使う。
SHARED_SCOPE_LABELS = MappingProxyType({
    "private": "非公開（自分のみ）",
    "group": "グループ限定",
    "public": "公開（全体）",
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

#: 第 25 周 第 7 波（IK-0571 の studio 側）: 解析が原文の言語（英語など）のまま残した名前・
#: 主張・例文に添える印。翻訳はしない（LLM 0 回）— 原文の引用であることを言うだけ。
#: 判定は ``text_excerpt.is_untranslated_text``。JS は素通しで描く（日本語をミラーしない）。
ORIGINAL_TEXT_MARK = "（原文）"
#: IK-1155: 原子化（atomic rewrite）で解析が言い換えた主張 — 原文の言語だが論文の逐語ではない。
REWRITTEN_TEXT_MARK = "（原文の言い換え）"
#: IK-1155: 式から決定論で組み立てた主張（論文の文を持たない — schema.NON_SOURCE_TEXT_CLAIM_ORIGINS）。
SYNTHESIZED_TEXT_MARK = "（式から組み立てた文）"


def original_text_mark_for_origin(origin: str | None) -> str:
    """IK-1155: 原文の言語のまま出す主張の印を、主張の出所（theory_claims.origin）で選ぶ。

    逐語（span / claim_object / 出所不明）は「（原文）」、原子化の言い換えは「（原文の言い換え）」、
    式から組み立てた文は「（式から組み立てた文）」。言語の判定（is_untranslated_text）は呼び出し側。
    """
    key = str(origin or "").strip()
    if key == "atomic_rewrite":
        return REWRITTEN_TEXT_MARK
    if key == "equation_synthesis":
        return SYNTHESIZED_TEXT_MARK
    return ORIGINAL_TEXT_MARK

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

# 第 25 周 第 3 波（IK-0947）: トピックに本文も論文の区画も無く、教材が空のとき。空白のまま
# 配らず空だと言う。講義の案内（core/lecture.py::lecture_availability_note）も同じ文を使い、
# 空の「読む」へ誘導しない。生成中なら準備中の文。
TOPIC_MATERIAL_EMPTY_NOTICE = "このトピックの教材はまだ用意されていません（担当の先生が準備します）。"
TOPIC_MATERIAL_EMPTY_PREPARING_NOTICE = "このトピックの教材は準備中です。"
# 教員がコースを公開（public / group）したとき、本文の無いトピックが残っていれば応答に添える
# 事実文（IK-0947。件数は書かない）。
COURSE_PUBLISHED_WITH_EMPTY_TOPICS_NOTICE = (
    "本文がまだ無いトピックがあります。学生がそのトピックを開くと、"
    "教材はまだ用意されていないと表示されます。"
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
# 既に受講中のコースに再度登録したとき（新しい受講記録は作られていない）。
COURSE_ALREADY_ENROLLED_NOTICE = "このコースはすでに受講中です。これまでの学習の続きから読めます。"
# 第 25 周 第 3 波（IK-0945）: 受講中のコースが非公開になったとき。受講者はこれまでどおり読める
# （新規の受講開始だけが止まる）。コース一覧の受講中の行と受講開始の 403 で同じ文を使う。
COURSE_ENROLLMENT_CLOSED_NOTICE = "このコースは新規の受講を受け付けていません。"

# 第 25 周 第 3 波（IK-0949）: 教員が教材図スタジオで作り採用した説明図の印（論文由来の根拠と
# 同じ「根拠候補」に並べない。根拠の状態にも数えない）。
TEACHING_FIGURE_ORIGIN_LABEL = "教員が作った説明図"

# 第 25 周 第 3 波（IK-0951）: コース・章・トピックの題名に生の TeX 記法が残っているとき、教員に
# 知らせる事実文（題名は学生の画面で素の文字列として出る）。該当の題名は別に列挙する。
COURSE_TITLE_RAW_TEX_NOTICE = (
    "題名に数式の記法が残っています。学生の画面では記号のまま表示されるので、"
    "言葉で言い換えてください。"
)
COURSE_ENROLLED_BUT_CLOSED_NOTICE = (
    "このコースは新規の受講を受け付けていません。受講中のあなたはこれまでどおり読めます。"
)
# 入場できないコース（存在しない・閲覧できない）を学習画面で言うとき（英語の 404 を出さない）。
COURSE_NOT_AVAILABLE_NOTICE = "このコースは見つからないか、閲覧できません。"


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

#: 前提確認の逆質問の英語版（IK-0574）。学習者の当該発話が英語（``_is_mostly_latin`` か
#: ``_is_kana_kanji_free``）の往復では、日本語の逆質問（``services.check_prerequisites`` が正本）
#: の代わりにこちらを出す。履歴の判定器は下の 2 つの目印と ``"{topic}"`` の引用で
#: 英語の逆質問を読む（日本語の ``「{topic}」`` / ``PREREQUISITE_GATE_MARKER`` と対）。
#: 目印の文字列はこの本文の部分文字列でなければならない（test_ik0574_prerequisite_gate_language.py）。
PREREQUISITE_GATE_MARKER_EN = "you first need the following prerequisites"
#: 英語の逆質問が「最初に説明する前提」を挙げる言い方（``まず「X」から説明します`` と対）。
PREREQUISITE_GATE_FIRST_EN = 'I will start by explaining "{first}"'
PREREQUISITE_GATE_FULL_EN = (
    'To understand "{topic}", ' + PREREQUISITE_GATE_MARKER_EN + ":\n\n"
    "**{prerequisites}**\n\n"
    'Do you already understand these? If you do, we will continue with "{topic}" on that basis. '
    "If not, " + PREREQUISITE_GATE_FIRST_EN + ".\n\n"
    + PREREQUISITE_GATE_EN.replace("{prerequisite}", "{first}")
)
#: 逆質問を回答の後ろに添えた往復の目印の英語版（``PREREQUISITE_GATE_ANSWERED_MARKER``
#: 「ご質問には上で答えました。」と対。保存文の形 = 本文 → 区切り → 目印 → 逆質問 は不変）。
PREREQUISITE_GATE_ANSWERED_MARKER_EN = "I answered your question above. "
#: 逆質問の選択肢ボタンの英語版（``LearningSupportAgent.prerequisite_choice_actions``）。
#: ``message`` は英語の確認判定（services の肯定 / route の否定・説明要求）が受理する形。
PREREQUISITE_CHOICE_YES_EN = "Yes, I understand"
PREREQUISITE_CHOICE_NO_LABEL_EN = 'No, explain "{prerequisite}" first'
PREREQUISITE_CHOICE_NO_MESSAGE_EN = "No, please explain {prerequisite} first"

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
# 知識ランドスケープの配置状態（教員向けの状態名）
# ---------------------------------------------------------------------------
#
# 語彙（enum）の正本は ``core/landscape/schema.py`` の ``STATUSES``、教員向けの状態名の
# 正本はここ（``landscape/schema.py::STATUS_LABELS`` はこの表の別名）。学習者向けの
# 出所ラベル（「確認待ち（AI推定）」等）は ``landscape/schema.py::PROVENANCE_LABELS`` で、
# 宛先が違う別の表。教材管理の位置づけモーダル（``admin.js``）とリリース前の確認
# （``admin-release-review.js``）の状態名フォールバックはこの表の**逐語ミラー**
# （``test_landscape_status_vocab_mirror.py``）。行のボタン「再検討」を押した結果は
# 「再検討中」で、押したボタンと同じ語で読める（第 23 波 F5）。
LANDSCAPE_STATUS_LABELS = MappingProxyType({
    "inferred": "AI推定（未確認）",
    "confirmed": "教員確認済み",
    "rejected": "却下",
    "review_required": "再検討中",
    "superseded": "再解析により置換",
})


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
#: 提示された配置と確認した配置が一致しないときの事実文（第 23 周 F5）。
#: 「表示されていた N 件のうち M 件を確認した」を数字で言う（RR6 が隠すのは weight /
#: confidence であって、提示・適用の件数という事実ではない — DC2）。読み手は
#: ``api/routes/landscape.py::accept_course_landscape_placements`` の ``decision_note``。
RELEASE_DECISION_PARTIAL_NOTE = "表示された{presented}件のうち確認したのは{applied}件です。"
#: 上の事実文に、編集権限の無い教材を対象外にした分を添える補足（教材の本数）。
RELEASE_DECISION_NOT_EDITABLE_SUFFIX = "（編集権限の無い教材{documents}本の位置づけは対象外）"


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
    # IK-0888: 層を名乗る（検証の台帳の話であって、論文の配置の有無ではない）。
    # 論文が置かれている場所でも、この文が「論文が無い」と読めないようにする。
    "unrecorded": (
        "この場所について、検証の記録（台帳）はまだありません。"
        "論文がこの場所に置かれているかどうかは「論文の位置」で示します。"
    ),
    "fog": "この領域には、まだコーパスの論文が関係づけられていません。",
    # IK-0964: 札が個人の訪問状態を名乗るときは、説明も訪問の軸で言う（台帳の状態は
    # 検証行だけが言う — 2 つの軸を 1 枚の札と注記に重ねない）。
    "unvisited": (
        # IK-0964 残り（第 5 波）: 訪問は学習の痕跡で数え、地図で選ぶことは数えない。
        "あなたはまだこの場所から学んでいません（地図で選ぶだけでは数えず、"
        "［ここから学ぶ］などで学習を始めると変わります）。"
        "確かめられているかどうかは「検証」の行で示します。"
    ),
    "now": (
        "あなたがいま学習している場所です。"
        "確かめられているかどうかは「検証」の行で示します。"
    ),
})

#: 論文の配置の根拠の出所の印（IK-0677。キーは ``core/landscape/schema.py`` の
#: ``EVIDENCE_SOURCE_*``）。verbatim 検査（LS4）を通った引用でも、照合先には解析が
#: 論文全体を再構成した文が含まれるので、原文の逐語と同じ顔で出さない。
LANDSCAPE_EVIDENCE_SOURCE_LABELS = MappingProxyType({
    "claim": "論文の主張からの引用",
    "paper_summary": "解析が論文全体を再構成した文からの引用（原文の逐語ではありません）",
    # 出所の記録が無い旧行で claim_id も無いもの（推測で「再構成」と断定しない）。
    "unrecorded": "論文全体の記述からの引用（原文の逐語とは限りません）",
})
#: 配置の理由文の印（IK-0967。理由は常に AI の要約）。
LANDSCAPE_REASON_SOURCE_LABEL = "AIの要約"
#: 理由が引用に現れない固有の語を含むときの一文（語の列挙・件数なし）。
LANDSCAPE_REASON_BEYOND_QUOTE_TEMPLATE = "理由に挙がる {terms} は、引用には現れません。"

#: 分野（atlas ドメイン）の表示名が登録されていないときの学習者向けの呼び名。
#: domain_key（内部キー）を表示名の代わりに出さない。
ATLAS_DOMAIN_UNNAMED_LABEL = "名前が登録されていない分野"

#: 分野の地図の版の呼び名（IK-0942 — 「公開版（版 X）」「骨格 版X」「骨格版 X」「版 X」と
#: 画面ごとに違っていた）。教員・学習者とも「地図 版 X」の 1 形式。JS は逐語ミラー
#: （admin.js / landscape-layer.js / atlas-threads-layer.js / app.js の ``ATLAS_VERSION_PREFIX``。
#: 一致は ``test_atlas_review_round25_r3.py`` が固定）。
ATLAS_VERSION_PREFIX = "地図 版 "

#: 学習者の地図の「コース」層（L2）で、上限（§13）のために骨格の項目の一部を描かなかった
#: ときの事実文（IK-0989・第 25 周 第 5 波）。件数・上限値は言わない。
ATLAS_L2_OMITTED_NOTE = (
    "この画面では、分野の地図の項目の一部だけを描いています（いま学んでいる場所と、"
    "そのつながりを優先しています）。描いていない項目は「分野」の層で見られます。"
    "関係の実線は、ここに描いている項目どうしの間にだけ引いています。"
)
#: 学習者の地図の右パネル「承認」の行で、承認の記録が無い「暗黙の前提」の概念に出す事実文
#: （IK-1018・第 25 周 第 6 波）。承認の行は承認（C層）だけを言い、検証の範囲（D層の欄）は
#: 検証の行に任せる。閉世界（SL1）: 「このコーパスの中では」を付け、分野全体の不在は言わない。
ATLAS_ENDORSE_NONE_ASSUMED_LINE = "承認: このコーパスの中では、教員の承認の記録がありません"
#: IK-1056（第 25 周 第 8 波）: 承認の記録が無いことは状態に依らず 1 つの文で言う（暗黙の前提だけ
#: 閉世界の文・ほかは「承認記録なし」と言い分けていた）。正本はこの 1 定数（上の名前は互換の別名）。
ATLAS_ENDORSE_NONE_LINE = ATLAS_ENDORSE_NONE_ASSUMED_LINE
#: 置き換える前の短い文（状態キャッシュに残る行を読み時に置き換えるためだけに持つ）。
ATLAS_ENDORSE_LEGACY_NONE_LINE = "承認: 承認記録なし"
#: 置き換える前の固定文（地図の状態キャッシュに残っている行を読み時に置き換えるためだけに持つ）。
ATLAS_ENDORSE_LEGACY_SCOPE_LINE = "承認: 承認記録なし — 台帳のスコープ欄は空欄"
#: 学習者の地図のパネルの文に出してはいけない内部の欄名（IK-1018 のガードレールが検査する）。
ATLAS_LEARNER_PANEL_DENYLIST = ("スコープ欄", "verification_scopes", "source_scope")

#: IK-1018 残り（第 25 周 第 7 波）: 学習者の地図の札・検証の行・札の説明に初出で出ていた
#: 内部の語（「台帳」「記帳」）を学習者の語に読み替える表。上から順に置換する（長い句を先に）。
#: 状態キャッシュの行（教員側の画面・ガードレールが読む正本の文）は書き換えず、学習者向けの
#: ルート（``routes/atlas_view.py``）が読み時に通す。意味は変えない（「記録が無い」は無いまま）。
ATLAS_LEARNER_WORDING = (
    ("台帳に根拠の記帳がありません", "根拠の記録がありません"),
    ("台帳に記帳がありません", "検証の記録がありません"),
    ("台帳に記帳なし", "検証の記録なし"),
    ("原文の引用は台帳に記録されていません", "原文の引用の記録がありません"),
    ("検証の記録（台帳）", "検証の記録"),
    ("記録は台帳にありません", "記録はありません"),
    ("記帳された直接検証なし", "直接確かめた記録なし"),
    ("台帳の記帳情報がありません", "検証の記録がありません"),
    ("台帳", "検証の記録"),
    ("記帳", "記録"),
)
#: 読み替えの後に学習者の地図の文へ残ってはいけない語（ガードレールが検査する）。
ATLAS_LEARNER_WORDING_DENYLIST = ("台帳", "記帳")


def learner_atlas_wording(text: object) -> str:
    """学習者の地図の文から「台帳」「記帳」を学習者の語へ読み替える（IK-1018 残り）。決定論。"""
    out = str(text or "")
    for old, new in ATLAS_LEARNER_WORDING:
        if old in out:
            out = out.replace(old, new)
    return out

#: L2 の代表選出で実際に使った手がかりの語（IK-1017・第 25 周 第 6 波）。
#: ``atlas_l2_omitted_note`` が並べて事実文にする。数値・版の番号は言わない。
#: IK-1055（第 25 周 第 8 波）: 「いま学んでいる場所」は学習者本人の状態（焦点のトピック・いまここ・足跡）が
#: 効いたときだけ言う。教員がコースのトピックに結んだ地図の場所は別の語で言う（足跡の無い学生に
#: 「いま学んでいる場所」と言わない）。
ATLAS_L2_HINT_LEARNING = "いま学んでいる場所"
ATLAS_L2_HINT_COURSE_TOPICS = "このコースのトピックが結ばれている場所"
ATLAS_L2_HINT_PLACEMENTS = "このコースの論文が置かれている場所"
#: IK-1045（第 25 周 第 7 波）: 直近の凍結だけでなく、過去の改訂で加わった関係も手がかりに含めるので
#: 「新しい版で」ではなく「版の改訂で」と言う（選出と一致させる）。
#: IK-1119（第 25 周 第 10 波）: 集合は最初の版以降の全改訂。「これまでの」で範囲を言い、直近の版の差と
#: 読ませない（どの版で加わったかは右パネルの関係の行だけが版の事実として言う）。
ATLAS_L2_HINT_ADDED_RELATIONS = "これまでの版の改訂で加わった関係"
ATLAS_L2_HINTS = (
    ATLAS_L2_HINT_LEARNING,
    ATLAS_L2_HINT_COURSE_TOPICS,
    ATLAS_L2_HINT_PLACEMENTS,
    ATLAS_L2_HINT_ADDED_RELATIONS,
)
#: 手がかりが 1 つも無かったとき（足跡が無く、コースの論文の配置もトピックの対応も無い）。
ATLAS_L2_NO_HINT_CLAUSE = (
    "このコースには地図の上の手がかりがまだ無いため、地図の並び順で先頭の項目を描いています"
)


#: IK-1045（第 25 周 第 8 波）: 版の改訂で加わった（教員が確定した）関係のうち、端点が上限のために
#: この画面に描かれなかったものを名前で言う文の前置き。件数は言わない。名前を挙げきれないときは
#: :data:`ATLAS_L2_UNDRAWN_RELATIONS_MORE` を足す（「ほか」— 数は言わない）。
ATLAS_L2_UNDRAWN_RELATIONS_PREFIX = "これまでの版の改訂で加わった関係のうち、ここには描いていないもの: "
ATLAS_L2_UNDRAWN_RELATIONS_MORE = "ほか"
#: 名前で挙げる関係の上限（文の長さのため。数値は文に出さない）。
ATLAS_L2_UNDRAWN_RELATIONS_MAX_NAMED = 6


def atlas_l2_undrawn_relations_line(pairs) -> str:
    """描かなかった確定済みの関係を名前で言う一文（IK-1045 / IK-1118）。

    ``pairs`` の各要素は (kind, from_label, to_label)。関係は :func:`edge_relation_phrase` の句
    （依存は「「A」が「B」に依存する」・隣接/関連は「「A」と「B」（隣接）」— 向きを落とさない）。
    種別の無い (ラベル, ラベル) は「A — B」（種別が分からない事実のまま）。空なら空文字。件数は言わない。
    """
    named: list[str] = []
    for pair in pairs or ():
        try:
            unrecorded = False
            if len(pair) == 4:
                kind, a, b, unrecorded = pair
            elif len(pair) == 3:
                kind, a, b = pair
            else:
                kind = ""
                a, b = pair
        except (TypeError, ValueError):
            continue
        a, b = str(a or "").strip(), str(b or "").strip()
        if not a or not b:
            continue
        text = edge_relation_phrase(kind, a, b) if kind else ""
        if text and edge_kind_is_directed(kind):
            text = edge_direction_with_record(text, unrecorded)
        if not text:
            text = f"{a} — {b}"
        if text not in named:
            named.append(text)
    if not named:
        return ""
    shown = named[:ATLAS_L2_UNDRAWN_RELATIONS_MAX_NAMED]
    body = "、".join(shown)
    if len(named) > len(shown):
        body += f"、{ATLAS_L2_UNDRAWN_RELATIONS_MORE}"
    return f"{ATLAS_L2_UNDRAWN_RELATIONS_PREFIX}{body}。"


def atlas_l2_omitted_note(hints, undrawn_relations=None) -> str:
    """L2 で骨格の項目の一部だけを描いたときの事実文（IK-0989 / IK-1017 / IK-1045）。

    ``hints`` は :data:`ATLAS_L2_HINTS` の語の列（実際に描いた項目に効いた手がかりだけ）。
    空なら「手がかりが無いので並び順」と言い、優先が働いたかのようには言わない。
    ``undrawn_relations`` は描かなかった確定済みの関係の (ラベル, ラベル) の列で、あれば
    名前で言う一文を末尾に足す（件数なし）。
    """
    used = [h for h in ATLAS_L2_HINTS if h in set(hints or [])]
    if not used:
        clause = ATLAS_L2_NO_HINT_CLAUSE
    else:
        clause = "と、".join(used) + "と、そのつながりを優先しています"
    note = (
        f"この画面では、分野の地図の項目の一部だけを描いています（{clause}）。"
        "描いていない項目は「分野」の層で見られます。"
        "関係の実線は、ここに描いている項目どうしの間にだけ引いています。"
    )
    undrawn = atlas_l2_undrawn_relations_line(undrawn_relations)
    return note + undrawn if undrawn else note


#: IK-1072（第 25 周 第 9 波）: L2 に描いた有向の関係（依存）の向きを言う一文の前置き。
#: 実線の矢じりと同じ向き（依存する側 → 依存される側）。件数は言わない。
ATLAS_L2_DIRECTED_RELATIONS_PREFIX = "矢印のある線は向きのある関係です（矢印は依存する側から依存される側へ）: "
ATLAS_L2_DIRECTED_RELATIONS_MAX_NAMED = 6


def atlas_l2_directed_relations_line(triples) -> str:
    """L2 に描いた依存の関係を向きの句で並べる一文。``triples`` は (kind, from_label, to_label) の列。

    依存（向きのある種別）が無ければ空文字。挙げきれないときは「ほか」（数は言わない）。
    """
    named: list[str] = []
    for item in triples or ():
        try:
            if len(item) == 4:
                kind, a, b, unrecorded = item
            else:
                kind, a, b = item
                unrecorded = False
        except (TypeError, ValueError):
            continue
        phrase = edge_direction_with_record(edge_direction_phrase(kind, a, b), unrecorded)
        if phrase and phrase not in named:
            named.append(phrase)
    if not named:
        return ""
    shown = named[:ATLAS_L2_DIRECTED_RELATIONS_MAX_NAMED]
    body = "、".join(shown)
    if len(named) > len(shown):
        body += f"、{ATLAS_L2_UNDRAWN_RELATIONS_MORE}"
    return f"{ATLAS_L2_DIRECTED_RELATIONS_PREFIX}{body}。"


#: 右パネル（L1/L2 で選んだ概念）に添える、その概念に結ばれた確定済みの関係の行（名前の列挙・件数なし）。
ATLAS_PANEL_RELATIONS_PREFIX = "地図の関係: "
ATLAS_PANEL_RELATIONS_MAX_NAMED = 6


#: IK-1119（第 25 周 第 10 波・オーナー判断＝推奨どおり）: 右パネルの関係の行にだけ、その関係が加わった版を
#: 事実として添える（地図の上に印・NEW バッジ・告知は作らない — category_gap_candidates_design §5.6）。
#: 最初の版から在る関係には添えない。
ATLAS_PANEL_RELATION_ADDED_IN = "（版 {version} で加わった）"


def atlas_panel_relations_line(triples) -> str:
    """選んだ概念の確定済みの関係を句で並べる一文（``edge_relation_phrase`` を通す）。無ければ空文字。

    各要素は (kind, from_label, to_label) か (kind, from_label, to_label, added_version)、
    または末尾に ``direction_unrecorded``（IK-1149: 有向の関係の向きが採用のときに選ばれていない）を足した 5 要素。
    ``added_version`` があればその句に :data:`ATLAS_PANEL_RELATION_ADDED_IN` を添える（IK-1119）。
    """
    named: list[str] = []
    for item in triples or ():
        try:
            unrecorded = False
            if len(item) == 5:
                kind, a, b, added_version, unrecorded = item
            elif len(item) == 4:
                kind, a, b, added_version = item
            else:
                kind, a, b = item
                added_version = ""
        except (TypeError, ValueError):
            continue
        phrase = edge_relation_phrase(kind, a, b)
        if phrase and edge_kind_is_directed(kind):
            phrase = edge_direction_with_record(phrase, unrecorded)
        version_text = str(added_version or "").strip()
        if phrase and version_text:
            phrase += ATLAS_PANEL_RELATION_ADDED_IN.format(version=version_text)
        if phrase and phrase not in named:
            named.append(phrase)
    if not named:
        return ""
    shown = named[:ATLAS_PANEL_RELATIONS_MAX_NAMED]
    body = "、".join(shown)
    if len(named) > len(shown):
        body += f"、{ATLAS_L2_UNDRAWN_RELATIONS_MORE}"
    return f"{ATLAS_PANEL_RELATIONS_PREFIX}{body}。"


#: 右パネル（選んだ概念）に添える、その場所に置かれたこのコースの論文の名前の行（第 25 周 第 10 波）。
#: 「論文の位置」レイヤーと往復しなくても読めるよう名前だけを並べる（件数なし）。配置ごとに出所ラベル
#: （教員確認済み / AIによる推定（未確認））を必ず添える（LS8 / AC-005 — 推定を確定に見せない）。
ATLAS_PANEL_PAPERS_PREFIX = "ここに置かれているこのコースの論文: "
ATLAS_PANEL_PAPERS_MAX_NAMED = 4


def atlas_panel_papers_line(items) -> str:
    """``items`` は (論文の呼び方, 出所ラベル) の列。無ければ空文字。挙げきれないと「ほか」（数は言わない）。"""
    named: list[str] = []
    for item in items or ():
        try:
            title, provenance = item
        except (TypeError, ValueError):
            continue
        title = str(title or "").strip()
        provenance = str(provenance or "").strip()
        if not title:
            continue
        text = f"『{title}』" + (f"（{provenance}）" if provenance else "")
        if text not in named:
            named.append(text)
    if not named:
        return ""
    shown = named[:ATLAS_PANEL_PAPERS_MAX_NAMED]
    body = "、".join(shown)
    if len(named) > len(shown):
        body += f"、{ATLAS_L2_UNDRAWN_RELATIONS_MORE}"
    return f"{ATLAS_PANEL_PAPERS_PREFIX}{body}。"


#: IK-1073（第 25 周 第 9 波）: 地図の場所に結びついた学習の記録（足跡）が無く「いまここ」を付けられない
#: ときの事実文。いまここを目次の「学習中」から推定して付けない（学習の行為の無い位置を現在地と言わない）。
ATLAS_NOW_ABSENT_NOTE = (
    "まだ地図の場所に結びついた学習の記録がないため、「いまここ」は表示していません"
    "（コースの目次の「学習中」は、学ぶ順番や開いたトピックから付く印で、地図の「いまここ」とは別に付きます）。"
)


#: 推定の糸の一部の端点が L2 に描かれていないときの事実文（IK-0989）。本数は言わない。
ATLAS_THREADS_UNDRAWN_NOTE = (
    "糸の一部は、この画面に描いていない項目につながるため線にしていません。"
    "下の「糸ごとの根拠」には、すべての糸を挙げています。"
)


def atlas_version_label(version: object) -> str:
    """版の表示語（「地図 版 2026.1」）。版が空なら空文字（版を捏造しない）。"""
    text = str(version or "").strip()
    return f"{ATLAS_VERSION_PREFIX}{text}" if text else ""


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
    # 検証シミュレーション層 v1.1（SB15）: 動かして確かめた試行。「未解決」と読ませない。
    "verification_attempt": MappingProxyType({"open": "動かして確かめた"}),
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

#: 教材の解析成果のグループ共有の権限語（IK-0866。画面に viewer / editor を出さない）。
DOCUMENT_SHARE_PERMISSION_LABELS = {
    "viewer": "閲覧・引用できる",
    "editor": "編集・再解析できる",
}

#: コース管理の役割の印（IK-0911。画面に owner / editor / viewer を出さない。editor 共有でも
#: 原稿スタジオのトピック保存は所有者だけ — IK-0908 — なので印がそれを言う）。
COURSE_ROLE_LABELS = MappingProxyType({
    "owner": "所有",
    "editor": "共有（編集可・トピックの保存は所有者）",
    "viewer": "共有（閲覧のみ）",
})

#: 教材一覧の役割の印（IK-0974。コース一覧の COURSE_ROLE_LABELS と同じ語で、教材の
#: 編集権限 — 記帳・再解析・説明の追加 — の有無を操作の前に言う）。
MATERIAL_ROLE_LABELS = MappingProxyType({
    "owner": "所有",
    "editor": "共有（編集可）",
    "viewer": "閲覧のみ",
})

#: 閲覧のみの教材に添える、編集の共有を頼む先（IK-0974。教員向けの一覧だけで使う）。
MATERIAL_EDIT_REQUEST_TEMPLATE = "編集（台帳の記帳・再解析など）の共有は、所有者の{owner}さんに頼めます。"
MATERIAL_EDIT_REQUEST_UNKNOWN = "編集（台帳の記帳・再解析など）の共有は、この教材の所有者に頼めます。"

#: コースのグループ共有の権限語（IK-0911。共有モーダルの選択肢と現在の設定の表で同じ語）。
COURSE_SHARE_PERMISSION_LABELS = MappingProxyType({
    "viewer": "受講・閲覧できる",
    "editor": "編集できる（トピックの保存は所有者）",
})

#: 検証スコープの 4 軸の画面の語（IK-0905。台帳の記帳フォームの欄見出しと同じ語。
#: 入力検査の事実文に API の欄名 condition / domain / precision / system を出さない）。
LEDGER_SCOPE_AXIS_LABELS = MappingProxyType({
    "condition": "成立条件",
    "domain": "領域",
    "precision": "精度",
    "system": "系",
})

#: C層の説明カードの 2 軸を別の語で言う（IK-0924）。``component_explanations.review_status``
#: （migration 021 の CHECK 語彙）は「学生への公開」の軸、支持（endorsement）の厚みは
#: 「教員の支持」の軸。JS（``admin-lecture-studio.js``）は逐語ミラー
#: （``test_share_round25_explanations.py`` が一致を固定）。
EXPLANATION_AXIS_LABELS = MappingProxyType({
    "review": "学生への公開",
    "support": "教員の支持",
    "citation": "引用",
})

EXPLANATION_REVIEW_STATUS_LABELS = MappingProxyType({
    "teacher_approved": "公開可（査読済み）",
    "teacher_review_required": "査読待ち",
    "needs_revision": "要修正（公開しない）",
    "rejected": "却下（公開しない）",
})

#: 説明カードのボタンの語（IK-0924。査読と支持を同じ「承認」の語で並べない）。
EXPLANATION_ACTION_LABELS = MappingProxyType({
    "approve": "学生に公開してよいと査読する",
    "endorse": "支持する",
    "revoke": "支持を取り消す",
    # IK-0984: 承認済みの標準の説明に、別の教員がもう一人の査読者として査読する。
    "second_review": "もう一人の査読者として査読する",
})

#: 支持の段階（``component_endorsements.level`` の CHECK 語彙）の語（IK-0924）。
EXPLANATION_SUPPORT_LEVEL_LABELS = MappingProxyType({
    "provisional": "暫定の支持",
    "endorsed": "支持",
    "strong": "強い支持",
})

#: 教員の支持の段階ラベル（IK-0924 / IK-0926）。人数は**著者を除き・取り消しを除いた**
#: 支持の数（``core/explanation_governance.COUNTED_ENDORSEMENT_SUMMARY_SQL``）。
#: 定義は制度指標カタログ ``explanation-support-label`` に宣言する（改訂原則 4）。
EXPLANATION_SUPPORT_LABEL_NONE = "まだ教員の支持はありません"
EXPLANATION_SUPPORT_LABEL_PROVISIONAL = "暫定的に{count}名の教員が支持"
EXPLANATION_SUPPORT_LABEL_COUNT = "{count}名の教員が支持"
EXPLANATION_SUPPORT_LABEL_STRONG = "強い支持: {label}"
EXPLANATION_SUPPORT_LABEL_BREADTH = "{label}(専門{breadth}分野)"


def explanation_support_label(summary: Mapping[str, object]) -> str:
    """支持の厚みを段階ラベルにする（数値スコアは出さない。人数の数え方は上の注記）。"""
    def _int(key: str) -> int:
        try:
            return int(summary.get(key) or 0)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    count = _int("endorser_count")
    if count <= 0:
        return EXPLANATION_SUPPORT_LABEL_NONE
    if count == _int("provisional_count"):
        return EXPLANATION_SUPPORT_LABEL_PROVISIONAL.format(count=count)
    label = EXPLANATION_SUPPORT_LABEL_COUNT.format(count=count)
    if _int("strong_count") > 0:
        label = EXPLANATION_SUPPORT_LABEL_STRONG.format(label=label)
    breadth = _int("expertise_breadth")
    if breadth >= 2:
        label = EXPLANATION_SUPPORT_LABEL_BREADTH.format(label=label, breadth=breadth)
    return label


# ── コース管理の一覧: 地図のトピック対応の段階（IK-0929・比を出さない）─────────────
ATLAS_BINDING_COVERAGE_LABELS: dict[str, str] = {
    "all": "すべてのトピックに対応あり",
    "some": "一部のトピックに対応あり",
    "none": "トピックの対応なし",
}


def atlas_binding_coverage_label(bound: int, total: int) -> str:
    """トピックの地図対応を段階の語にする（件数・比は返さない）。"""
    if total <= 0 or bound <= 0:
        return ATLAS_BINDING_COVERAGE_LABELS["none"]
    if bound >= total:
        return ATLAS_BINDING_COVERAGE_LABELS["all"]
    return ATLAS_BINDING_COVERAGE_LABELS["some"]


# ── 学生の「共通部品として」: 標準化の評価の 1 文（IK-0959）─────────────────────────
#: 教員が W層の標準化判定を確定した値だけを学生向けの 1 文にする。``unknown``（未評価）は
#: 教員の内部手続の状態なので学生には何も出さない（キー自体を作らない）。語の正本は
#: ``core/library/schema.py::STANDARDIZATION_STATUS_LABELS``（教員向けの短い語）で、
#: ここは学生向けの文。JS にミラーしない（サーバが解決した文を素通しで描く）。
LEARNER_STANDARDIZATION_NOTES: dict[str, str] = {
    "standard": "教員は、この共通部品を広く標準的に使われるものと判断しています。",
    "field_standard": "教員は、この共通部品をこの分野で標準的に使われるものと判断しています。",
    "emerging_common": "教員は、この共通部品を複数の論文で共通に使われ始めているものと判断しています。",
    "novel": "教員は、この共通部品をこの論文に新しく現れたものと判断しています。",
}


def learner_standardization_note(status: object) -> str:
    """標準化の評価を学生向けの 1 文にする（未評価・語彙外は空文字 = 出さない）。"""
    return LEARNER_STANDARDIZATION_NOTES.get(str(status or ""), "")


# ── 前提の地図・台帳の欄の出所（IK-1002 / IK-1003 / IK-0998・第 25 周 面 B 第 5 波）──────
#: 台帳の 1 画面・未検証合意リストの 1 行に並ぶ欄は、それぞれ別の記録から読む（人の記帳の
#: 検証状態・検証スコープ・観測からの支持線・理論操作グラフの下流・解析時に数えた参照）。
#: 欄名でその出所を言い分け、同じ語（「支持」「依存」）が別の量を指して矛盾に読めないようにする。
#: JS にミラーしない（サーバが ``field_labels`` として渡し、画面はそのまま描く）。
LEDGER_FIELD_LABELS = MappingProxyType({
    "verification": "検証状態（教員の記帳）",
    "support_line": "観測からの支持線",
    "load": "依存の広がり（理論操作グラフの下流・段階）",
    "behavioral": "行動上の合意（解析時に数えた参照）",
    # IK-1120（第 25 周 第 10 波）: 合意リストの「到達可能性」は覆る条件の量で、検証提案の到達可能性
    # （台帳の提案カード）とは別の量。同じ語のまま並べない。
    "falsification_reachability": "覆る条件の到達可能性",
    "proposal_reachability": "検証提案の到達可能性",
})

#: IK-1120: 合意リストの支持線は、各行の対象が載っている論文の範囲で読む（台帳と同じ範囲 — IK-0852）。
#: 一覧と詳細で範囲が違うと、同じ欄名のまま「複数」と「単一」が並ぶ。件数は言わない。
OPEN_ASSUMPTIONS_SUPPORT_SCOPE_NOTE = (
    "観測からの支持線は、各行の対象が載っている論文の中で読んでいます（台帳と同じ範囲です）。"
)

#: 欄の出所の違いを 1 行で言う事実文（数値なし）。
LEDGER_FIELD_SOURCES_NOTE = (
    "検証状態・観測からの支持線・依存の広がり・行動上の合意は、それぞれ別の記録から読んでいます"
    "（一方が「あり」で他方が「なし」でも矛盾ではありません）。"
)

#: 検証状態が未記帳（unknown）のままスコープだけが記帳されている台帳の 1 文。
#: スコープの記帳は検証状態を変えない（状態の記帳は教員の別の操作）。
LEDGER_STATUS_UNRECORDED_WITH_SCOPES_NOTE = (
    "検証状態はまだ記帳されていません（検証スコープの記帳はあります。スコープを記帳しても"
    "検証状態は変わりません）。"
)

#: 支える主張ごとの疑義・提案の段階語（IK-1003）。件数は出さない。
SUPPORTING_CLAIM_CHALLENGE_STATE_LABELS = MappingProxyType({
    "challenged": "疑義あり",
    "proposed": "検証提案あり",
    "in_progress": "検証に着手済み",
})

#: 部品・式の台帳で、支える主張のどれかに疑義・提案が残っているときの事実文（IK-1003）。
SUPPORTING_CLAIMS_CHALLENGED_NOTE = (
    "この対象そのものへの疑義はありませんが、支える主張に疑義・検証提案が残っています"
    "（主張の台帳で読めます）。"
)

#: 取り下げた疑義の折りたたみの見出しと、取り下げ直後の事実文（IK-0998）。
WITHDRAWN_CHALLENGES_HEADING = "取り下げ済みの疑義（読み取りのみ）"
CHALLENGE_WITHDRAWN_NOTE = "取り下げました。記録は台帳に残り、「取り下げ済みの疑義」で読めます。"
#: 第 25 周 第 9 波（面 B・軽）: 同じ対象に同じ型・同じ理由の未取り下げの疑義が既にあるときの告知。
#: 記帳は止めない（重ねて記帳する判断は教員のもの）— 事実だけを言う。件数は言わない。
CHALLENGE_DUPLICATE_NOTE = (
    "記帳しました。この対象には、同じ型・同じ理由の疑義がすでに残っています（取り下げていないもの）。"
)

#: IK-1060（第 25 周 第 8 波）: 疑義の行の「誰が・いつ」。取り下げは本人のみ（取り下げた人 = 記帳者）。
#: 日時は日本時間の分まで（同じ日に同じ文面の取り下げが 2 件あっても見分けられる）。数値スコアではない。
CHALLENGE_RECORDED_TEMPLATE = "記帳: {name}（{at}）"
CHALLENGE_WITHDRAWN_BY_TEMPLATE = "取り下げ: {name}（{at}・記帳者本人）"
CHALLENGE_TIME_UNKNOWN = "日時の記録なし"


def challenge_timestamp_text(value: object) -> str:
    """疑義の日時を「YYYY-MM-DD HH:MM」（日本時間）にする。引けなければ固定文（IK-1060）。"""
    if value is None or value == "":
        return CHALLENGE_TIME_UNKNOWN
    from datetime import datetime, timedelta, timezone

    dt = value
    if not isinstance(dt, datetime):
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return CHALLENGE_TIME_UNKNOWN
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")


def challenge_record_line(
    challenger_name: object, created_at: object, status: object, updated_at: object = None,
) -> str:
    """疑義の帰属と時刻の 1 行（IK-1060）。取り下げ済みなら取り下げの日時と取り下げた人を足す。"""
    name = str(challenger_name or "").strip()
    line = CHALLENGE_RECORDED_TEMPLATE.format(name=name, at=challenge_timestamp_text(created_at))
    if str(status or "") == "withdrawn":
        line += " ／ " + CHALLENGE_WITHDRAWN_BY_TEMPLATE.format(
            name=name, at=challenge_timestamp_text(updated_at)
        )
    return line

# ── 台帳への書き込みを断る文・前提の出所と状態（IK-1027 / IK-1028・第 25 周 面 B 第 6 波）──────
#: 閲覧はできるが編集できない教員に台帳の画面が添える文（IK-0900）。台帳への書き込みを
#: サーバが断るとき（403）の detail も同じ文にする（IK-1027 — 画面とサーバで理由を言い分けない）。
LEDGER_READ_ONLY_NOTE = (
    "この論文の編集権限が無いため、台帳への記帳はできません（閲覧と疑義の記帳はできます）。"
)

#: 台帳の対象が解決できない・閲覧もできないときの書き込み拒否（404）の文（IK-1027）。
#: 存在の有無は言わない（fail-closed — 閲覧できない相手に対象の存在を漏らさない）。
LEDGER_WRITE_TARGET_NOT_FOUND_NOTE = "この台帳の対象は見つからないか、記帳できません。"

#: 前提（assumption_nodes.origin）の出所の画面の語（IK-1028。``a.origin`` の生の値を描かない）。
#: 語彙の正本は migration 030 の CHECK（mined_gap / mined_corpus / naive_aggregate / manual）。
ASSUMPTION_ORIGIN_LABELS = MappingProxyType({
    "mined_gap": "前提マイニング",
    "mined_corpus": "コーパス監査",
    "naive_aggregate": "素朴な問いの集約",
    "manual": "教員が手で記帳",
})

#: 前提（assumption_nodes.status）の段階の画面の語（IK-1028）。
ASSUMPTION_STATUS_LABELS = MappingProxyType({
    "candidate": "候補（未確定）",
    "confirmed": "確定済み",
    "operationalized": "確定済み（検証に着手）",
    "dismissed": "却下",
})

#: 語彙外の出所・段階に添える語（生の値を出さない）。
ASSUMPTION_LABEL_UNKNOWN = "記録の語が読めません"

#: 論文に結ばれていない前提のカードに添える事実文（IK-1028。台帳行は論文単位なので作れない）。
ASSUMPTION_NOT_LINKED_TO_DOCUMENT_NOTE = (
    "この前提はどの論文にも結ばれていないため、台帳の行がありません（検証スコープは論文に結んだ前提にだけ記帳できます）。"
)

#: IK-1058（第 25 周 第 8 波）: 依存の広がり（負荷）を計算できない前提（手で記帳し、グラフ上の主張・式・
#: ノードに結ばれていない前提）。値が無いことを「載せない」と読まず、計算できない段として言う。
#: 散布図の縦の位置は決められないので点にせず、地図の下に並べる。一覧・台帳にも同じ文。
ASSUMPTION_LOAD_UNCOMPUTABLE_NOTE = (
    "依存の広がり: 計算できません（この前提はグラフ上の主張・式に結ばれていないため、"
    "依存する範囲を辿れません）。"
)
ASSUMPTION_ATLAS_UNPLOTTED_HEADING = (
    "依存の広がりを計算できない前提（グラフ上の主張・式に結ばれていないため、散布図の縦の位置を決められません）"
)
#: IK-1074（第 25 周 第 9 波）: 「計算できない前提」の各行に添える区別の 1 行。散布図では横の位置が
#: 運ぶ検証スコープの被覆と、行キーの論文・前提を記帳した日時（同じ文面の前提を見分ける — IK-1060
#: と同じ「同文は日時で見分ける」形）。件数・生数値は言わない。
ASSUMPTION_UNPLOTTED_DETAIL_TEMPLATE = "検証スコープの被覆: {coverage} ／ 論文『{title}』 ／ 記帳: {at}"


def assumption_unplotted_detail_line(coverage_label: object, title: object, created_at: object) -> str:
    """「計算できない前提」の行の区別の 1 行（IK-1074）。題名が無ければ論文の欄を省く。"""
    coverage = str(coverage_label or "").strip() or ASSUMPTION_LABEL_UNKNOWN
    name = str(title or "").strip()
    at = challenge_timestamp_text(created_at)
    if not name:
        return f"検証スコープの被覆: {coverage} ／ 記帳: {at}"
    return ASSUMPTION_UNPLOTTED_DETAIL_TEMPLATE.format(coverage=coverage, title=name, at=at)


# ── 検証提案の完了の帰属と下流の語（IK-1121・第 25 周 第 10 波）──────────────────────
#: 完了の帰属の 1 行（取り下げの IK-1060 と同じ「誰が・いつ」の形）。日時は日本時間の分まで。
PROPOSAL_COMPLETED_BY_TEMPLATE = "完了: {name}（{at}）"
#: 完了の根拠（何を確かめたか）が書かれていないときの事実語。
PROPOSAL_COMPLETION_NOTE_EMPTY = "確かめたことの記帳なし"
PROPOSAL_COMPLETION_NOTE_TEMPLATE = "確かめたこと: {note}"
#: 完了しても検証状態は変わらない（人が別の操作で記帳する — SL1・AI に疑わせない）ことの事実文。
PROPOSAL_COMPLETED_STATUS_UNCHANGED_NOTE = (
    "提案の完了を記録しても、検証状態は変わりません（検証状態は教員が［検証状態を記帳する］で書きます）。"
)
#: 合意リスト・学習者の一覧・開幕の「確かめていないこと」で、検証提案の進み具合を言い分ける段階語。
#: 件数は言わない。キー: open（未完了だけ）/ completed（完了だけ）/ mixed（両方）。
VERIFICATION_PROPOSAL_PRESENCE_LABELS = MappingProxyType({
    "open": "検証提案あり",
    "completed": "検証提案あり（完了）",
    "mixed": "検証提案あり（完了と未完了）",
})


def proposal_completed_line(actor_name: object, completed_at: object) -> str:
    """検証提案の完了の帰属の 1 行（IK-1121）。名前は呼び出し側が表示名に解決して渡す。"""
    name = str(actor_name or "").strip() or ACCOUNT_NAME_UNREGISTERED
    return PROPOSAL_COMPLETED_BY_TEMPLATE.format(name=name, at=challenge_timestamp_text(completed_at))


def proposal_completion_note_line(note: object) -> str:
    """完了の根拠の 1 行（空なら記帳なしの事実語）。"""
    text = str(note or "").strip()
    return PROPOSAL_COMPLETION_NOTE_TEMPLATE.format(note=text) if text else PROPOSAL_COMPLETION_NOTE_EMPTY


def verification_proposal_presence(open_count: int, completed_count: int) -> str:
    """検証提案の段階キー（件数は外へ出さない）。どちらも無ければ空文字。"""
    if open_count > 0 and completed_count > 0:
        return "mixed"
    if completed_count > 0:
        return "completed"
    if open_count > 0:
        return "open"
    return ""


def assumption_origin_label(origin: object) -> str:
    """前提の出所の画面の語（語彙外は :data:`ASSUMPTION_LABEL_UNKNOWN`）。"""
    return ASSUMPTION_ORIGIN_LABELS.get(str(origin or ""), ASSUMPTION_LABEL_UNKNOWN)


def assumption_status_label(status: object) -> str:
    """前提の段階の画面の語（語彙外は :data:`ASSUMPTION_LABEL_UNKNOWN`）。"""
    return ASSUMPTION_STATUS_LABELS.get(str(status or ""), ASSUMPTION_LABEL_UNKNOWN)

# ---------------------------------------------------------------------------
# 人に見せる名前（IK-0848 / IK-1007・第 25 周 第 5 波）
# ---------------------------------------------------------------------------
# ``users.display_name`` はログインの照合列を兼ねるので、帰属・所有者・査読者・記帳者の
# 表示には使わない。表示は本人が登録した ``users.profile_name`` だけで、空なら下の事実文に
# 戻る（ログイン ID へフォールバックしない）。読み手は ``core/account_display.py``。

#: 名前の登録が無いことの事実文（単独の行・欄に出す）。
ACCOUNT_NAME_UNREGISTERED = "名前の登録がありません"

#: 文の主語として名前の代わりに置く事実語（「〜が採用しました」の主語）。
ACCOUNT_NAME_UNREGISTERED_ACTOR = "名前を登録していない利用者"

#: 本人が名前を登録・変更したときの事実文。
ACCOUNT_PROFILE_NAME_SAVED = "名前を保存しました。共有先の画面には、この名前が出ます。"

#: 本人が名前を空にしたときの事実文。
ACCOUNT_PROFILE_NAME_CLEARED = (
    "名前を消しました。共有先の画面には「" + ACCOUNT_NAME_UNREGISTERED + "」と出ます。"
)


# ---------------------------------------------------------------------------
# 凍結した教材の ⚓ が指す部品が置き換わったとき（IK-1021 doubt 側・第 25 周 第 7 波）
# ---------------------------------------------------------------------------
# 学習者の部品ポップアップ・説明ポップアップは、後継が決まらないとき凍結時の部品名を見出しに置き、
# 次の 1 行で事実を言う（推測で別の部品へ結ばない — 第 6 波の判断どおり）。

#: 凍結時の部品名が引けたときの事実文（``{label}`` は凍結時の部品名）。
FROZEN_COMPONENT_SUPERSEDED_LINE = (
    "教材を作ったときの部品『{label}』は、再解析で置き換わり、後継が決まっていません。"
)

#: 凍結時の部品名が引けないときの事実文。
FROZEN_COMPONENT_SUPERSEDED_LINE_UNNAMED = (
    "教材を作ったときの部品は、再解析で置き換わり、後継が決まっていません。"
)


#: 第 7 波（IK-1051・面 A）: 出典の重なる新しい部品が 1 つも無かったとき（「決まらない」と言わない）。
FROZEN_COMPONENT_NOT_FOUND_LINE = (
    "教材を作ったときの部品『{label}』は、再解析で置き換わりました。出典の重なる新しい部品は見つかりません。"
)
FROZEN_COMPONENT_NOT_FOUND_LINE_UNNAMED = (
    "教材を作ったときの部品は、再解析で置き換わりました。出典の重なる新しい部品は見つかりません。"
)


def frozen_component_superseded_line(label: object, reason: object = "") -> str:
    """置き換わって後継の決まらない部品の事実文（凍結時の名前があれば名指す）。

    ``reason == "not_found"``（出典の重なる新しい部品が 1 つも無い — 第 7 波 IK-1051）は
    「見つかりません」と言う。それ以外（1 対 1 に決まらない）は「後継が決まっていません」。
    """
    name = str(label or "").strip()
    if str(reason or "") == "not_found":
        return FROZEN_COMPONENT_NOT_FOUND_LINE.format(label=name) if name else FROZEN_COMPONENT_NOT_FOUND_LINE_UNNAMED
    return FROZEN_COMPONENT_SUPERSEDED_LINE.format(label=name) if name else FROZEN_COMPONENT_SUPERSEDED_LINE_UNNAMED


#: 第 25 周 第 10 波（IK-1127）: 版の管理モーダルで、発行した版が宛先ごとにどう届くかの事実文
#: （V層の確定仕様: 受講者 = 有効な版を同意なしに読む / 閲覧の共有先 = 取り込むまでピンした版 /
#: 編集の共有先 = 常に最新の内容）。``versioning.js`` は ``version-state`` の文を素通しで描く。
VERSION_AUDIENCE_NOTE_COURSE = (
    "発行すると、受講者（学習者）はその時点から新しい版を見ます（受講者の同意は要りません）。"
    "共有先の教員のうち閲覧の共有先は、取り込む（同意する）まで自分の版に留まります。"
    "編集の共有先は常にいまの内容を見ます。"
)
#: 発行の後に変わったトピックの題名の一覧の見出し（件数は出さない）。
VERSION_CHANGED_TOPICS_LABEL = "発行の後に変わったトピック"


#: IK-1124（第 25 周 第 10 波）: ナレッジライブラリのエントリを凍結する確認文に添える、学生の画面への
#: 効果の事実文。学習者向けの「共通部品として」（部品の文脈）と記号の吹き出しの概念名は、確定した
#: 結び付きに加えて凍結版があるときだけ出る（``library_store.get_frozen_entry_for_learner`` /
#: ``symbol_lookup`` の凍結版の join）。admin.js の凍結の確認文は逐語ミラー（一致はテストで固定）。
LIBRARY_FREEZE_LEARNER_EFFECT_NOTE = (
    "凍結すると、この共通部品に確定済みで結び付いた教材の部品・記号が、学生の画面"
    "（部品の文脈の「共通部品として」・記号の吹き出しの概念名）に出るようになります。"
)
#: IK-1152: 記号と概念の結び付きを確定した直後の知らせ（部品の「共通部品として」ではなく
#: 記号の吹き出しの概念名に出る。JS にミラーしない — サーバの文を素通しで描く）。
SYMBOL_LINK_CONFIRMED_FACT = (
    "記号と概念の結び付きを確定しました（{line}）。学生の記号の吹き出しに概念名として出るのは、"
    "この共通部品に凍結した版があるときです。取り消すときは、エントリの詳細の［確定を取り消す］から"
    "理由を書いて取り消します。"
)
