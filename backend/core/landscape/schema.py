"""知識ランドスケープ — 語彙とラベルの正本（設計書 §8）。

正本: ``docs/features/knowledge_landscape_design.md``（不変条項 LS1〜LS10 は §0）。

DB にも FastAPI にも LLM SDK にも依存しない純粋な語彙定義に留める
（``core/teaching_figures/schema.py`` / ``core/deliberation/schema.py`` と同じ立場）。

ここの語彙は **migration 065 の CHECK 制約と一対一で一致させること**
（ガードレールテスト ``backend/tests/test_landscape_guardrails.py`` が SQL 本文を
パースして完全一致を検査する）。

LS5「数値を見せない」: weight / confidence の生値を返す関数はここに置かない。
表示に出せるのは段階ラベル（:func:`weight_label`）と出所ラベル
（:func:`provenance_label`）・状態ラベル（:func:`status_label`）だけである。
"""

from __future__ import annotations

import re

from collections.abc import Iterable, Mapping
from typing import Any

from core.label_vocab import (
    LANDSCAPE_EVIDENCE_SOURCE_LABELS as _EVIDENCE_SOURCE_LABELS,
    LANDSCAPE_REASON_BEYOND_QUOTE_TEMPLATE as REASON_BEYOND_QUOTE_TEMPLATE,
    LANDSCAPE_REASON_SOURCE_LABEL as REASON_SOURCE_LABEL,
    LANDSCAPE_STATUS_LABELS as _LANDSCAPE_STATUS_LABELS,
    WEIGHT_LABELS,
    WEIGHT_LEVEL_SCALE,
    WEIGHT_RELATION,
    WEIGHT_THRESHOLD_MEDIUM,
    WEIGHT_THRESHOLD_STRONG,
)

# ---------------------------------------------------------------------------
# 観点（perspective）— 1論文が複数領域へ異なる観点で配置されるのが既定（LS1）
# ---------------------------------------------------------------------------

PERSPECTIVE_SUBJECT = "subject"
PERSPECTIVE_QUESTION = "question"
PERSPECTIVE_METHOD = "method"
PERSPECTIVE_THEORY = "theory"
PERSPECTIVE_OBSERVATION = "observation"
PERSPECTIVE_APPLICATION = "application"

PERSPECTIVES = (
    PERSPECTIVE_SUBJECT,
    PERSPECTIVE_QUESTION,
    PERSPECTIVE_METHOD,
    PERSPECTIVE_THEORY,
    PERSPECTIVE_OBSERVATION,
    PERSPECTIVE_APPLICATION,
)

PERSPECTIVE_LABELS: dict[str, str] = {
    PERSPECTIVE_SUBJECT: "対象から",
    PERSPECTIVE_QUESTION: "問いから",
    PERSPECTIVE_METHOD: "方法から",
    PERSPECTIVE_THEORY: "理論から",
    PERSPECTIVE_OBSERVATION: "観測から",
    PERSPECTIVE_APPLICATION: "応用から",
}


def is_valid_perspective(value: str) -> bool:
    return value in PERSPECTIVES


def perspective_label(value: str) -> str:
    """観点の日本語表示名（未登録の値はそのまま返す）。"""
    return PERSPECTIVE_LABELS.get(value, value)


# ---------------------------------------------------------------------------
# 状態（status）— LS3: AI 配置は inferred 止まり、確定は教員のみ
# ---------------------------------------------------------------------------

#: AI（LLM / 決定論アルゴリズム）由来の候補。教員未確認。
STATUS_INFERRED = "inferred"
#: 教員が明示的に確認した配置。
STATUS_CONFIRMED = "confirmed"
#: 教員が却下した配置（行は保持する = P4。学習者には出さない）。
STATUS_REJECTED = "rejected"
#: 教員が再検討に回した配置（学習者には「確認待ち（AI推定）」で見える）。
STATUS_REVIEW_REQUIRED = "review_required"
#: 再解析で置き換えられた旧候補（履歴。学習者にも一覧の既定にも出さない）。
STATUS_SUPERSEDED = "superseded"

STATUSES = (
    STATUS_INFERRED,
    STATUS_CONFIRMED,
    STATUS_REJECTED,
    STATUS_REVIEW_REQUIRED,
    STATUS_SUPERSEDED,
)

#: 「生きている」状態（superseded 以外）。migration 065 の部分一意インデックス
#: ``uq_landscape_placements_live`` の条件と対応する。手動の status 遷移
#: （``store.update_status``）が許すのはこの集合の内側だけ。
LIVE_STATUSES = (
    STATUS_INFERRED,
    STATUS_CONFIRMED,
    STATUS_REJECTED,
    STATUS_REVIEW_REQUIRED,
)

#: 学習者に見せてよい状態（§4.2）。rejected / superseded は見えない。
LEARNER_VISIBLE_STATUSES = (
    STATUS_CONFIRMED,
    STATUS_INFERRED,
    STATUS_REVIEW_REQUIRED,
)

#: 再解析で ``superseded`` へ落として良い状態（LS3: 教員の判断は AI が上書きしない）。
SUPERSEDABLE_STATUSES = (STATUS_INFERRED,)

#: 教員向けの状態名。正本は ``core/label_vocab.py::LANDSCAPE_STATUS_LABELS``（第 23 波 F5:
#: 管理画面 2 か所の JS フォールバックが逐語ミラーする相手を 1 つにする）。
STATUS_LABELS = _LANDSCAPE_STATUS_LABELS


def is_valid_status(value: str) -> bool:
    return value in STATUSES


def status_label(value: str) -> str:
    """状態の日本語表示名（事実文のみ。督促・煽り表現を入れない — LS1/LS8）。"""
    return STATUS_LABELS.get(value, value)


# ---------------------------------------------------------------------------
# 出所ラベル（§4.2 / LS8 コーパスの正直さ・AC-005）
# ---------------------------------------------------------------------------

#: status → 学習者・教員の両画面で必ず併記する出所ラベル。
PROVENANCE_LABELS: dict[str, str] = {
    STATUS_CONFIRMED: "教員確認済み",
    STATUS_INFERRED: "AIによる推定（未確認）",
    STATUS_REVIEW_REQUIRED: "確認待ち（AI推定）",
}

#: 未知・非表示状態に対する既定ラベル。最も慎重な「AI推定」に倒す
#: （教員確認済みに見せる方向へは絶対に倒さない）。
DEFAULT_PROVENANCE_LABEL = PROVENANCE_LABELS[STATUS_INFERRED]


def provenance_label(status: str) -> str:
    """**status** から出所ラベルを引く（AC-005: AI推定と教員確認を必ず区別する）。

    引数が status なのは設計書 §8 の契約どおり — 表示上の「出所」は
    ``provenance``（llm/deterministic/human = 生成手段）ではなく
    「教員が確認したかどうか」で決まるため。未知の status は
    :data:`DEFAULT_PROVENANCE_LABEL`（AI推定側）へ倒す。
    """
    return PROVENANCE_LABELS.get(status, DEFAULT_PROVENANCE_LABEL)


# ---------------------------------------------------------------------------
# 根拠の出所と理由の印（IK-0677 / IK-0967）
# ---------------------------------------------------------------------------
#
# 配置の「根拠」は verbatim 検査（LS4）を通った引用だが、照合先には解析が論文全体を
# 再構成した文（中心の問い・命題・目標。日本語のこともある）も含まれる。引用の出所を
# 印で言い分け、理由文（LLM の要約）には要約の印を付ける。LS4 の検査自体は緩めない。

#: 引用の出所: 論文の主張（claim）の本文から。
EVIDENCE_SOURCE_CLAIM = "claim"
#: 引用の出所: 解析が論文全体を再構成した文（問い・命題・目標・見出しの主張）から。
EVIDENCE_SOURCE_PAPER_SUMMARY = "paper_summary"
EVIDENCE_SOURCES = (EVIDENCE_SOURCE_CLAIM, EVIDENCE_SOURCE_PAPER_SUMMARY)
#: 出所の記録が無い旧行で ``claim_id`` も無いもの（表示だけの区分・保存しない）。
EVIDENCE_SOURCE_UNRECORDED = "unrecorded"

#: 出所 → 印（正本は ``label_vocab.LANDSCAPE_EVIDENCE_SOURCE_LABELS``）。
EVIDENCE_SOURCE_LABELS = _EVIDENCE_SOURCE_LABELS


def evidence_source_for(quote: str, claim_id: Any, claim_texts: Iterable[str] = ()) -> str:
    """引用の出所を決める（決定論）。

    ``claim_id`` があるか、引用が主張の本文に逐語で現れれば :data:`EVIDENCE_SOURCE_CLAIM`。
    それ以外は論文全体の記述（解析の再構成）から引いたもの。
    """
    if str(claim_id or "").strip():
        return EVIDENCE_SOURCE_CLAIM
    needle = " ".join(str(quote or "").split())
    if needle:
        for text in claim_texts or ():
            if needle in " ".join(str(text or "").split()):
                return EVIDENCE_SOURCE_CLAIM
    return EVIDENCE_SOURCE_PAPER_SUMMARY


def evidence_source_label(entry: Mapping[str, Any]) -> str:
    """1 件の evidence → 出所の印（記録が無い旧行は ``claim_id`` の有無で決める）。

    旧行（``source`` 未記録）で ``claim_id`` が null の引用は、再構成文からと断定せず
    「原文の逐語とは限りません」の印にする（推測で埋めない）。
    """
    source = str(entry.get("source") or "")
    if source not in EVIDENCE_SOURCES:
        source = (
            EVIDENCE_SOURCE_CLAIM
            if str(entry.get("claim_id") or "").strip()
            else EVIDENCE_SOURCE_UNRECORDED
        )
    return EVIDENCE_SOURCE_LABELS[source]


def _is_proper_term(term: str) -> bool:
    """固有の語らしいか（IK-0967 残り・第 25 周 第 5 波）。

    要約が英語の句を日本語の文に混ぜただけの普通の語（``dynamical`` / ``dark`` / ``energy``
    のような小文字だけの語）は注意の対象から外し、略語・固有名・型番（大文字を含む・数字を
    含む: ``BAO`` / ``Ia`` / ``Cepheus`` / ``DR2``）だけを挙げる。照合の正本（A層の
    ``reason_scope``）は変えず、表示の段で絞る。
    """
    text = str(term or "")
    return any(ch.isupper() or ch.isdigit() for ch in text)


#: 頭字語の展開で読み飛ばしてよい機能語（「Large Magellanic Cloud」は LMC、「equation of state」は EoS）。
_ACRONYM_SKIP_WORDS = frozenset({"of", "the", "and", "for", "in", "on", "to", "a", "an", "de"})
_ACRONYM_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def acronym_expanded_in(term: str, quotes: Iterable[str]) -> bool:
    """頭字語 ``term`` の正式名（連続する語の頭文字の連なり）が引用に現れるか（IK-1046）。

    「TOV」と「Tolman-Oppenheimer-Volkoff equations」のように、引用の連続する語
    （ハイフン区切りも語の区切り）の頭文字を大小無視で並べたものが ``term`` と一致すれば、
    引用に現れたと数える。機能語（of / the 等）は頭文字に使わずに読み飛ばしてよい。
    頭字語らしくない語（大文字 2 文字以上を含まない・6 文字を超える）は照合しない。
    決定論・LLM 0 回。
    """
    text = str(term or "")
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 2 or len(letters) > 6 or len(letters) != len(text):
        return False
    if sum(1 for ch in letters if ch.isupper()) < 2:
        return False
    target = [ch.lower() for ch in letters]
    for quote in quotes or ():
        words = _ACRONYM_WORD_RE.findall(str(quote or ""))
        for start in range(len(words)):
            if words[start][0].lower() != target[0]:
                continue
            pos = 0
            idx = start
            while idx < len(words) and pos < len(target):
                word = words[idx]
                if word[0].lower() == target[pos]:
                    pos += 1
                elif word.lower() in _ACRONYM_SKIP_WORDS:
                    pass
                else:
                    break
                idx += 1
            if pos == len(target):
                return True
    return False


#: 切り詰めた略（「Cep」= Cepheus）と見なす最短の長さ。2 文字（Ia 等）は照合しない。
_TRUNCATION_MIN_LEN = 3


def truncation_matched_in(term: str, quotes: Iterable[str]) -> bool:
    """語 ``term`` と引用の語が、片方を切り詰めた略の関係にあるか（IK-1046 残り・第 25 周 第 8 波）。

    「Cepheus」と引用の「Cep B」のように、大文字で始まる英字だけの語どうしで、短い方（3 文字以上）が
    長い方の先頭と大小無視で一致すれば、同じものの別表記として「引用に現れた」と数える。
    頭字語（:func:`acronym_expanded_in`）の双対。数字を含む型番（DR2 等）・2 文字以下は照合しない。
    決定論・LLM 0 回。
    """
    text = str(term or "")
    if not text.isalpha() or not text[:1].isupper() or len(text) < _TRUNCATION_MIN_LEN:
        return False
    low = text.lower()
    for quote in quotes or ():
        for word in _ACRONYM_WORD_RE.findall(str(quote or "")):
            if not word.isalpha() or not word[:1].isupper() or len(word) < _TRUNCATION_MIN_LEN:
                continue
            w = word.lower()
            if w == low:
                continue
            short, long_ = (w, low) if len(w) < len(low) else (low, w)
            if long_.startswith(short):
                return True
    return False


def reason_beyond_quote_note(reason: str, quotes: Iterable[str]) -> str:
    """理由が引用の範囲を越える固有の語を含むときの一文（無ければ空文字）。

    照合の正本は agent 側 ``landscape_placement.reason_scope``（validator の warning と
    同じ関数）。読めないときは空文字（fail-soft — 一文が出ないだけ）。
    """
    try:
        from episteme_graph.agents.landscape_placement.reason_scope import (
            reason_terms_beyond_quote,
        )
    except Exception:  # noqa: BLE001
        return ""
    quote_list = [str(q or "") for q in quotes or ()]
    # IK-1046（第 25 周 第 7 波）: 略称の正式名が引用にあれば「現れない」と言わない。
    terms = [
        t
        for t in reason_terms_beyond_quote(reason, quote_list)
        if _is_proper_term(t)
        and not acronym_expanded_in(t, quote_list)
        # IK-1046 残り（第 8 波）: 切り詰めた略（Cep ⇔ Cepheus）も同じものの別表記として数える。
        and not truncation_matched_in(t, quote_list)
    ]
    if not terms:
        return ""
    return REASON_BEYOND_QUOTE_TEMPLATE.format(terms="・".join(f"「{t}」" for t in terms))


# ---------------------------------------------------------------------------
# 生成手段（provenance 列）
# ---------------------------------------------------------------------------

PROVENANCE_LLM = "llm"
PROVENANCE_DETERMINISTIC = "deterministic"
PROVENANCE_HUMAN = "human"
PROVENANCES = (PROVENANCE_LLM, PROVENANCE_DETERMINISTIC, PROVENANCE_HUMAN)


def is_valid_provenance(value: str) -> bool:
    return value in PROVENANCES


# ---------------------------------------------------------------------------
# mapping_justification（概念レジストリ KR4 / migration 082 §4.6）
# ---------------------------------------------------------------------------
#
# 「なぜここに置けたか」。生成手段（provenance）から決定論的に導く。写像は migration
# 082 のバックフィル UPDATE と**同じ規則**でなければならない（ガードレールが固定する）。
# 語彙の正本は core/schema.py::MAPPING_JUSTIFICATIONS。

JUSTIFICATION_MANUAL = "manual_curation"

_JUSTIFICATION_BY_PROVENANCE: dict[str, str] = {
    PROVENANCE_LLM: "llm_candidate",
    PROVENANCE_HUMAN: JUSTIFICATION_MANUAL,
    PROVENANCE_DETERMINISTIC: "lexical_match",
}


def justification_for_provenance(provenance: str) -> str | None:
    """生成手段から ``mapping_justification`` を導く（未知は ``None`` = 記録しない）。

    KR4「推測で埋めない」: 写像に無い値は黙って ``manual_curation`` に倒さず、
    記録なし（NULL）のままにする。
    """
    return _JUSTIFICATION_BY_PROVENANCE.get(str(provenance or "").strip())


# ---------------------------------------------------------------------------
# ノード種別（骨格の region / concept）
# ---------------------------------------------------------------------------

NODE_KIND_REGION = "region"
NODE_KIND_CONCEPT = "concept"
NODE_KINDS = (NODE_KIND_REGION, NODE_KIND_CONCEPT)


def is_valid_node_kind(value: str) -> bool:
    return value in NODE_KINDS


# ---------------------------------------------------------------------------
# 関連の段階ラベル（LS5: weight の生値を出さない）
# ---------------------------------------------------------------------------

# 段階の境界（設計書 §8: >=0.7 strong / >=0.4 medium / else weak）・訳語・
# 「未測定は最も慎重な段階へ倒す」規則の正本は core/label_vocab.py。
WEIGHT_LEVEL_STRONG = "strong"
WEIGHT_LEVEL_MEDIUM = "medium"
WEIGHT_LEVEL_WEAK = "weak"
WEIGHT_LEVELS = (WEIGHT_LEVEL_STRONG, WEIGHT_LEVEL_MEDIUM, WEIGHT_LEVEL_WEAK)

DEFAULT_WEIGHT = 0.5


def weight_level(value: object) -> str:
    """weight の生値を段階キー（strong / medium / weak）へ変換する。

    数値化できない・未測定（``None``）は最も慎重な ``weak`` に倒す
    （情報が無いことを強い関連に見せない）。
    """
    return WEIGHT_LEVEL_SCALE.label_for(value)


def weight_label(value: object) -> str:
    """weight の生値を段階ラベル（強い関連 / 関連 / 弱い関連）へ変換する（LS5）。"""
    return WEIGHT_RELATION.label_for(value)


def normalize_weight(value: object) -> float:
    """LLM / 呼び出し側の weight を ``[0.0, 1.0]`` の float に正規化する。

    数値化できない値は :data:`DEFAULT_WEIGHT`、範囲外は端へクランプする
    （DB の ``weight REAL NOT NULL DEFAULT 0.5`` と揃える。生値は DB 内部にしか
    出ないため、破棄よりも決定論的なクランプが扱いやすい）。
    """
    try:
        if value is None:
            return DEFAULT_WEIGHT
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_WEIGHT
    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return numeric


# ---------------------------------------------------------------------------
# その他の規約
# ---------------------------------------------------------------------------

#: パイプライン由来の created_by 既定値（教員の手動再提案は user_id を入れる）。
PIPELINE_CREATED_BY = "pipeline"

#: 学習者 DTO から必ず落とすキー（LS5。projection 側の実装が参照する）。
FORBIDDEN_NUMERIC_KEYS = ("weight", "confidence")


__all__ = [
    "DEFAULT_PROVENANCE_LABEL",
    "DEFAULT_WEIGHT",
    "FORBIDDEN_NUMERIC_KEYS",
    "LEARNER_VISIBLE_STATUSES",
    "LIVE_STATUSES",
    "NODE_KINDS",
    "NODE_KIND_CONCEPT",
    "NODE_KIND_REGION",
    "PERSPECTIVES",
    "PERSPECTIVE_APPLICATION",
    "PERSPECTIVE_LABELS",
    "PERSPECTIVE_METHOD",
    "PERSPECTIVE_OBSERVATION",
    "PERSPECTIVE_QUESTION",
    "PERSPECTIVE_SUBJECT",
    "PERSPECTIVE_THEORY",
    "PIPELINE_CREATED_BY",
    "PROVENANCES",
    "PROVENANCE_DETERMINISTIC",
    "PROVENANCE_HUMAN",
    "PROVENANCE_LABELS",
    "PROVENANCE_LLM",
    "STATUSES",
    "STATUS_CONFIRMED",
    "STATUS_INFERRED",
    "STATUS_LABELS",
    "STATUS_REJECTED",
    "STATUS_REVIEW_REQUIRED",
    "STATUS_SUPERSEDED",
    "SUPERSEDABLE_STATUSES",
    "WEIGHT_LABELS",
    "WEIGHT_LEVELS",
    "WEIGHT_LEVEL_MEDIUM",
    "WEIGHT_LEVEL_STRONG",
    "WEIGHT_LEVEL_WEAK",
    "WEIGHT_THRESHOLD_MEDIUM",
    "WEIGHT_THRESHOLD_STRONG",
    "is_valid_node_kind",
    "is_valid_perspective",
    "is_valid_provenance",
    "justification_for_provenance",
    "JUSTIFICATION_MANUAL",
    "is_valid_status",
    "normalize_weight",
    "perspective_label",
    "provenance_label",
    "status_label",
    "weight_label",
    "weight_level",
]
