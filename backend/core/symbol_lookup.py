"""記号の「直前の定義」（概念レジストリ P3-5 / ``concept_registry_design.md`` §7）。

学習者が教材の数式の中の記号（``F``・``\\rho``・``V_{cb}`` など）をタップしたときに、
**その位置より前で最も近い定義**を論文の逐語で返す読み手。ScholarPhi の
「直前の定義を出す」規則を、Phase 1 で行になった ``knowledge_symbols``（記号）・
``knowledge_equations``（式）・``knowledge_evidence``（本文の根拠箇所）から
**決定論的に**導出する。

不変条項（KR1〜KR10 のうち本モジュールに掛かるもの）:

- **KR1 A層非改変**: ``SymbolRecord`` に ``concept_ref`` を足すのではなく、
  確定済み同一性リンク（``element_identity_links``）を**読み時に join** する。
- **KR5 決定論・非LLM**: ``core.llm`` を import しない。embedding も呼ばない。
  記号の一致は :func:`core.concept_normalizer.normalize_key` の**完全一致**のみ
  （部分一致は P0-2 の F-7（``SM`` が ``cosmological`` に当たる）と同じ事故を招く）。
- **KR6 / KO10 数値を見せない**: ``confidence`` / ``stable_key`` /
  ``produced_by_run_id`` / 内部 ID（``sym_…`` / ``eq_op_*`` / ``ev_*``）を DTO に
  載せない。載せるのは ``unit`` / ``scope_label`` / 出所（論文タイトル）。
- **KR8 閉世界の正直さ**: 「この記号はどこにも定義が無い」とは言わない。言えるのは
  「**この論文には**定義の記述が見つかりませんでした」だけ。順序が解けないときも
  黙って先頭を出さず「位置を特定できないため、最初の定義を表示しています」と書く。
- **KR10 権限 fail-closed**: ``document_ids``（= 受講コースの sources）を
  ``document_id = ANY(CAST(:doc_ids AS uuid[]))`` で **SQL の中で**強制する。
  空集合なら SQL を発行せずに「記号が見つからない」へ落とす。

本モジュールは FastAPI を import しない（開発ルール2 / core/ 共通ルール）。
セッションは呼び出し側（route）が渡す（テストは fake session で行う）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text as sa_text

from core import element_vocab
from core.concept_normalizer import normalize_key
from core.learner_context_common import is_uuid
from core.text_hygiene import strip_control_sequences

logger = logging.getLogger(__name__)

__all__ = [
    "FACT_DEFINED_LATER",
    "FACT_NO_DEFINITION",
    "FACT_POSITION_UNKNOWN",
    "lookup_symbol_definition",
]

# ---------------------------------------------------------------------------
# 事実文（固定文・言い換えない。閉世界の主語は常に「この論文」）
# ---------------------------------------------------------------------------

#: タップ位置より後ろにしか定義が無かったとき。
FACT_DEFINED_LATER = "この位置より後で定義されています。"

#: 定義の記述そのものが見つからないとき（分野レベルの不在は言わない・KR8）。
FACT_NO_DEFINITION = "この論文には定義の記述が見つかりませんでした。"

#: タップ位置の順序が解けなかったとき（黙って先頭を出さない）。
FACT_POSITION_UNKNOWN = "位置を特定できないため、最初の定義を表示しています。"

#: 定義の逐語の上限（読み手が1画面で読める長さ。数値は表示しない）。
_MAX_DEFINITION_CHARS = 400

#: 1回の照会で走査する記号行の上限（同名記号が論文横断で多数ある場合の保険）。
_MAX_SYMBOL_ROWS = 40


# ---------------------------------------------------------------------------
# 位置（document order）の決定論的な解決
# ---------------------------------------------------------------------------
#
# 比較可能な順序は2つの空間しか無い:
#   空間 0: ``chunks.block_ids`` から作った block_id → 連番（配信された本文の順）
#   空間 1: ``page``（block_id が引けないときの粗い代替）
# **異なる空間どうしは比較しない**（混ぜると「前／後」が嘘になる）。どちらでも
# 解けなければ順序なし（``None``）として「位置を特定できない」側へ倒す。


def _order_key(
    block_id: str, page: Any, block_order: dict[str, int]
) -> Optional[tuple[int, int]]:
    """``(空間, 位置)`` を返す。解けなければ ``None``。"""
    key = str(block_id or "").strip()
    if key and key in block_order:
        return (0, block_order[key])
    try:
        if page is not None:
            return (1, int(page))
    except (TypeError, ValueError):
        return None
    return None


def _comparable(a: Optional[tuple[int, int]], b: Optional[tuple[int, int]]) -> bool:
    """2つの位置が同じ空間にあり比較してよいか。"""
    return a is not None and b is not None and a[0] == b[0]


def _load_block_order(session: Any, document_ids: list[str]) -> dict[str, int]:
    """``chunks.block_ids`` から ``block_id -> 連番`` を作る（配信順＝論文順）。

    chunk は ``chunk_index`` 昇順、chunk 内の block_id は配列の順を保つ。同じ
    block_id が複数の chunk に現れたら**最初の出現**を採る（定義は最初に現れた
    場所を指すのが自然で、かつ決定論になる）。
    """
    if not document_ids:
        return {}
    try:
        rows = (
            session.execute(
                sa_text(
                    """
                    SELECT chunk_index, block_ids
                      FROM chunks
                     WHERE document_id = ANY(CAST(:doc_ids AS uuid[]))
                     ORDER BY document_id, chunk_index
                    """
                ),
                {"doc_ids": document_ids},
            )
            .mappings()
            .fetchall()
        )
    except Exception:  # noqa: BLE001 — 順序が引けないだけで照会は成立させる
        logger.warning("symbol_lookup: block order unavailable", exc_info=True)
        return {}

    order: dict[str, int] = {}
    counter = 0
    for row in rows or []:
        for block_id in _as_list(row.get("block_ids")):
            key = str(block_id or "").strip()
            counter += 1
            if key and key not in order:
                order[key] = counter
    return order


# ---------------------------------------------------------------------------
# 小さな値ユーティリティ
# ---------------------------------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _clean(value: Any, *, limit: int = 0) -> str:
    """表示前の衛生（制御文字除去 + 空白の正規化 + 任意の長さ制限）。"""
    text = strip_control_sequences(str(value or "")).strip()
    if limit and len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def _uuid_only(document_ids: Any) -> list[str]:
    """``uuid[]`` へキャストできる ID だけを残す（material_id 形を混ぜない）。"""
    out: list[str] = []
    for value in document_ids or []:
        candidate = str(value or "").strip()
        if candidate and is_uuid(candidate) and candidate not in out:
            out.append(candidate)
    return sorted(out)


def _symbol_matches(row: dict, wanted: str) -> bool:
    """``canonical_symbol`` / ``notation_variants`` の**完全一致**（正規化後）。"""
    if normalize_key(row.get("canonical_symbol")) == wanted:
        return True
    return any(normalize_key(v) == wanted for v in _as_list(row.get("notation_variants")))


# ---------------------------------------------------------------------------
# DB 読み（すべて document スコープを SQL で強制する）
# ---------------------------------------------------------------------------


def _load_symbol_rows(session: Any, document_ids: list[str]) -> list[dict]:
    rows = (
        session.execute(
            sa_text(
                """
                SELECT document_id::text AS document_id,
                       agent_symbol_id,
                       canonical_symbol,
                       notation_variants,
                       kind,
                       unit,
                       scope,
                       definition_status,
                       defining_equation_ids,
                       source_evidence_ids,
                       definition_evidence_texts
                  FROM knowledge_symbols_live
                 WHERE document_id = ANY(CAST(:doc_ids AS uuid[]))
                 ORDER BY document_id, agent_symbol_id
                """
            ),
            {"doc_ids": document_ids},
        )
        .mappings()
        .fetchall()
    )
    return [dict(r) for r in (rows or [])]


def _load_equation_positions(session: Any, document_ids: list[str]) -> dict[tuple[str, str], dict]:
    """``(document_id, agent_equation_id) -> {block_id, page, label}``。"""
    rows = (
        session.execute(
            sa_text(
                """
                SELECT document_id::text AS document_id, agent_equation_id,
                       block_id, page, label
                  FROM knowledge_equations
                 WHERE document_id = ANY(CAST(:doc_ids AS uuid[]))
                   AND superseded_at IS NULL
                """
            ),
            {"doc_ids": document_ids},
        )
        .mappings()
        .fetchall()
    )
    index: dict[tuple[str, str], dict] = {}
    for row in rows or []:
        agent_id = str(row.get("agent_equation_id") or "").strip()
        if agent_id:
            index[(str(row.get("document_id") or ""), agent_id)] = dict(row)
    return index


def _load_evidence_positions(session: Any, document_ids: list[str]) -> dict[tuple[str, str], dict]:
    """``(document_id, agent_evidence_id) -> {block_id, page, evidence_text}``。"""
    rows = (
        session.execute(
            sa_text(
                """
                SELECT document_id::text AS document_id, agent_evidence_id,
                       block_id, page, evidence_text
                  FROM knowledge_evidence
                 WHERE document_id = ANY(CAST(:doc_ids AS uuid[]))
                   AND superseded_at IS NULL
                """
            ),
            {"doc_ids": document_ids},
        )
        .mappings()
        .fetchall()
    )
    index: dict[tuple[str, str], dict] = {}
    for row in rows or []:
        agent_id = str(row.get("agent_evidence_id") or "").strip()
        if agent_id:
            index[(str(row.get("document_id") or ""), agent_id)] = dict(row)
    return index


def _load_document_titles(session: Any, document_ids: list[str]) -> dict[str, str]:
    rows = (
        session.execute(
            sa_text(
                """
                SELECT id::text AS id, title
                  FROM documents
                 WHERE id = ANY(CAST(:doc_ids AS uuid[]))
                """
            ),
            {"doc_ids": document_ids},
        )
        .mappings()
        .fetchall()
    )
    return {str(r.get("id") or ""): _clean(r.get("title")) for r in (rows or [])}


def _load_concept_ref(session: Any, *, document_id: str, agent_symbol_id: str) -> Optional[dict]:
    """確定済みの同一性リンク経由でレジストリの概念を 1 件返す（KR2: candidate は出さない）。

    ``element_identity_links`` の ``instance_element_type='symbol'`` は migration 082 で
    追加される（``concept_registry_design.md`` §4.7）。リンクが ``confirmed``、かつ
    エントリが ``active``（教員が公開を止めていない）**かつ** review_status が
    confirmed（教員が概念として確定した行）のときだけ返す。後者が要るのは、同一性候補の
    自動生成（§6.2）が未確定のエントリ行を作るようになったため — AI が立てた候補の
    名前を学習者に「概念」として見せない（KR2）。
    """
    if not agent_symbol_id:
        return None
    try:
        row = (
            session.execute(
                sa_text(
                    """
                    SELECT e.id::text AS entry_id, e.name AS name, e.entry_type AS entry_type
                      FROM element_identity_links l
                      JOIN library_entries e ON e.id = l.shared_part_id
                     WHERE l.instance_element_type = 'symbol'
                       AND l.status = 'confirmed'
                       AND l.instance_element_id = :symbol_id
                       AND l.instance_document_id = :document_id
                       AND e.status = 'active'
                       AND e.review_status = 'confirmed'
                     ORDER BY l.decided_at DESC NULLS LAST, e.name
                     LIMIT 1
                    """
                ),
                {"symbol_id": agent_symbol_id, "document_id": document_id},
            )
            .mappings()
            .fetchone()
        )
    except Exception:  # noqa: BLE001 — レジストリが無くても記号の定義は返す（fail-soft）
        logger.warning("symbol_lookup: concept_ref unavailable", exc_info=True)
        return None
    if not row:
        return None
    name = _clean(row.get("name"))
    if not name:
        return None
    ref: dict[str, Any] = {
        "entry_id": str(row.get("entry_id") or ""),
        "name": name,
        "entry_type": str(row.get("entry_type") or ""),
    }
    label = _entry_type_label(ref["entry_type"])
    if label:
        ref["entry_type_label"] = label
    return ref


def _entry_type_label(entry_type: str) -> str:
    """レジストリ種別の日本語ラベル（表が無ければ空 — 新しい訳語表を作らない）。"""
    try:  # pragma: no cover - 表の有無は担当 A の実装タイミングに依存する
        from core.library import schema as library_schema

        table = getattr(library_schema, "ENTRY_TYPE_LABELS", None)
        if isinstance(table, dict):
            return str(table.get(entry_type) or "")
    except Exception:  # noqa: BLE001
        return ""
    return ""


# ---------------------------------------------------------------------------
# 定義候補の組み立て
# ---------------------------------------------------------------------------


def _definition_candidates(
    symbol_row: dict,
    *,
    equations: dict[tuple[str, str], dict],
    evidence: dict[tuple[str, str], dict],
    block_order: dict[str, int],
) -> list[dict]:
    """1つの記号行から、位置つきの定義候補を作る（決定論・重複なし）。

    逐語は ``definition_evidence_texts`` を正本とし、evidence 行の
    ``evidence_text`` を補助に使う（``definition_evidence_texts`` は agent が
    抜き出した「定義の文」で、evidence 行は本文の根拠箇所）。
    """
    document_id = str(symbol_row.get("document_id") or "")
    texts = [_clean(t, limit=_MAX_DEFINITION_CHARS) for t in _as_list(symbol_row.get("definition_evidence_texts"))]
    texts = [t for t in texts if t]

    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(source: str, ref_id: str, row: dict | None, fallback_text: str) -> None:
        key = (source, ref_id)
        if key in seen:
            return
        seen.add(key)
        row = row or {}
        text = _clean(row.get("evidence_text"), limit=_MAX_DEFINITION_CHARS) or fallback_text
        candidates.append(
            {
                "source": source,
                "text": text,
                "equation_label": _clean(row.get("label")) if source == "equation" else "",
                "order": _order_key(row.get("block_id"), row.get("page"), block_order),
            }
        )

    for idx, eq_id in enumerate(_as_list(symbol_row.get("defining_equation_ids"))):
        ref_id = str(eq_id or "").strip()
        if not ref_id:
            continue
        _add(
            "equation",
            ref_id,
            equations.get((document_id, ref_id)),
            texts[idx] if idx < len(texts) else (texts[0] if texts else ""),
        )

    for idx, ev_id in enumerate(_as_list(symbol_row.get("source_evidence_ids"))):
        ref_id = str(ev_id or "").strip()
        if not ref_id:
            continue
        _add(
            "evidence",
            ref_id,
            evidence.get((document_id, ref_id)),
            texts[idx] if idx < len(texts) else (texts[0] if texts else ""),
        )

    # 参照 ID を 1 つも持たないが逐語だけある記号（定義文は取れたが所在が残っていない）。
    if not candidates and texts:
        candidates.append(
            {"source": "text", "text": texts[0], "equation_label": "", "order": None}
        )

    return [c for c in candidates if c["text"]]


def _resolve_tap_order(
    *,
    equation_id: str,
    chunk_id: str,
    session: Any,
    document_ids: list[str],
    equations: dict[tuple[str, str], dict],
    block_order: dict[str, int],
) -> Optional[tuple[int, int]]:
    """タップ位置の順序。式 → チャンクの順に解決し、解けなければ ``None``。"""
    if equation_id:
        for document_id in document_ids:
            row = equations.get((document_id, equation_id))
            if row:
                key = _order_key(row.get("block_id"), row.get("page"), block_order)
                if key is not None:
                    return key
    if chunk_id and is_uuid(chunk_id):
        try:
            row = (
                session.execute(
                    sa_text(
                        """
                        SELECT chunk_index, block_ids
                          FROM chunks
                         WHERE id = CAST(:chunk_id AS uuid)
                           AND document_id = ANY(CAST(:doc_ids AS uuid[]))
                        """
                    ),
                    {"chunk_id": chunk_id, "doc_ids": document_ids},
                )
                .mappings()
                .fetchone()
            )
        except Exception:  # noqa: BLE001
            logger.warning("symbol_lookup: chunk position unavailable", exc_info=True)
            row = None
        if row:
            for block_id in _as_list(row.get("block_ids")):
                key = _order_key(block_id, None, block_order)
                if key is not None:
                    return key
    return None


def _choose_definition(
    candidates: list[dict], tap_order: Optional[tuple[int, int]]
) -> tuple[Optional[dict], str]:
    """ScholarPhi 規則で 1 件選ぶ。戻り値は ``(定義, 事実文)``。

    1. タップ位置より**前**（同じ順序空間）の定義のうち最も近いもの。
    2. 無ければ**後ろ**の最初の定義（``FACT_DEFINED_LATER`` を添える）。
    3. タップ位置が解けない／どの定義も順序を持たないときは、順序のある定義の
       先頭（無ければ候補の先頭）を ``FACT_POSITION_UNKNOWN`` 付きで返す。
    """
    if not candidates:
        return None, ""

    ordered = [c for c in candidates if _comparable(c["order"], tap_order)]
    if ordered:
        before = [c for c in ordered if c["order"] <= tap_order]
        if before:
            return max(before, key=lambda c: c["order"]), ""
        after = [c for c in ordered if c["order"] > tap_order]
        if after:
            return min(after, key=lambda c: c["order"]), FACT_DEFINED_LATER

    with_order = sorted(
        (c for c in candidates if c["order"] is not None), key=lambda c: c["order"]
    )
    chosen = with_order[0] if with_order else candidates[0]
    return chosen, FACT_POSITION_UNKNOWN


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def lookup_symbol_definition(
    session: Any,
    *,
    symbol: str,
    document_ids: list[str] | set[str] | None,
    equation_id: str | None = None,
    chunk_id: str | None = None,
) -> dict:
    """記号の「直前の定義」を返す（LLM 0 回・既存データのみ）。

    Args:
        session: 呼び出し側が開閉する DB セッション。
        symbol: 学習者がタップした記号（生の表記のまま。正規化は内部で行う）。
        document_ids: 受講コースの sources（``documents.id``）。**空なら SQL を
            発行せず** ``available=False`` を返す（fail-closed）。
        equation_id: タップ位置の式（``knowledge_equations.agent_equation_id``）。
        chunk_id: タップ位置のチャンク（``chunks.id``）。式が無いときの代替。

    Returns:
        ``{"available": bool, "symbol": str, "facts": [str], ...}``。
        ``confidence`` / ``stable_key`` / ``produced_by_run_id`` / 内部 ID は
        載せない（KR6 / KO10）。
    """
    wanted = normalize_key(symbol)
    display_symbol = _clean(symbol, limit=40)
    doc_ids = _uuid_only(document_ids)

    empty: dict[str, Any] = {
        "available": False,
        "symbol": display_symbol,
        "facts": [],
        "definition": None,
        "concept_ref": None,
    }
    if not wanted or not doc_ids:
        return empty

    symbol_rows = [
        row for row in _load_symbol_rows(session, doc_ids) if _symbol_matches(row, wanted)
    ][:_MAX_SYMBOL_ROWS]
    if not symbol_rows:
        return empty

    block_order = _load_block_order(session, doc_ids)
    equations = _load_equation_positions(session, doc_ids)
    evidence = _load_evidence_positions(session, doc_ids)
    tap_order = _resolve_tap_order(
        equation_id=str(equation_id or "").strip(),
        chunk_id=str(chunk_id or "").strip(),
        session=session,
        document_ids=doc_ids,
        equations=equations,
        block_order=block_order,
    )

    # タップ位置の論文を優先する（同名記号が複数論文にあるときの決定論的な優先順）。
    tap_document_id = ""
    eq_key = str(equation_id or "").strip()
    if eq_key:
        for document_id in doc_ids:
            if (document_id, eq_key) in equations:
                tap_document_id = document_id
                break
    symbol_rows.sort(
        key=lambda r: (
            0 if str(r.get("document_id") or "") == tap_document_id else 1,
            str(r.get("document_id") or ""),
            str(r.get("agent_symbol_id") or ""),
        )
    )

    titles = _load_document_titles(session, doc_ids)

    for row in symbol_rows:
        candidates = _definition_candidates(
            row, equations=equations, evidence=evidence, block_order=block_order
        )
        chosen, fact = _choose_definition(candidates, tap_order)
        if chosen is None:
            continue
        return _build_result(
            session, row, chosen=chosen, fact=fact, titles=titles, display_symbol=display_symbol
        )

    # 一致する記号行はあるが、定義の逐語がどこにも無い（KR8: 主語は「この論文」）。
    row = symbol_rows[0]
    return _build_result(
        session, row, chosen=None, fact=FACT_NO_DEFINITION, titles=titles, display_symbol=display_symbol
    )


def _build_result(
    session: Any,
    row: dict,
    *,
    chosen: Optional[dict],
    fact: str,
    titles: dict[str, str],
    display_symbol: str,
) -> dict:
    document_id = str(row.get("document_id") or "")
    agent_symbol_id = str(row.get("agent_symbol_id") or "").strip()

    facts: list[str] = []
    if fact:
        facts.append(fact)

    definition: Optional[dict] = None
    if chosen is not None:
        definition = {"text": chosen["text"]}
        if chosen.get("equation_label"):
            # 論文の印字番号（``(12)``）は読者に可読な出所。内部 ID は入れない（PL7）。
            definition["equation_label"] = chosen["equation_label"]
    else:
        status_label = element_vocab.definition_status_label(row.get("definition_status"))
        if status_label:
            facts.insert(0, status_label)

    result: dict[str, Any] = {
        "available": True,
        "symbol": display_symbol or _clean(row.get("canonical_symbol"), limit=40),
        "definition": definition,
        "facts": facts,
        "source": titles.get(document_id, ""),
        "concept_ref": _load_concept_ref(
            session, document_id=document_id, agent_symbol_id=agent_symbol_id
        ),
    }

    unit = _clean(row.get("unit"), limit=40)
    if unit:
        result["unit"] = unit
    scope_label = element_vocab.symbol_scope_label(row.get("scope"))
    if scope_label:
        result["scope_label"] = scope_label
    return result
