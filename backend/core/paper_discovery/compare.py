"""論文レーダー — 起点論文と候補の比較分析（本層で唯一のテキスト LLM）。

設計正本: ``docs/features/paper_radar_design.md`` §5.3（不変条項 PR4 / PR7）。

seed 論文の要旨・中心命題と、選ばれた候補のアブストラクトを **1リクエスト = 1コール**で
突き合わせ、「重なり / 違い」を候補ごとの短い仮説文で返す。重なりは可能なら
**起点論文の部品（承認済み theory_components のラベル）に紐づけて**返し、どの部分が
重なっていそうなのかを読めるようにする（LLM コール数は増やさない — 同一コールの出力拡張）。

このモジュールが守るもの:

- **PR4 比較文は AI の推定・非保存・出所明示**: 結果はレスポンス限りで DB に書かない。
  各違い・各重なりには候補アブストラクトからの **verbatim** ``evidence_quote`` を必須とし、
  逐語で一致しないものは**その1件だけ** drop して ``notes`` に正直に積む
  （discuss_opening / figure_suggest と同じ捏造ガード）。
  重なりの ``component_label`` も**供給した部品リストの閉世界**で照合し、リスト外の
  名前は**空文字へ落として statement は残す**（P4 情報を落とさない — 項目ごと捨てない）。
  注意書き :data:`CAVEAT` は**サーバ側の固定文**で、LLM 出力に依存しない。
- **PR6 候補の素材はサーバが取り直す**: 要旨はクライアントから受け取らず、
  保存済みの写し（``metadata_cache`` / migration 085）か
  :func:`arxiv_client.fetch_by_ids` から取得する（verbatim 検査の土台を本物にする。
  写しはライブ取得と同一の本文）。seed の要旨も同じ1コールに相乗りさせるので、
  arXiv への呼び出しは**足りない分がある場合だけ1回**（設計書 §14）。
- **PR7 閉世界の正直さ**: 比較はアブストラクトの範囲で言えることに限る
  （本文は取得しない — 取り込みの弁を迂回しない）。引けなかった候補は ``skipped`` に
  事実文つきで返し、黙って落とさない。
- **素材なしで創作しない**: seed 側の素材が1つも無ければ **LLM を呼ばず**
  :class:`NoSeedMaterialError`（route が 422 の事実文にする）。

**日次コスト上限（CostGate）は route 層の責務**（``figure_suggest`` と同じ配置。
設計書 §5.3 — このモジュールはゲートを持たない）。FastAPI 非 import。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from core.paper_discovery import arxiv_client, metadata_cache, radar
from core.paper_discovery.schema import ArxivEntry, normalize_arxiv_id
from core.text_hygiene import UNTRUSTED_SOURCE_NOTICE

logger = logging.getLogger(__name__)

#: 1リクエストで比較できる候補の上限（1コールに同梱するため定数。env にしない）。
RADAR_COMPARE_MAX_CANDIDATES = 10

#: U層 feature（``KNOWN_FEATURES`` / ``llm_policy`` と3点同時登録してある）。
FEATURE_RADAR_COMPARE = "discovery:compare"

#: M層のモデル設定キー（``DISCOVERY_COMPARE_LLM_MODEL``。空なら fast tier）。
_MODEL_SETTING_KEY = "discovery_compare_llm_model"

#: 比較結果に必ず添えるサーバ側固定文（PR4 — LLM に書かせない）。
CAVEAT = (
    "アブストラクト（要旨）の比較に基づく AI の推定です。本文は確認されていません。"
)

#: 違いの観点語彙。語彙外は :data:`ASPECT_UNKNOWN` へ落とす（fail-closed。
#: 情報は落とさず「分類できなかった」として残す）。
COMPARE_ASPECTS = ("approach", "conclusion", "scope", "method", "theme", "unknown")
ASPECT_UNKNOWN = "unknown"

#: 起点論文と候補の関係の種類（設計書 §18 — 「近くない」を言える出口）。
#: 正本はこのモジュール（``schema.py`` / ``label_vocab`` は別作業が編集中のため
#: ここに置く。移すときは逐語で移し、二重に持たない）。
RELATION_SHARED_SUBJECT = "shared_subject"
RELATION_SHARED_METHOD = "shared_method"
RELATION_ADJACENT = "adjacent"
RELATION_NONE = "no_notable_common_ground"
RELATION_KINDS = (
    RELATION_SHARED_SUBJECT,
    RELATION_SHARED_METHOD,
    RELATION_ADJACENT,
    RELATION_NONE,
)
#: 関係の種類の日本語ラベル（数値を含めない。JS にはミラーせず DTO で素通しする）。
RELATION_LABELS = {
    RELATION_SHARED_SUBJECT: "同じ対象を扱う",
    RELATION_SHARED_METHOD: "同じ方法を使う",
    RELATION_ADJACENT: "隣り合う話題",
    RELATION_NONE: "要旨からは目立った共通点が読み取れません",
}

#: 共通点の soft 検査で「具体的な語」として拾う連なり（ラテン・ギリシャ文字・数字と
#: 記号の 2 文字以上 / カタカナ 3 文字以上）。漢字は一般語（理論・観測）と区別できない
#: ため拾わない（設計書 §18.3 の限界）。
_TERM_RE = re.compile(
    r"[A-Za-z0-9\u0370-\u03ff\u2190-\u21ff+\-/^_]{2,}|[\u30a0-\u30ffー]{3,}"
)
#: 語として数えない汎用のラテン語（日本語の共通点に紛れても具体性の根拠にならない）。
_GENERIC_TERMS = frozenset(
    {
        "the", "and", "for", "with", "model", "models", "theory", "data", "paper",
        # 日本語の一般論に混じりやすいカタカナの汎用語。
        "パラメータ", "パラメーター", "データ", "モデル", "フレームワーク", "アプローチ",
        "スケール", "シミュレーション",
    }
)

#: プロンプトへ載せる本文の上限（膨張の防波堤）。
MAX_ABSTRACT_CHARS = 4000
MAX_SEED_FIELD_CHARS = 1200

#: 重なりに紐づけられる部品ラベルの供給元（``radar.seed_keyphrase_candidates`` の語彙。
#: 承認済み theory_components だけが ``component`` で来る — 語彙を増やさない）。
COMPONENT_LABEL_SOURCE = "component"

#: 取り込み済み候補の解析結果（IK-0818 / 設計書 §22.5）。
#: 候補 1 件あたりに渡す部品ラベルの上限（膨張の防波堤。radar.MAX_SEED_KEYPHRASES と同じ値）。
MAX_CANDIDATE_COMPONENT_LABELS = 12
#: 重なりが紐づいた部品リストの側（``overlaps[].side``）。
OVERLAP_SIDE_SEED = "seed"
OVERLAP_SIDE_CANDIDATE = "candidate"
OVERLAP_SIDE_BOTH = "both"
OVERLAP_SIDES = (OVERLAP_SIDE_SEED, OVERLAP_SIDE_CANDIDATE, OVERLAP_SIDE_BOTH)
#: 引用の出所（``evidence_source``）。``abstract`` = 候補の要旨 / ``thesis`` = 候補の
#: 解析結果の中心命題（取り込み済み候補のみ）。
EVIDENCE_SOURCE_ABSTRACT = "abstract"
EVIDENCE_SOURCE_THESIS = "thesis"
#: 承認済み部品が無い候補では未承認（candidate 等）を明示ラベル付きで渡す。却下は渡さない。
_EXCLUDED_CANDIDATE_REVIEW_STATUSES = ("rejected", "teacher_rejected")
#: 出所の事実文（サーバ側固定文。UI はこれを素通しする — JS に表を持たない）。
NOTE_SOURCE_CANDIDATE_PART = "候補側の解析結果から（部品）"
NOTE_SOURCE_BOTH_PARTS = "起点と候補の両方の解析結果から（部品）"
NOTE_SOURCE_UNAPPROVED_PART = "候補側の部品は教員の承認前です"
NOTE_SOURCE_THESIS_QUOTE = "引用は候補の解析結果（中心命題）から"
NOTE_CANDIDATE_ANALYSIS_USED = (
    "この候補は取り込み済みのため、要旨に加えてこちらの解析結果（中心命題・部品）も"
    "比較の材料にしています。"
)

#: 事実文（数値を含めない）。
NOTE_QUOTE_NOT_VERBATIM = (
    "候補の要旨に見つからない引用が含まれていたため、その項目は表示していません。"
)
NOTE_ITEM_MISSING = "一部の候補については比較結果が返りませんでした。"
DETAIL_METADATA_UNAVAILABLE = "arXiv から論文情報を取得できませんでした。"

_WS_RE = re.compile(r"\s+")


class CompareError(Exception):
    """比較分析の失敗（route が HTTP へ写す）。"""


class NoSeedMaterialError(CompareError):
    """seed 側に比較の素材（要旨・中心命題）が1つも無い（LLM を呼ばない）。"""


class CompareUnavailableError(CompareError):
    """候補メタデータが1件も引けない / LLM 呼び出しが失敗した。"""


def normalize_for_quote_match(text: Any) -> str:
    """引用照合用の正規化（空白の畳み込みのみ。語形・記号・大小文字は触らない）。

    ``core/teaching_figures/suggest.py::normalize_for_quote_match`` と同じ規約。
    """
    return _WS_RE.sub(" ", str(text or "")).strip()


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


# ---------------------------------------------------------------------------
# 素材の組み立て（非LLM）
# ---------------------------------------------------------------------------


def _artifact(artifacts: Any, stage: str) -> Any:
    """artifact 1ステージ分（dict / dataclass のどちらでも読む）。"""
    if isinstance(artifacts, dict):
        value = artifacts.get(stage)
    else:
        value = getattr(artifacts, stage, None)
    return value if value is not None else {}


def _field(container: Any, key: str) -> str:
    """``{"text": ...}`` 形にも素の文字列にも耐える寛容な読み方。

    ``core/landscape/builder.py::_entry_text`` と同型（規則そのものは相互 import
    せず小さく持つ）。
    """
    if isinstance(container, dict):
        value = container.get(key)
    else:
        value = getattr(container, key, None)
    if isinstance(value, dict):
        return _clean(value.get("text"))
    return _clean(value)


def seed_prompt_title(seed: dict) -> str:
    """LLM に渡す起点論文の題名（IK-0709）。

    教材名が ``arXiv-NNNN.NNNNNvN`` のようなファイル名のままなら内容を表さないので、
    ``radar.resolve_seed`` が付けた ``display_title``（写し・arXiv から得た題名）を優先し、
    無ければ ``arxiv_title`` を使う。どちらも無ければ教材名のまま（追加の取得はしない）。
    """
    title = _clean(seed.get("title"))
    if not radar.title_looks_like_filename(title):
        return title
    return _clean(seed.get("display_title")) or _clean(seed.get("arxiv_title")) or title


def build_seed_material(seed: dict, *, artifacts: Any = None) -> dict[str, str]:
    """seed 側の比較素材を組み立てる（タイトル + 要旨 + A層 artifact）。

    ``paper_goal`` は ``paper_skeleton`` artifact、``central_thesis`` /
    ``central_question`` は ``thesis_reconstruction`` artifact が正本
    （``core/landscape/builder.py`` と同じ寛容な読み方）。
    """
    skeleton = _artifact(artifacts, "paper_skeleton")
    thesis = _artifact(artifacts, "thesis_reconstruction")
    central = _field(thesis, "central_thesis")

    material = {
        "title": seed_prompt_title(seed)[:MAX_SEED_FIELD_CHARS],
        "summary": _clean(seed.get("summary"))[:MAX_ABSTRACT_CHARS],
        "paper_goal": _field(skeleton, "paper_goal")[:MAX_SEED_FIELD_CHARS],
        "central_thesis": central[:MAX_SEED_FIELD_CHARS],
        "central_question": (
            _field(thesis, "central_question") or _field(skeleton, "central_question")
        )[:MAX_SEED_FIELD_CHARS],
    }
    return material


def seed_component_labels(seed: dict) -> list[str]:
    """seed の部品ラベル（承認済み theory_components）を閉世界リストとして取り出す。

    供給元は :func:`radar.seed_keyphrase_candidates`（``source="component"``・
    ``MAX_SEED_KEYPHRASES`` 件で既に打ち止め）。ここでは語彙の絞り込みと重複除去だけを
    行い、**新しい問い合わせをしない**（比較は 1 arXiv + 1 LLM コールのまま）。
    リストが空でも比較そのものは従来どおり実行する（重なりに部品参照が付かないだけ）。
    """
    labels: list[str] = []
    seen: set[str] = set()
    for candidate in seed.get("keyphrase_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        if _clean(candidate.get("source")) != COMPONENT_LABEL_SOURCE:
            continue
        text = _clean(candidate.get("text"))
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        labels.append(text)
    return labels


def candidate_analysis_from(
    document_id: str,
    component_rows: list[tuple[str, str]],
    artifacts: Any = None,
) -> dict:
    """取り込み済み候補 1 件の比較素材を組み立てる（純関数・IK-0818）。

    ``component_rows`` は ``(name, review_status)`` の並び。承認済み
    （:data:`radar.APPROVED_REVIEW_STATUSES`）があればそれだけを、無ければ却下以外を
    ``labels_approved=False``（明示ラベル付き）で渡す — 承認前の部品を承認済みに見せない。
    中心命題は ``thesis_reconstruction`` artifact の ``central_thesis``（短文に切る）。
    """
    approved: list[str] = []
    others: list[str] = []
    seen: set[str] = set()
    for name, status in component_rows or []:
        text = _clean(name)
        key = text.casefold()
        if not text or key in seen:
            continue
        status_text = _clean(status)
        if status_text in _EXCLUDED_CANDIDATE_REVIEW_STATUSES:
            continue
        seen.add(key)
        if status_text in radar.APPROVED_REVIEW_STATUSES:
            approved.append(text)
        else:
            others.append(text)
    labels_approved = bool(approved)
    labels = (approved if labels_approved else others)[:MAX_CANDIDATE_COMPONENT_LABELS]
    thesis = _artifact(artifacts, "thesis_reconstruction")
    return {
        "document_id": _clean(document_id),
        "labels": labels,
        "labels_approved": labels_approved if labels else False,
        "central_thesis": _field(thesis, "central_thesis")[:MAX_SEED_FIELD_CHARS],
    }


def has_candidate_analysis(analysis: Optional[dict]) -> bool:
    """解析結果として渡せるもの（部品か中心命題）が 1 つでもあるか。"""
    return bool(analysis) and bool(analysis.get("labels") or analysis.get("central_thesis"))


def load_candidate_analyses(
    session,
    candidates: list[ArxivEntry],
    *,
    visible_document_ids: Any,
    seed_document_id: str = "",
) -> dict[str, dict]:
    """取り込み済み候補（閲覧者に見える document あり）の解析結果を読む（読み取りのみ）。

    取り込み済みの判定は ``search.visible_ingested_documents``（可視性を必須引数で受け、
    空・``None`` は SQL を発行しない = fail-closed）。起点と同じ document は対象外。
    部品は ``theory_components_live`` を 1 クエリで、中心命題は採用 run の artifact を読む。
    失敗はその材料を落とすだけ（比較そのものは要旨で成立させる — fail-soft）。
    """
    from core.paper_discovery import search
    from core.deliberation.refs import document_run_artifacts

    if not candidates or not visible_document_ids:
        return {}
    try:
        ingested = search.visible_ingested_documents(
            session, visible_document_ids=visible_document_ids
        )
    except Exception:  # noqa: BLE001 — 解析結果は補助材料（要旨の比較は止めない）
        logger.info("radar compare: ingested lookup failed", exc_info=True)
        rollback = getattr(session, "rollback", None)
        if callable(rollback):
            rollback()
        return {}
    seed_id = _clean(seed_document_id)
    doc_by_arxiv: dict[str, str] = {}
    for entry in candidates:
        row = ingested.get(entry.arxiv_id) or {}
        doc_id = _clean(row.get("document_id"))
        if doc_id and doc_id != seed_id:
            doc_by_arxiv[entry.arxiv_id] = doc_id
    if not doc_by_arxiv:
        return {}

    rows_by_doc: dict[str, list[tuple[str, str]]] = {}
    try:
        from sqlalchemy import text as sa_text

        rows = session.execute(
            sa_text(
                """
                SELECT document_id::text, name, COALESCE(review_status, '')
                  FROM theory_components_live
                 WHERE document_id::text = ANY(CAST(:refs AS text[]))
                   AND COALESCE(name, '') <> ''
                 ORDER BY name
                """
            ),
            {"refs": sorted(set(doc_by_arxiv.values()))},
        ).fetchall()
        for row in rows:
            rows_by_doc.setdefault(_clean(row[0]), []).append((row[1], row[2]))
    except Exception:  # noqa: BLE001
        logger.info("radar compare: candidate components lookup failed", exc_info=True)
        rollback = getattr(session, "rollback", None)
        if callable(rollback):
            rollback()

    out: dict[str, dict] = {}
    for arxiv_id, doc_id in doc_by_arxiv.items():
        try:
            artifacts = document_run_artifacts(doc_id)
        except Exception:  # noqa: BLE001
            artifacts = {}
        analysis = candidate_analysis_from(doc_id, rows_by_doc.get(doc_id, []), artifacts)
        if has_candidate_analysis(analysis):
            out[arxiv_id] = analysis
    return out


def has_seed_material(material: dict[str, str]) -> bool:
    """比較の素材が1つでもあるか（タイトルだけでは比較にならないので数えない）。"""
    return any(
        material.get(key)
        for key in ("summary", "paper_goal", "central_thesis", "central_question")
    )


def build_prompt(
    material: dict[str, str],
    candidates: list[ArxivEntry],
    component_labels: Optional[list[str]] = None,
    candidate_analyses: Optional[dict[str, dict]] = None,
) -> str:
    """比較分析のプロンプト（制約文はガードレールが原文 grep で固定する — PR4）。

    ``candidate_analyses`` は取り込み済み候補の解析結果（``{arxiv_id: analysis}``・
    IK-0818）。空なら従来と同一のプロンプト（1 バイトも変えない）。

    ``component_labels`` は起点論文の部品リスト（閉世界）。空・未指定なら見出しごと
    省略し、重なりの ``component_label`` は空で返させる（存在しない部品名を作らせない）。
    """
    labels = [label for label in (component_labels or []) if label]
    analyses = {
        key: value
        for key, value in (candidate_analyses or {}).items()
        if has_candidate_analysis(value)
    }
    has_candidate_parts = any(value.get("labels") for value in analyses.values())
    if has_candidate_parts:
        component_rule = "- overlaps の component_label は、起点論文の部品リストか、その候補の部品リストにある名前をそのまま使う（どちらのリストにも無い重なりは component_label を空にする）"
    elif labels:
        component_rule = "- overlaps の component_label は、下の部品リストにある名前をそのまま使う（リストに無い重なりは component_label を空にする）"
    else:
        # 部品リストを付けないときは「下の部品リスト」に言及しない（IK-0709）。
        component_rule = "- この起点論文には承認済みの理論部品がまだ無いので、overlaps は空配列にし、component_label を作らない（部品名を捏造しない）"
    lines: list[str] = [
        "あなたは、ある論文（起点論文）と複数の候補論文を読み比べる研究者の補助をしています。",
        "起点論文と各候補論文について、共通点と違いを1件ずつ短くまとめてください。",
        "",
        "厳守事項:",
        "- アブストラクトに書かれていることだけを比較する",
        "- 断定せず推量形で書く",
        "- 数値スコア・優劣の評価を書かない",
        "- 各違いには、その候補のアブストラクトからの逐語引用を evidence_quote として付ける",
        "  （原文の文字列をそのままコピーする。要約・翻訳・言い換えをしない）",
        "- 違いの観点 aspect は次のいずれか: " + " / ".join(COMPARE_ASPECTS),
        "- 重なり（overlaps）は、起点論文と同じ内容を別の表現・文脈で扱っていそうな箇所だけを挙げる（最大3件）",
        component_rule,
        "- 各重なりにも、その候補のアブストラクトからの逐語引用を evidence_quote として付ける",
    ]
    if analyses:
        lines += [
            "- 取り込み済みの候補には、こちらで解析した中心命題と部品リスト（閉世界）を添えてある。比較にはその範囲も使ってよい",
            "- その場合の evidence_quote は、その候補のアブストラクトか、添えた中心命題の原文から逐語でコピーする",
            "- 「承認前」と付いた部品リストは AI の解析結果で、教員が確認していない",
        ]
    lines += [
        "- relation_kind は次のいずれか: " + " / ".join(RELATION_KINDS)
        + "（shared_subject = 同じ対象 / shared_method = 同じ方法 / adjacent = 隣り合う話題）",
        "- 要旨から読み取れる具体的な共通点が無ければ relation_kind に no_notable_common_ground を選び、common_ground は空文字にする",
        "- common_ground を一般論（『理論と観測を結ぶ』『関係式を導く』『パラメータを制約する』のような、どの論文にも当てはまる言い方）で埋めない",
        "- common_ground を書くときは、要旨に出てくる具体的な対象・手法の名前を1つ以上含める",
        "- 候補ごとに arxiv_id を必ずそのまま返す",
        "",
        # 信頼境界（正本: docs/architecture/trust_boundary_pdf_input.md）: 要旨は
        # arXiv 由来 = 第三者が書いた untrusted 入力。指示として解釈しない旨を明示する。
        UNTRUSTED_SOURCE_NOTICE,
        "",
        "【起点論文】",
        f"タイトル: {material.get('title') or '(不明)'}",
    ]
    for label, key in (
        ("要旨", "summary"),
        ("この論文の目的", "paper_goal"),
        ("中心命題", "central_thesis"),
        ("中心的な問い", "central_question"),
    ):
        value = material.get(key) or ""
        if value:
            lines.append(f"{label}: {value}")

    if labels:
        lines.append("")
        lines.append("【起点論文の部品リスト】")
        for label in labels:
            lines.append(f"- {label}")

    lines.append("")
    lines.append("【候補論文】")
    for entry in candidates:
        lines.append(f"- arxiv_id: {entry.arxiv_id}")
        lines.append(f"  タイトル: {_clean(entry.title)}")
        lines.append(f"  要旨: {_clean(entry.summary)[:MAX_ABSTRACT_CHARS]}")
        analysis = analyses.get(entry.arxiv_id)
        if analysis:
            lines.append("  （取り込み済み — こちらの解析結果）")
            if analysis.get("central_thesis"):
                lines.append(f"  中心命題: {analysis['central_thesis']}")
            if analysis.get("labels"):
                head = (
                    "  部品リスト:"
                    if analysis.get("labels_approved")
                    else "  部品リスト（承認前）:"
                )
                lines.append(head)
                for label in analysis["labels"]:
                    lines.append(f"  - {label}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# structured output
# ---------------------------------------------------------------------------


class _DifferenceOut(BaseModel):
    """違い1件（strict schema のため全フィールド非 nullable + 空既定）。"""

    aspect: str = ""
    statement: str = ""
    evidence_quote: str = ""


class _OverlapOut(BaseModel):
    """重なり1件（strict schema のため全フィールド非 nullable + 空既定）。

    ``component_label`` は空でよい（部品リストに載らない一般的な重なり）。
    """

    component_label: str = ""
    statement: str = ""
    evidence_quote: str = ""


class _ItemOut(BaseModel):
    arxiv_id: str = ""
    relation_kind: str = ""
    common_ground: str = ""
    overlaps: list[_OverlapOut] = Field(default_factory=list)
    differences: list[_DifferenceOut] = Field(default_factory=list)


class _CompareOutput(BaseModel):
    items: list[_ItemOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 検証（非LLM・捏造ガード）
# ---------------------------------------------------------------------------


def _concrete_terms(text: str) -> list[str]:
    """共通点の文から「具体的な語」の候補を取り出す（casefold・汎用語は除く）。"""
    terms: list[str] = []
    for match in _TERM_RE.findall(text or ""):
        term = match.strip("-/+^_").casefold()
        if len(term) < 2 or term in _GENERIC_TERMS:
            continue
        terms.append(term)
    return terms


def common_ground_is_grounded(
    common_ground: str,
    candidate_summary: str,
    seed_texts: Optional[list[str]] = None,
    component_labels: Optional[list[str]] = None,
) -> bool:
    """共通点が候補要旨か seed 素材の語を 1 つ以上含むか（soft 検査・部分一致・casefold）。

    ①供給した部品ラベルが共通点に現れる ②共通点の具体的な語（:data:`_TERM_RE`）が
    候補要旨か seed 素材に現れる、のどちらかで合格。一般論は日本語の汎用語だけで
    書かれ、①②のどちらにも当たらないので落ちる（設計書 §18）。
    """
    text = _clean(common_ground).casefold()
    if not text:
        return False
    for label in component_labels or []:
        key = _clean(label).casefold()
        if key and key in text:
            return True
    haystack = " ".join(
        [_clean(candidate_summary)] + [_clean(t) for t in (seed_texts or [])]
    ).casefold()
    return any(term in haystack for term in _concrete_terms(common_ground))


def _normalize_relation(
    raw_kind: str,
    common_ground: str,
    *,
    candidate_summary: str,
    seed_texts: Optional[list[str]],
    component_labels: Optional[list[str]],
) -> tuple[str, str]:
    """``(relation_kind, common_ground)`` を正規化する（純粋関数・設計書 §18.2）。

    - 語彙外の relation_kind は ``adjacent``（分類できなかった関係として残す）。
    - ``no_notable_common_ground`` なら共通点は空文字。
    - それ以外で共通点が空、または soft 検査に落ちたら、共通点を落として ``adjacent`` へ
      降格（item そのものは保持 = P4）。
    """
    kind = _clean(raw_kind)
    if kind not in RELATION_KINDS:
        kind = RELATION_ADJACENT
    ground = _clean(common_ground)
    if kind == RELATION_NONE:
        return kind, ""
    if not ground:
        return RELATION_ADJACENT, ""
    if not common_ground_is_grounded(
        ground, candidate_summary, seed_texts, component_labels
    ):
        return RELATION_ADJACENT, ""
    return kind, ground


def _annotate_overlap_source(
    row: dict,
    *,
    in_seed: bool,
    in_candidate: bool,
    candidate_labels_approved: bool,
    evidence_source: str,
) -> None:
    """取り込み済み候補の重なりに ``side`` / ``evidence_source`` / ``source_note`` を足す。

    ``side`` は部品名が載っていたリスト（起点 / 候補 / 両方）。部品名が空なら ``side`` は
    付けない（どちらのリストにも紐づいていない — 申告をそのまま出さない）。
    ``source_note`` はサーバ側固定文の連結（UI は素通し）。
    """
    notes: list[str] = []
    if row.get("component_label"):
        if in_seed and in_candidate:
            row["side"] = OVERLAP_SIDE_BOTH
        elif in_candidate:
            row["side"] = OVERLAP_SIDE_CANDIDATE
        else:
            row["side"] = OVERLAP_SIDE_SEED
        if row["side"] == OVERLAP_SIDE_CANDIDATE:
            notes.append(NOTE_SOURCE_CANDIDATE_PART)
        elif row["side"] == OVERLAP_SIDE_BOTH:
            notes.append(NOTE_SOURCE_BOTH_PARTS)
        if row["side"] != OVERLAP_SIDE_SEED and not candidate_labels_approved:
            notes.append(NOTE_SOURCE_UNAPPROVED_PART)
    row["evidence_source"] = evidence_source
    if evidence_source == EVIDENCE_SOURCE_THESIS:
        notes.append(NOTE_SOURCE_THESIS_QUOTE)
    if notes:
        row["source_note"] = "・".join(notes)


def validate_items(
    parsed: _CompareOutput,
    candidates: list[ArxivEntry],
    component_labels: Optional[list[str]] = None,
    seed_texts: Optional[list[str]] = None,
    candidate_analyses: Optional[dict[str, dict]] = None,
) -> tuple[list[dict], list[str]]:
    """LLM 出力を検証し ``(items, notes)`` を返す（純粋関数）。

    - ``evidence_quote`` が**当該候補の要旨の逐語部分文字列**でない違い・重なりは drop
      （候補ごと全滅しても ``common_ground`` は残す — 情報を落とさない）。
    - ``aspect`` の語彙外は :data:`ASPECT_UNKNOWN` へ落とす（drop しない）。
    - 重なりの ``component_label`` が供給リスト外なら**空文字へ落として statement は残す**
      （存在しない部品名は出さないが、重なりの指摘そのものは捨てない — P4）。
    - 候補の並び順は**リクエストの順**（LLM の返却順に依存させない）。
    - ``relation_kind`` / ``common_ground`` は :func:`_normalize_relation` で正規化する
      （一般論の共通点は落として ``adjacent`` へ降格 — 設計書 §18）。
    - 取り込み済み候補（``candidate_analyses`` にある候補・IK-0818）では、引用は要旨
      **または**添えた中心命題の逐語で可（``evidence_source``）。重なりの部品名は起点
      リスト ∪ その候補のリストで照合し、どちらに載っていたかを ``side`` に残す
      （LLM の申告ではなくサーバが導出する）。
    """
    by_id = {entry.arxiv_id: entry for entry in candidates}
    haystacks = {
        entry.arxiv_id: normalize_for_quote_match(entry.summary) for entry in candidates
    }
    # 部品名の照合は大小文字を無視し、**供給した表記**をそのまま返す（表記ゆれを作らない）。
    seed_label_map = {
        label.casefold(): label for label in (component_labels or []) if label
    }
    analyses = candidate_analyses or {}

    parsed_by_id: dict[str, _ItemOut] = {}
    for entry in parsed.items or []:
        arxiv_id = normalize_arxiv_id(entry.arxiv_id)
        if arxiv_id and arxiv_id in by_id and arxiv_id not in parsed_by_id:
            parsed_by_id[arxiv_id] = entry

    items: list[dict] = []
    notes: list[str] = []
    dropped = 0
    for candidate in candidates:
        entry = parsed_by_id.get(candidate.arxiv_id)
        if entry is None:
            continue

        haystack = haystacks.get(candidate.arxiv_id) or ""
        analysis = analyses.get(candidate.arxiv_id) or {}
        thesis_haystack = normalize_for_quote_match(analysis.get("central_thesis"))
        cand_label_map = {
            label.casefold(): label for label in (analysis.get("labels") or []) if label
        }
        # 起点側の表記を優先する（同じ名前なら起点リストの綴りで返す）。
        label_by_key = {**cand_label_map, **seed_label_map}
        overlaps: list[dict] = []
        for overlap in entry.overlaps or []:
            statement = _clean(overlap.statement)
            quote = str(overlap.evidence_quote or "").strip()
            if not statement or not quote:
                dropped += 1
                continue
            evidence_source = EVIDENCE_SOURCE_ABSTRACT
            if not haystack or normalize_for_quote_match(quote) not in haystack:
                if thesis_haystack and normalize_for_quote_match(quote) in thesis_haystack:
                    evidence_source = EVIDENCE_SOURCE_THESIS
                else:
                    dropped += 1
                    continue
            label = _clean(overlap.component_label)
            row = {
                # リスト外の名前は空へ（statement は保持する）。
                "component_label": label_by_key.get(label.casefold(), ""),
                "statement": statement,
                "evidence_quote": quote,
            }
            if analysis:
                _annotate_overlap_source(
                    row,
                    in_seed=label.casefold() in seed_label_map,
                    in_candidate=label.casefold() in cand_label_map,
                    candidate_labels_approved=bool(analysis.get("labels_approved")),
                    evidence_source=evidence_source,
                )
            overlaps.append(row)

        differences: list[dict] = []
        for difference in entry.differences or []:
            statement = _clean(difference.statement)
            quote = str(difference.evidence_quote or "").strip()
            if not statement or not quote:
                dropped += 1
                continue
            evidence_source = EVIDENCE_SOURCE_ABSTRACT
            if not haystack or normalize_for_quote_match(quote) not in haystack:
                if thesis_haystack and normalize_for_quote_match(quote) in thesis_haystack:
                    evidence_source = EVIDENCE_SOURCE_THESIS
                else:
                    dropped += 1
                    continue
            aspect = _clean(difference.aspect)
            diff_row = {
                "aspect": aspect if aspect in COMPARE_ASPECTS else ASPECT_UNKNOWN,
                "statement": statement,
                "evidence_quote": quote,
            }
            if analysis:
                diff_row["evidence_source"] = evidence_source
                if evidence_source == EVIDENCE_SOURCE_THESIS:
                    diff_row["source_note"] = NOTE_SOURCE_THESIS_QUOTE
            differences.append(diff_row)

        relation_kind, common_ground = _normalize_relation(
            entry.relation_kind,
            entry.common_ground,
            candidate_summary=" ".join(
                [candidate.summary or "", _clean(analysis.get("central_thesis"))]
            ).strip(),
            seed_texts=seed_texts,
            component_labels=list(component_labels or [])
            + list(analysis.get("labels") or []),
        )
        item = {
            "arxiv_id": candidate.arxiv_id,
            "title": _clean(candidate.title),
            "relation_kind": relation_kind,
            "relation_label": RELATION_LABELS[relation_kind],
            "common_ground": common_ground,
            "overlaps": overlaps,
            "differences": differences,
            "caveat": CAVEAT,
        }
        if analysis:
            item["candidate_analysis_used"] = True
            item["analysis_note"] = NOTE_CANDIDATE_ANALYSIS_USED
        items.append(item)

    if dropped:
        notes.append(NOTE_QUOTE_NOT_VERBATIM)
    if len(items) < len(candidates):
        notes.append(NOTE_ITEM_MISSING)
    return items, notes


# ---------------------------------------------------------------------------
# 実行本体
# ---------------------------------------------------------------------------


def resolve_model() -> str:
    """``DISCOVERY_COMPARE_LLM_MODEL`` があればそれを、無ければ fast tier のモデル。"""
    from core.llm_worker.client import resolve_model as _resolve_model_key

    return _resolve_model_key(_MODEL_SETTING_KEY, fallback="fast")


def _call_llm(content: str, model: str) -> _CompareOutput:
    """1リクエスト = 1コール（``teaching_figures/suggest.py`` と同じ同期・単発型）。"""
    from core.llm import generate_text_with_structured_output

    return generate_text_with_structured_output(
        [{"role": "user", "content": content}], _CompareOutput, model=model
    )


def run_compare(
    session,
    document_id: str,
    arxiv_ids: list[str],
    *,
    user_id: str = "",
    model: Optional[str] = None,
    visible_document_ids: Any = None,
) -> dict:
    """起点論文と候補論文の比較分析を実行する（arXiv 0〜1 コール + 1 LLM コール）。

    要旨は**保存済みの写し（migration 085）を先に読み**、足りない分だけを arXiv から
    取る（設計書 §14 — 直前に検索していれば arXiv コールは 0 回）。写しはライブ取得と
    同一の本文なので、``evidence_quote`` の verbatim 検査の土台は変わらない。

    Args:
        session: SQLAlchemy セッション（seed の解決に使う。commit しない）。
        document_id: 起点となる教材（``documents.id`` / ``source_path``）。
        arxiv_ids: 比較対象の arXiv ID（:data:`RADAR_COMPARE_MAX_CANDIDATES` 件まで。
            件数の検査は route 層が行う）。
        user_id: U層計測の帰属。
        model: 明示指定（省略時は M層の解決）。
        visible_document_ids: 閲覧者に見える document.id の集合（route が注入する）。
            取り込み済み候補の解析結果（部品・中心命題）を比較の材料に足すのに使う
            （IK-0818）。``None`` / 空なら従来どおり要旨だけで比較する（fail-closed）。

    Returns:
        ``{"items": [{arxiv_id, title, relation_kind, relation_label, common_ground,
        overlaps, differences, caveat}],
        "skipped": [{arxiv_id, detail}], "notes": [事実文, ...]}``。
        ``overlaps`` は ``{component_label, statement, evidence_quote}`` の並び
        （``component_label`` は空文字あり得る）。``common_ground`` は後方互換のため維持する。
        取り込み済み候補の項目には additive に ``candidate_analysis_used`` /
        ``analysis_note``、重なりに ``side``（seed / candidate / both・部品名があるときだけ）/
        ``evidence_source``（abstract / thesis）/ ``source_note`` が付く（IK-0818）。

    Raises:
        LookupError: document が存在しない。
        NoSeedMaterialError: seed 側の素材ゼロ（LLM を呼ばない）。
        CompareUnavailableError: 候補メタデータが1件も引けない / LLM 失敗。
        arxiv_client.ArxivApiError: arXiv API への到達・応答・パースの失敗。
    """
    from core.deliberation.refs import document_run_artifacts
    from core.llm_usage import usage_context

    requested: list[str] = []
    invalid: list[dict] = []
    for raw in arxiv_ids or []:
        normalized = normalize_arxiv_id(raw)
        if not normalized:
            invalid.append({"arxiv_id": str(raw or ""), "detail": DETAIL_METADATA_UNAVAILABLE})
            continue
        if normalized not in requested:
            requested.append(normalized)
    requested = requested[:RADAR_COMPARE_MAX_CANDIDATES]

    # seed の arXiv 取得はここではしない（候補と同じ id_list に相乗りさせる — PR6）。
    seed = radar.resolve_seed(session, document_id, fetch_arxiv=False)
    seed_arxiv_id = seed.get("arxiv_id") or ""

    fetch_ids = ([seed_arxiv_id] if seed_arxiv_id else []) + [
        i for i in requested if i != seed_arxiv_id
    ]
    # 保存済みの写し（migration 085 / 設計書 §14）を先に読み、足りない分だけを
    # arXiv に取りに行く。レーダーで検索した直後の比較なら、候補の要旨は既に
    # 写しとして在るので arXiv は 0 回で済む。写しはライブ取得と同一の本文なので、
    # evidence_quote の verbatim 検査の土台（サーバが持つ要旨）も変わらない。
    by_id: dict[str, ArxivEntry] = (
        metadata_cache.read_fresh(session, fetch_ids) if fetch_ids else {}
    )
    missing = [i for i in fetch_ids if i not in by_id]
    entries = arxiv_client.fetch_by_ids(missing) if missing else []
    if entries:
        metadata_cache.remember(session, entries)
    by_id.update({entry.arxiv_id: entry for entry in entries})

    if seed_arxiv_id and seed_arxiv_id in by_id:
        seed["summary"] = by_id[seed_arxiv_id].summary or seed.get("summary") or ""

    candidates: list[ArxivEntry] = []
    skipped: list[dict] = list(invalid)
    for arxiv_id in requested:
        entry = by_id.get(arxiv_id)
        if entry is None or not _clean(entry.summary):
            skipped.append({"arxiv_id": arxiv_id, "detail": DETAIL_METADATA_UNAVAILABLE})
            continue
        candidates.append(entry)

    if not candidates:
        raise CompareUnavailableError(DETAIL_METADATA_UNAVAILABLE)

    material = build_seed_material(
        seed, artifacts=document_run_artifacts(seed["document_id"])
    )
    if not has_seed_material(material):
        # 素材ゼロで比較文を作らせない（根拠の無い比較を創作しない）。
        raise NoSeedMaterialError(
            "この教材には、比較に使える要旨・解析結果がありません。"
        )

    # 部品リストは seed 解決の結果に既に入っている（追加クエリ・追加コールなし）。
    component_labels = seed_component_labels(seed)
    # 取り込み済み候補は解析結果も材料にする（読み取りのみ・追加 LLM コールなし — IK-0818）。
    candidate_analyses = load_candidate_analyses(
        session,
        candidates,
        visible_document_ids=visible_document_ids,
        seed_document_id=seed["document_id"],
    )
    content = build_prompt(material, candidates, component_labels, candidate_analyses)
    resolved_model = model or resolve_model()

    with usage_context(
        FEATURE_RADAR_COMPARE,
        user_id=user_id or None,
        document_id=seed["document_id"] or None,
    ):
        try:
            parsed = _call_llm(content, resolved_model)
        except Exception as exc:  # noqa: BLE001 — 単発の明示操作なので縮退させない
            logger.warning(
                "radar compare: LLM call failed document=%s", seed["document_id"],
                exc_info=True,
            )
            raise CompareUnavailableError("比較分析を実行できませんでした。") from exc

    items, notes = validate_items(
        parsed,
        candidates,
        component_labels,
        seed_texts=[value for value in material.values() if value],
        candidate_analyses=candidate_analyses,
    )
    return {"items": items, "skipped": skipped, "notes": notes}


__all__ = [
    "ASPECT_UNKNOWN",
    "CAVEAT",
    "COMPARE_ASPECTS",
    "COMPONENT_LABEL_SOURCE",
    "CompareError",
    "CompareUnavailableError",
    "FEATURE_RADAR_COMPARE",
    "NoSeedMaterialError",
    "RELATION_KINDS",
    "RELATION_LABELS",
    "RELATION_NONE",
    "RADAR_COMPARE_MAX_CANDIDATES",
    "build_prompt",
    "build_seed_material",
    "candidate_analysis_from",
    "has_candidate_analysis",
    "load_candidate_analyses",
    "common_ground_is_grounded",
    "has_seed_material",
    "normalize_for_quote_match",
    "resolve_model",
    "run_compare",
    "seed_component_labels",
    "seed_prompt_title",
    "validate_items",
]
