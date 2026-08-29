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
- **PR6 候補の素材はサーバが取り直す**: 要旨はクライアントから受け取らず
  :func:`arxiv_client.fetch_by_ids` で取得する（verbatim 検査の土台を本物にする）。
  seed の要旨も同じ1コールに相乗りさせる（arXiv への呼び出しは1回）。
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

from core.paper_discovery import arxiv_client, radar
from core.paper_discovery.schema import ArxivEntry, normalize_arxiv_id

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

#: プロンプトへ載せる本文の上限（膨張の防波堤）。
MAX_ABSTRACT_CHARS = 4000
MAX_SEED_FIELD_CHARS = 1200

#: 重なりに紐づけられる部品ラベルの供給元（``radar.seed_keyphrase_candidates`` の語彙。
#: 承認済み theory_components だけが ``component`` で来る — 語彙を増やさない）。
COMPONENT_LABEL_SOURCE = "component"

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
        "title": _clean(seed.get("title"))[:MAX_SEED_FIELD_CHARS],
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
) -> str:
    """比較分析のプロンプト（制約文はガードレールが原文 grep で固定する — PR4）。

    ``component_labels`` は起点論文の部品リスト（閉世界）。空・未指定なら見出しごと
    省略し、重なりの ``component_label`` は空で返させる（存在しない部品名を作らせない）。
    """
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
        "- overlaps の component_label は、下の部品リストにある名前をそのまま使う（リストに無い重なりは component_label を空にする）",
        "- 各重なりにも、その候補のアブストラクトからの逐語引用を evidence_quote として付ける",
        "- 候補ごとに arxiv_id を必ずそのまま返す",
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

    labels = [label for label in (component_labels or []) if label]
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
    common_ground: str = ""
    overlaps: list[_OverlapOut] = Field(default_factory=list)
    differences: list[_DifferenceOut] = Field(default_factory=list)


class _CompareOutput(BaseModel):
    items: list[_ItemOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 検証（非LLM・捏造ガード）
# ---------------------------------------------------------------------------


def validate_items(
    parsed: _CompareOutput,
    candidates: list[ArxivEntry],
    component_labels: Optional[list[str]] = None,
) -> tuple[list[dict], list[str]]:
    """LLM 出力を検証し ``(items, notes)`` を返す（純粋関数）。

    - ``evidence_quote`` が**当該候補の要旨の逐語部分文字列**でない違い・重なりは drop
      （候補ごと全滅しても ``common_ground`` は残す — 情報を落とさない）。
    - ``aspect`` の語彙外は :data:`ASPECT_UNKNOWN` へ落とす（drop しない）。
    - 重なりの ``component_label`` が供給リスト外なら**空文字へ落として statement は残す**
      （存在しない部品名は出さないが、重なりの指摘そのものは捨てない — P4）。
    - 候補の並び順は**リクエストの順**（LLM の返却順に依存させない）。
    """
    by_id = {entry.arxiv_id: entry for entry in candidates}
    haystacks = {
        entry.arxiv_id: normalize_for_quote_match(entry.summary) for entry in candidates
    }
    # 部品名の照合は大小文字を無視し、**供給した表記**をそのまま返す（表記ゆれを作らない）。
    label_by_key = {
        label.casefold(): label for label in (component_labels or []) if label
    }

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
        overlaps: list[dict] = []
        for overlap in entry.overlaps or []:
            statement = _clean(overlap.statement)
            quote = str(overlap.evidence_quote or "").strip()
            if not statement or not quote:
                dropped += 1
                continue
            if not haystack or normalize_for_quote_match(quote) not in haystack:
                dropped += 1
                continue
            label = _clean(overlap.component_label)
            overlaps.append(
                {
                    # リスト外の名前は空へ（statement は保持する）。
                    "component_label": label_by_key.get(label.casefold(), ""),
                    "statement": statement,
                    "evidence_quote": quote,
                }
            )

        differences: list[dict] = []
        for difference in entry.differences or []:
            statement = _clean(difference.statement)
            quote = str(difference.evidence_quote or "").strip()
            if not statement or not quote:
                dropped += 1
                continue
            if not haystack or normalize_for_quote_match(quote) not in haystack:
                dropped += 1
                continue
            aspect = _clean(difference.aspect)
            differences.append(
                {
                    "aspect": aspect if aspect in COMPARE_ASPECTS else ASPECT_UNKNOWN,
                    "statement": statement,
                    "evidence_quote": quote,
                }
            )

        items.append(
            {
                "arxiv_id": candidate.arxiv_id,
                "title": _clean(candidate.title),
                "common_ground": _clean(entry.common_ground),
                "overlaps": overlaps,
                "differences": differences,
                "caveat": CAVEAT,
            }
        )

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
) -> dict:
    """起点論文と候補論文の比較分析を実行する（1 arXiv コール + 1 LLM コール）。

    Args:
        session: SQLAlchemy セッション（seed の解決に使う。commit しない）。
        document_id: 起点となる教材（``documents.id`` / ``source_path``）。
        arxiv_ids: 比較対象の arXiv ID（:data:`RADAR_COMPARE_MAX_CANDIDATES` 件まで。
            件数の検査は route 層が行う）。
        user_id: U層計測の帰属。
        model: 明示指定（省略時は M層の解決）。

    Returns:
        ``{"items": [{arxiv_id, title, common_ground, overlaps, differences, caveat}],
        "skipped": [{arxiv_id, detail}], "notes": [事実文, ...]}``。
        ``overlaps`` は ``{component_label, statement, evidence_quote}`` の並び
        （``component_label`` は空文字あり得る）。``common_ground`` は後方互換のため維持する。

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
    entries = arxiv_client.fetch_by_ids(fetch_ids) if fetch_ids else []
    by_id = {entry.arxiv_id: entry for entry in entries}

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
    content = build_prompt(material, candidates, component_labels)
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

    items, notes = validate_items(parsed, candidates, component_labels)
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
    "RADAR_COMPARE_MAX_CANDIDATES",
    "build_prompt",
    "build_seed_material",
    "has_seed_material",
    "normalize_for_quote_match",
    "resolve_model",
    "run_compare",
    "seed_component_labels",
    "validate_items",
]
