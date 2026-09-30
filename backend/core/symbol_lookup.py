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
  記号の一致は :func:`symbol_key` の**完全一致**のみ（大文字小文字を区別する —
  ``λ`` と ``Λ`` は別の記号。IK-0387）。部分一致は P0-2 の F-7（``SM`` が
  ``cosmological`` に当たる）と同じ事故を招く。
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
import re
from typing import Any, Optional

from sqlalchemy import text as sa_text

from core import element_vocab
from core.learner_context_common import is_uuid
from core.text_hygiene import strip_control_sequences

logger = logging.getLogger(__name__)

__all__ = [
    "FACT_DEFINED_LATER",
    "FACT_DEFINITION_FROM",
    "FACT_DEFINITION_FROM_OTHER_DOCUMENT",
    "FACT_NO_DEFINITION",
    "FACT_NO_SOURCE_DOCUMENTS",
    "FACT_POSITION_UNKNOWN",
    "FACT_SYMBOL_NOT_REGISTERED",
    "lookup_symbol_definition",
    "symbol_key",
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

#: 記号レジストリに当該記号の行が1つも無いとき（IK-0386。``available=False`` を
#: 事実文なしで返さない）。主語は「このコースの論文」— 分野レベルの不在は言わない（KR8）。
FACT_SYMBOL_NOT_REGISTERED = "このコースの論文には、この記号の登録がありません。"

#: 照会できる論文（コースの sources で解析済みのもの）が1つも無いとき（IK-0386）。
FACT_NO_SOURCE_DOCUMENTS = "このコースには、記号を照会できる論文がありません。"

#: タップ位置が与えられず（または解けず）どの論文の記述かが画面から分からないとき、
#: 出所の論文タイトルを添える（IK-0387。タイトルのみ・内部 ID は入れない）。
FACT_DEFINITION_FROM = "論文『{title}』の記述です。"

#: タップした論文に定義が無く、同じコースの別の論文の定義へ倒したとき（IK-0387）。
#: 論文をまたいで「前／後」を比べない（順序は論文の中でしか意味を持たない）ので、
#: 位置の事実文ではなくこの1文だけを添える。
#: 表示中の論文にこの記号の登録が無いとき（第 15 周。別の論文の定義は持ってこない）。
FACT_SYMBOL_NOT_IN_THIS_DOCUMENT = "この論文には、この記号の登録がありません。"

#: 同じ記号がコースの別の論文にもあるとき（定義は出さず、題名だけを並べる）。
FACT_SAME_SYMBOL_IN_OTHER_DOCUMENTS = "別の論文にも同じ記号があります（{titles}）。"
_MAX_OTHER_TITLES = 3

FACT_DEFINITION_FROM_OTHER_DOCUMENT = (
    "この論文にはこの記号の定義が見つからなかったため、"
    "同じコースの論文『{title}』で最初に現れる定義を表示しています。"
)

#: タップ位置の論文に対して相対的な意味しか持たない有効範囲（「この節の中」
#: 「この式の中だけ」）。タップ位置がその論文に無いときは表示しない（IK-0387）。
_RELATIVE_SCOPES = frozenset({"section", "equation_local"})

# 定義の逐語が見つからなかったとき（FACT_NO_DEFINITION）にも並べてよい定義状態のキー。
# 訳語は element_vocab.DEFINITION_STATUS_LABELS が正本（ここはキーの集合だけ）。
_STATUSES_CONSISTENT_WITH_NO_DEFINITION = frozenset({"definition_missing"})

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


# 記号の照合キー（IK-0387）。``concept_normalizer.normalize_key`` は**概念名**の
# 正規化で、大文字小文字を畳み ``Λ`` と ``λ`` を同じ ``lambda`` にする。記号では
# ``λ``（質量対磁束比）と ``Λ``（別の量）、``B`` と ``b`` は別物なので、ここでは
# 大文字小文字を保ったまま表記の揺れ（TeX ⇄ Unicode・波括弧・書体指定・空白）だけを畳む。
_TEX_GREEK: dict[str, str] = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "θ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "omicron": "ο", "pi": "π", "varpi": "π", "rho": "ρ", "varrho": "ρ",
    "sigma": "σ", "varsigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
# Unicode の異体字を基本字へ（ϵ→ε など。大文字小文字は変えない）。
_UNICODE_VARIANTS = {"µ": "μ", "ϵ": "ε", "ϑ": "θ", "ϖ": "π", "ϱ": "ρ", "ς": "σ", "ϕ": "φ"}
# 書体だけを変える命令（KaTeX の textContent は書体を区別しないので畳む）。
_FONT_COMMANDS_RE = re.compile(
    r"\\(?:mathrm|rm|textrm|text|mathit|it|mathcal|mathbb|mathbf|bf|boldsymbol|bm|operatorname)(?![A-Za-z])"
)
_TEX_COMMAND_RE = re.compile(r"\\([A-Za-z]+)")


def symbol_key(value: Any) -> str:
    """記号の照合キー（決定論・大文字小文字を区別する）。

    ``\\lambda`` と ``λ`` は同じキー、``\\Lambda`` / ``Λ`` はそれとは別のキー。
    ``V_{cb}`` / ``V_cb``、``B_{\\rm 3D}`` / ``B_{3D}`` / ``B_3D`` は同じキー。
    部分一致はしない（キーの完全一致だけで照合する — P0-2 / F-7）。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("$", "")
    text = _FONT_COMMANDS_RE.sub("", text)
    text = _TEX_COMMAND_RE.sub(lambda m: _TEX_GREEK.get(m.group(1), "\\" + m.group(1)), text)
    for src, dst in _UNICODE_VARIANTS.items():
        text = text.replace(src, dst)
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"_+", "_", text)
    text = re.sub(r"\^+", "^", text)
    return text.strip("_^")


def _symbol_matches(row: dict, wanted: str) -> bool:
    """``canonical_symbol`` / ``notation_variants`` の**完全一致**（:func:`symbol_key` 後）。"""
    if symbol_key(row.get("canonical_symbol")) == wanted:
        return True
    return any(symbol_key(v) == wanted for v in _as_list(row.get("notation_variants")))


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
    # P3-R4: 学習者 DTO には表示に要るものだけ載せる。``entry_id``（内部 ID）と
    # 生の ``entry_type``（語彙キー）は学習者向けの表示に使わないので出さない
    # （内部 ID 非漏洩 / 新しい語彙を学習者に見せない）。
    ref: dict[str, Any] = {"name": name}
    label = _entry_type_label(str(row.get("entry_type") or ""))
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


def _resolve_focus_documents(
    session: Any,
    doc_ids: list[str],
    *,
    chunk_id: str,
    focus_document_ids: Any,
) -> list[str]:
    """「この論文」の集合: タップ位置のチャンクの論文 → 表示中トピックの論文。

    どちらもコースの sources（``doc_ids``）との積だけを採る（広げない）。空なら
    従来どおりコース全体（呼び出し側の後方互換）。
    """
    allowed = set(doc_ids)
    if chunk_id and is_uuid(chunk_id):
        try:
            row = (
                session.execute(
                    sa_text(
                        """
                        SELECT document_id::text AS document_id
                          FROM chunks
                         WHERE id = CAST(:chunk_id AS uuid)
                           AND document_id = ANY(CAST(:doc_ids AS uuid[]))
                        """
                    ),
                    {"chunk_id": chunk_id, "doc_ids": doc_ids},
                )
                .mappings()
                .fetchone()
            )
        except Exception:  # noqa: BLE001 - fail-soft（トピックの論文へ）
            logger.debug("symbol_lookup: chunk document unavailable", exc_info=True)
            row = None
        chunk_document_id = str((row or {}).get("document_id") or "")
        if chunk_document_id in allowed:
            return [chunk_document_id]
    return sorted(set(_uuid_only(focus_document_ids)) & allowed)


def _other_documents_fact(titles: dict[str, str], other_docs: list[str]) -> str:
    names = [
        f"『{_clean(titles.get(d, ''), limit=80)}』"
        for d in other_docs
        if _clean(titles.get(d, ""), limit=80)
    ][:_MAX_OTHER_TITLES]
    if not names:
        return ""
    return FACT_SAME_SYMBOL_IN_OTHER_DOCUMENTS.format(titles="・".join(names))


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
) -> tuple[Optional[tuple[int, int]], str]:
    """タップ位置の ``(順序, 論文)``。式 → チャンクの順に解決する。

    順序が解けなくても、式・チャンクがどの論文のものかが分かれば論文は返す
    （同じ論文の記号を先に当てるため — IK-0387）。どちらも解けなければ
    ``(None, "")``。
    """
    tap_document_id = ""
    if equation_id:
        for document_id in document_ids:
            row = equations.get((document_id, equation_id))
            if row:
                tap_document_id = tap_document_id or document_id
                key = _order_key(row.get("block_id"), row.get("page"), block_order)
                if key is not None:
                    return key, document_id
    if chunk_id and is_uuid(chunk_id):
        try:
            row = (
                session.execute(
                    sa_text(
                        """
                        SELECT document_id::text AS document_id, chunk_index, block_ids
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
            chunk_document_id = str(row.get("document_id") or "")
            tap_document_id = tap_document_id or chunk_document_id
            for block_id in _as_list(row.get("block_ids")):
                key = _order_key(block_id, None, block_order)
                if key is not None:
                    return key, (chunk_document_id or tap_document_id)
    return None, tap_document_id


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
    focus_document_ids: list[str] | set[str] | None = None,
) -> dict:
    """記号の「直前の定義」を返す（LLM 0 回・既存データのみ）。

    ``focus_document_ids``（第 15 周）: 表示中トピックが束ねる論文。タップ位置の
    チャンクが解ければその論文、無ければこの集合を「この論文」とみなし、定義は
    **その中からだけ**引く。コースの別の論文に同じ記号があっても定義は持ってこず、
    「別の論文にも同じ記号があります（題名列挙）」の事実文に留める（記号は論文ごとに
    意味が違う — 暗黒エネルギー論文の ``c_s^2`` に中性子星論文の定義を出さない）。

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
    wanted = symbol_key(symbol)
    display_symbol = _clean(symbol, limit=40)
    doc_ids = _uuid_only(document_ids)

    def _unavailable(fact: str) -> dict[str, Any]:
        # IK-0386: ``available=False`` を事実文なしで返さない（何が無いのかを1文で言う）。
        return {
            "available": False,
            "symbol": display_symbol,
            "facts": [fact],
            "definition": None,
            "concept_ref": None,
        }

    if not doc_ids:
        return _unavailable(FACT_NO_SOURCE_DOCUMENTS)
    if not wanted:
        return _unavailable(FACT_SYMBOL_NOT_REGISTERED)

    focus = _resolve_focus_documents(
        session, doc_ids, chunk_id=str(chunk_id or "").strip(), focus_document_ids=focus_document_ids
    )
    all_symbol_rows = [
        row for row in _load_symbol_rows(session, doc_ids) if _symbol_matches(row, wanted)
    ]
    other_fact = ""
    if focus:
        other_docs = sorted(
            {str(r.get("document_id") or "") for r in all_symbol_rows} - set(focus)
        )
        if other_docs:
            other_fact = _other_documents_fact(_load_document_titles(session, other_docs), other_docs)
        all_symbol_rows = [r for r in all_symbol_rows if str(r.get("document_id") or "") in focus]
        search_doc_ids = sorted(focus)
    else:
        search_doc_ids = doc_ids
    symbol_rows = all_symbol_rows[:_MAX_SYMBOL_ROWS]
    if not symbol_rows:
        if focus:
            fallback = _formula_fallback(
                session, search_doc_ids, symbol=symbol, display_symbol=display_symbol
            )
            if fallback is None:
                fallback = _unavailable(FACT_SYMBOL_NOT_IN_THIS_DOCUMENT)
            if other_fact:
                fallback["facts"] = list(fallback.get("facts") or []) + [other_fact]
            return fallback
        # IK（第 14 周）: PDF 経路では symbol_registry が式の断片化で主記号を取り逃がす
        # （A層の欠落・非改変）。配信済みの式（chunks.formulas）から、この記号が
        # **左辺に立つ式**と**現れる式**を印字番号で示す決定論フォールバック。
        fallback = _formula_fallback(session, doc_ids, symbol=symbol, display_symbol=display_symbol)
        if fallback is not None:
            return fallback
        return _unavailable(FACT_SYMBOL_NOT_REGISTERED)

    block_order = _load_block_order(session, doc_ids)
    equations = _load_equation_positions(session, doc_ids)
    evidence = _load_evidence_positions(session, doc_ids)
    tap_order, tap_document_id = _resolve_tap_order(
        equation_id=str(equation_id or "").strip(),
        chunk_id=str(chunk_id or "").strip(),
        session=session,
        # 式 ID は印字番号由来で論文をまたいで衝突する（``eq_5``）— 焦点の論文の中で引く。
        document_ids=search_doc_ids,
        equations=equations,
        block_order=block_order,
    )

    # タップ位置の論文を優先する（同名記号が複数論文にあるときの決定論的な優先順）。
    symbol_rows.sort(
        key=lambda r: (
            0 if str(r.get("document_id") or "") == tap_document_id else 1,
            str(r.get("document_id") or ""),
            str(r.get("agent_symbol_id") or ""),
        )
    )

    titles = _load_document_titles(session, doc_ids)

    for row in symbol_rows:
        row_document_id = str(row.get("document_id") or "")
        candidates = _definition_candidates(
            row, equations=equations, evidence=evidence, block_order=block_order
        )
        same_document = (bool(tap_document_id) and row_document_id == tap_document_id) or (
            bool(focus) and not tap_document_id and len(focus) == 1
        )
        if tap_document_id and not same_document:
            # 別の論文の定義へ倒す: 論文をまたいで「前／後」は比べない（IK-0387）。
            chosen, _ = _choose_definition(candidates, None)
            fact = _title_fact(FACT_DEFINITION_FROM_OTHER_DOCUMENT, titles.get(row_document_id, ""))
        else:
            chosen, fact = _choose_definition(candidates, tap_order if same_document else None)
        if chosen is None:
            continue
        extra: list[str] = []
        if not tap_document_id and not (focus and len(focus) == 1):
            # どの論文の記述かが画面から分からない（タップ位置なし）ので出所を添える。
            source_fact = _title_fact(FACT_DEFINITION_FROM, titles.get(row_document_id, ""))
            if source_fact:
                extra.append(source_fact)
        if other_fact:
            extra.append(other_fact)
        return _build_result(
            session,
            row,
            chosen=chosen,
            fact=fact,
            titles=titles,
            display_symbol=display_symbol,
            extra_facts=extra,
            relative_scope_ok=same_document,
        )

    # 一致する記号行はあるが、定義の逐語がどこにも無い（KR8: 主語は「この論文」）。
    row = symbol_rows[0]
    return _build_result(
        session,
        row,
        chosen=None,
        fact=FACT_NO_DEFINITION,
        titles=titles,
        display_symbol=display_symbol,
        extra_facts=[other_fact] if other_fact else None,
        relative_scope_ok=bool(tap_document_id)
        and str(row.get("document_id") or "") == tap_document_id,
    )


# ---------------------------------------------------------------------------
# 記号レジストリに行が無いときのフォールバック（配信済みの式から・決定論）
# ---------------------------------------------------------------------------

#: 登録は無いが、この記号を左辺に持つ式が見つかったとき（生 TeX は出さず印字番号だけ）。
FACT_FORMULA_DEFINES = (
    "記号の登録はありませんが、この記号は{where}の左辺に現れます"
    "（定義の式である可能性があります）。"
)
#: 登録は無いが、この記号を含む式が見つかったとき。
FACT_FORMULA_USES = "記号の登録はありませんが、この記号は{where}の中に現れます。"
#: フォールバックの出所（式の読み取りが PDF からの復元であることを隠さない）。
FACT_FORMULA_FALLBACK_NOTE = (
    "記号の対応はシステムが式の表記から機械的に照合したもので、"
    "PDF からの式の読み取りには崩れが含まれることがあります。"
)
_MAX_FALLBACK_LABELS = 3
_ARGUMENT_TAIL_RE = re.compile(r"\([^()]*\)$")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _loose_symbol_key(value: Any) -> str:
    """PDF 復元式で添字記号が落ちる（``\\alpha_B`` → ``\\alpha B``）ことを吸収するキー。

    :func:`symbol_key` の後で ``_`` / ``^`` を除くだけ（大文字小文字は保つ）。
    フォールバック専用で、レジストリ照合（完全一致）には使わない。
    """
    return symbol_key(value).replace("_", "").replace("^", "")


def _strip_argument(key: str) -> str:
    return _ARGUMENT_TAIL_RE.sub("", key)


def _contains_symbol(haystack: str, needle: str) -> bool:
    """語境界付きの包含（前後が英字でないこと）。部分文字列一致の F-7 を避ける。"""
    start = 0
    while needle:
        idx = haystack.find(needle, start)
        if idx < 0:
            return False
        before = haystack[idx - 1] if idx > 0 else ""
        after = haystack[idx + len(needle)] if idx + len(needle) < len(haystack) else ""
        if not _LATIN_RE.match(before or " ") and not _LATIN_RE.match(after or " "):
            return True
        start = idx + 1
    return False


def _where_text(labels: list[str], unlabeled: bool) -> str:
    parts = [f"式 ({label})" for label in labels[:_MAX_FALLBACK_LABELS]]
    if unlabeled and len(parts) < _MAX_FALLBACK_LABELS:
        parts.append("番号の無い式")
    return "・".join(parts)


def _formula_fallback(
    session: Any, document_ids: list[str], *, symbol: str, display_symbol: str
) -> Optional[dict]:
    wanted = _loose_symbol_key(symbol)
    wanted_bare = _strip_argument(wanted)
    if not wanted_bare:
        return None
    try:
        rows = (
            session.execute(
                sa_text(
                    """
                    SELECT document_id::text AS document_id, chunk_index, formulas
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
    except Exception:  # noqa: BLE001 - fail-soft（登録なしの事実文へ落とす）
        logger.debug("symbol formula fallback failed", exc_info=True)
        return None

    defines: dict[str, dict] = {}
    uses: dict[str, dict] = {}
    for row in rows or []:
        document_id = str(row.get("document_id") or "")
        for formula in _as_list(row.get("formulas")):
            if not isinstance(formula, dict):
                continue
            latex = str(formula.get("latex") or "")
            if not latex.strip():
                continue
            label = _clean(formula.get("label"), limit=12)
            lhs = latex.split("=", 1)[0] if "=" in latex else ""
            lhs_key = _strip_argument(_loose_symbol_key(lhs))
            bucket = None
            if lhs_key and lhs_key == wanted_bare:
                bucket = defines
            elif len(wanted_bare) >= 2 and _contains_symbol(_loose_symbol_key(latex), wanted_bare):
                bucket = uses
            if bucket is None:
                continue
            entry = bucket.setdefault(document_id, {"labels": [], "unlabeled": False})
            if label:
                if label not in entry["labels"]:
                    entry["labels"].append(label)
            else:
                entry["unlabeled"] = True

    chosen_bucket, template = (defines, FACT_FORMULA_DEFINES) if defines else (uses, FACT_FORMULA_USES)
    if not chosen_bucket:
        return None
    # 印字番号のある論文を優先（決定論: 番号あり → document_id 順）。
    document_id = sorted(chosen_bucket, key=lambda d: (0 if chosen_bucket[d]["labels"] else 1, d))[0]
    entry = chosen_bucket[document_id]
    titles = _load_document_titles(session, document_ids)
    facts = [template.format(where=_where_text(entry["labels"], entry["unlabeled"]))]
    source_fact = _title_fact(FACT_DEFINITION_FROM, titles.get(document_id, ""))
    if source_fact:
        facts.append(source_fact)
    facts.append(FACT_FORMULA_FALLBACK_NOTE)
    return {
        "available": True,
        "symbol": display_symbol,
        "definition": None,
        "facts": facts,
        "source": titles.get(document_id, ""),
        "concept_ref": None,
        "registered": False,
    }


def _title_fact(template: str, title: str) -> str:
    """論文タイトル入りの事実文。タイトルが引けなければ空（推測で埋めない）。"""
    title = _clean(title, limit=120)
    if not title:
        return ""
    return template.format(title=title)


def _build_result(
    session: Any,
    row: dict,
    *,
    chosen: Optional[dict],
    fact: str,
    titles: dict[str, str],
    display_symbol: str,
    extra_facts: Optional[list[str]] = None,
    relative_scope_ok: bool = True,
) -> dict:
    document_id = str(row.get("document_id") or "")
    agent_symbol_id = str(row.get("agent_symbol_id") or "").strip()

    facts: list[str] = []
    if fact:
        facts.append(fact)
    for line in extra_facts or []:
        if line and line not in facts:
            facts.append(line)

    definition: Optional[dict] = None
    if chosen is not None:
        definition = {"text": chosen["text"]}
        if chosen.get("equation_label"):
            # 論文の印字番号（``(12)``）は読者に可読な出所。内部 ID は入れない（PL7）。
            definition["equation_label"] = chosen["equation_label"]
    else:
        # 定義の逐語が見つからないときに添える定義状態は、「この論文に定義が無い」と
        # 食い違わないもの（``definition_missing``）だけ。``used`` の「定義は別の箇所」や
        # ``defined`` の「この論文で定義」は FACT_NO_DEFINITION と含意が逆になる（IK-0380）。
        status_key = str(row.get("definition_status") or "").strip()
        if status_key in _STATUSES_CONSISTENT_WITH_NO_DEFINITION:
            status_label = element_vocab.definition_status_label(status_key)
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
    scope_key = str(row.get("scope") or "").strip()
    # 「この節の中」「この式の中だけ」はタップ位置の論文に対して相対的な言葉。
    # タップ位置が無い・別の論文の記号へ倒したときは、どの節かを指せないので出さない（IK-0387）。
    if scope_key not in _RELATIVE_SCOPES or relative_scope_ok:
        scope_label = element_vocab.symbol_scope_label(scope_key)
        if scope_label:
            result["scope_label"] = scope_label
    return result
