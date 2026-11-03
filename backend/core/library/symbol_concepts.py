"""記号 → 概念の同一性候補（規則 ⑥・IK-0983・第 25 周 面 A 第 5 波）。

設計正本: ``docs/features/concept_registry_design.md`` の「第 5 波の追補（2026-10-27）」。

学生の記号の吹き出しの「どの概念か」（``symbol_lookup._load_concept_ref``）は、記号の
同一性リンク（``element_identity_links.instance_element_type='symbol'``）が**確定**して
初めて出る。ところが、そのリンクを作る入口が画面にも候補づくりにも無かった（読み手だけ
あって書き手が無い = ``last-mile-missing``）。本モジュールはその書き手の半分 — 候補づくり —
を受け持つ。確定は既存のリンクの確定の弁（``POST /api/admin/deliberation/identity-links/
{id}/confirm``）だけが行う（KR2）。

規則: ``knowledge_symbols_live`` の記号の**定義の文**（``definition_evidence_texts``）に、
確定済み・公開中のエントリの ``name`` か ``alternate`` ラベルが**語として**現れたら
（``alias_matching.text_mentions_alias`` の語境界一致 = P0-2。``in`` の部分一致は書かない）、
その記号からエントリへの candidate リンク（``lexical_match``）を 1 本作る。

- **KR5 決定論・LLM 0 回・embedding 0 回** — 本モジュールは ``core.llm`` も pgvector も
  使わない。
- **記号を概念にしない（CG6）** — 記号のような名前（``r`` / ``P`` / ``\\rho``）のエントリ・
  ラベルは照合の材料にしない（``is_symbol_like_concept_name``）。``hidden`` ラベルも
  使わない（表示しない語で結ばない）。
- **KR7** — ``DELETE`` は無い。既存の組（状態を問わず）は ``create_candidate`` が上書き
  せずに返し、新しく作った数だけを数える（見送った組を作り直さない）。
- **KR6 数値を見せない** — 戻り値の件数は stage_outputs / route の内部報告で、画面の事実文
  には書かない。

FastAPI / ``core.llm`` を import しない（core/ 共通ルール）。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Iterable, Optional

from sqlalchemy import text as sa_text

from core.deliberation import identity_links as _identity_links
from core.deliberation.schema import ELEMENT_SYMBOL, SCOPE_DOCUMENT, ElementRef

from . import registry, schema

logger = logging.getLogger(__name__)

__all__ = [
    "SYMBOL_CANDIDATES_MAX_PER_DOCUMENT",
    "SYMBOL_ENTRIES_MAX_PER_SYMBOL",
    "SYMBOL_LINK_REASON",
    "derive_symbol_concept_candidates",
    "symbol_link_reason",
]

#: 1 document あたりに作る記号の候補の上限（コード定数・env では緩めない）。
SYMBOL_CANDIDATES_MAX_PER_DOCUMENT = 20
#: 1 記号から結ぶエントリの上限（長い名前の一致を先に採る）。
SYMBOL_ENTRIES_MAX_PER_SYMBOL = 2
#: 定義の文として照らす文の上限（1 記号あたり）。
_DEFINITION_TEXTS_MAX = 4
#: リンクの evidence に載せる定義の文の長さ。
_DEFINITION_EVIDENCE_MAX_CHARS = 200

#: 候補に添える事実文（数値なし・相手の論文の名前を含めない = KR10）。
SYMBOL_LINK_REASON = "記号「{symbol}」の定義の文に、概念「{entry}」の名前（または別名）が語として現れました。"
#: IK-1052（第 7 波）: 1 文がほかの記号も定義しているときに添える事実文（見分けの材料）。
SYMBOL_SHARED_SENTENCE_NOTE = (
    "この定義の文はほかの記号（{others}）も定義しています。照らしたのは、この記号を述べる部分だけです。"
)
#: IK-1052（第 7 波）: 同じ論文の別の版の同じ記号に結び付いた概念を、もう一方の版にも候補として立てる理由。
SYMBOL_SAME_PAPER_REASON = (
    "同じ論文の別の版の同じ記号「{symbol}」が、概念「{entry}」と結び付いています（{state}）。"
    "この版の記号にも同じ候補を立てました（確定は教員）。"
)
_SAME_PAPER_STATE_LABELS = {"candidate": "候補", "confirmed": "確定済み"}


def symbol_link_reason(symbol: str, entry_name: str) -> str:
    return SYMBOL_LINK_REASON.format(symbol=symbol or "（記号）", entry=entry_name or "（名前の無い概念）")


_SYMBOL_SQL = """
    SELECT document_id::text, agent_symbol_id, canonical_symbol, definition_evidence_texts
      FROM knowledge_symbols_live
     WHERE document_id = ANY(CAST(:document_ids AS uuid[]))
       AND COALESCE(agent_symbol_id, '') <> ''
     ORDER BY document_id, agent_symbol_id
"""

_ENTRY_SQL = """
    SELECT id::text, domain_key, entry_type, name
      FROM library_entries
     WHERE status = :active AND review_status = :confirmed
       {entry_filter}
     ORDER BY name, id
"""


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _texts(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    out: list[str] = []
    for item in value if isinstance(value, list) else []:
        text = _clean(item if isinstance(item, str) else (item or {}).get("text") if isinstance(item, dict) else "")
        if text:
            out.append(text)
        if len(out) >= _DEFINITION_TEXTS_MAX:
            break
    return out


def _is_symbol_like(name: str) -> bool:
    try:
        from episteme_graph.agents.component_assembly.schema import is_symbol_like_concept_name

        return bool(is_symbol_like_concept_name(name))
    except Exception:  # noqa: BLE001 — 判定できなければ短い名前を記号とみなす（保守側）
        return len(name) < 3


def load_entry_surfaces(session: Any, entry_ids: Optional[Iterable[str]] = None) -> list[dict]:
    """確定・公開中のエントリの照合語（``name`` + ``alternate``。記号のような語は除く）。

    戻り値は ``[{"id", "name", "surfaces": [...]}]``（長い語を先に）。
    """
    ids = [e for e in (entry_ids or []) if _clean(e)]
    entry_filter = "AND id = ANY(CAST(:entry_ids AS uuid[]))" if entry_ids is not None else ""
    if entry_ids is not None and not ids:
        return []
    params: dict[str, Any] = {
        "active": schema.STATUS_ACTIVE,
        "confirmed": schema.REVIEW_STATUS_CONFIRMED,
    }
    if ids:
        params["entry_ids"] = ids
    rows = session.execute(sa_text(_ENTRY_SQL.format(entry_filter=entry_filter)), params).fetchall()
    entries = [{"id": str(r[0]), "name": _clean(r[3] if len(r) > 3 else "")} for r in rows]
    labels = registry.labels_for_entries(
        [e["id"] for e in entries], include_hidden=False, session=session
    )
    out: list[dict] = []
    for entry in entries:
        surfaces = [entry["name"]] + [
            _clean(label.get("label")) for label in labels.get(entry["id"]) or ()
        ]
        kept = sorted(
            {s for s in surfaces if s and not _is_symbol_like(s)}, key=lambda s: (-len(s), s)
        )
        if kept:
            out.append({"id": entry["id"], "name": entry["name"], "surfaces": kept})
    return out


# ── IK-1052（第 7 波）: 一致を見る範囲を「その記号を述べる部分」に絞る ─────────────────
def _symbol_key(value: Any) -> str:
    try:
        from core.symbol_lookup import symbol_key

        return symbol_key(value)
    except Exception:  # noqa: BLE001
        return _clean(value)


def _symbol_text_surfaces(canonical: str) -> list[str]:
    """記号が定義の文（PDF・TeX の平文）に現れる形（``F_{EOS}`` → ``F EOS`` / ``F_EOS`` / ``FEOS``）。

    ASCII の 1 文字（``r`` / ``a``）は語として本文に紛れるので、区切りの手がかりには使わない
    （戻り値に含めない）。添字の無い 2 文字以上の記号・非 ASCII の 1 文字（``ρ``）は使う。
    """
    key = _symbol_key(canonical)
    if not key:
        return []
    forms = {key, key.replace("_", " ").replace("^", " "), key.replace("_", "").replace("^", "")}
    out = []
    for form in forms:
        form = " ".join(form.split())
        if not form:
            continue
        if len(form) == 1 and form.isascii():
            continue
        out.append(form)
    return sorted(set(out), key=lambda f: (-len(f), f))


def _mention_spans(text: str, surfaces: list[str]) -> list[tuple[int, int]]:
    """語境界つきで記号の形が現れる位置（重なりは長い形を優先）。"""
    import re

    spans: list[tuple[int, int]] = []
    for form in surfaces:
        pattern = r"(?<![A-Za-z0-9_])" + r"\s*".join(re.escape(part) for part in form.split(" ")) + r"(?![A-Za-z0-9_])"
        for m in re.finditer(pattern, text):
            if not any(m.start() < e and s < m.end() for s, e in spans):
                spans.append((m.start(), m.end()))
    return sorted(spans)


def _symbol_clause(text: str, own: list[str], others: Mapping[str, list[str]]) -> tuple[str, list[str]]:
    """定義の文のうち、この記号を述べる部分（記号の名前は伏せる）と、同じ文が定義するほかの記号。

    この記号が文に見つかり、ほかの記号も文に現れるときは、直前のほかの記号の後ろから直後のほかの
    記号の前までに絞る。記号の名前（自分・ほか）は空白で伏せる — 別名が記号名の一部
    （``N TOV`` の ``TOV``）に当たって候補が立つのを防ぐ。
    """
    own_spans = _mention_spans(text, own)
    other_spans: list[tuple[int, int]] = []
    other_names: list[str] = []
    for name, forms in others.items():
        spans = _mention_spans(text, forms)
        if spans:
            other_spans.extend(spans)
            other_names.append(name)
    other_spans = [sp for sp in other_spans if not any(sp[0] < e and s < sp[1] for s, e in own_spans)]
    start, end = 0, len(text)
    if own_spans and other_spans:
        first_s, first_e = own_spans[0]
        before = [e for s, e in other_spans if e <= first_s]
        after = [s for s, e in other_spans if s >= first_e]
        start = max(before) if before else 0
        end = min(after) if after else len(text)
    chars = list(text)
    for s, e in own_spans + other_spans:
        for i in range(s, e):
            chars[i] = " "
    clause = " ".join("".join(chars[start:end]).split())
    return clause, (other_names if own_spans else [])


def _alias_inside_other_symbol(surface: str, other_forms: Mapping[str, list[str]]) -> bool:
    """別名がほかの記号名の一部（``TOV`` ⊂ ``N_TOV``）なら真（語の単位で比べる）。"""
    words = surface.casefold().split()
    if len(words) != 1:
        return False
    word = words[0]
    for forms in other_forms.values():
        for form in forms:
            if word in form.casefold().replace("_", " ").split():
                return True
    return False


def _matched_entries(
    definition_texts: list[str],
    entries: list[dict],
    *,
    own_forms: Optional[list[str]] = None,
    other_forms: Optional[Mapping[str, list[str]]] = None,
) -> list[tuple[dict, str]]:
    """定義の文に語として現れたエントリ（``(entry, 当たった語)``・長い語の一致を先に）。

    IK-1052: ``own_forms`` / ``other_forms`` が渡されたら、文を「この記号を述べる部分」に絞り
    （記号名は伏せる）、ほかの記号名の一部にあたる別名は使わない。
    """
    from episteme_graph.agents.alias_matching import text_mentions_alias

    others = dict(other_forms or {})
    texts = [
        _symbol_clause(text, list(own_forms or []), others)[0] if (own_forms or others) else text
        for text in definition_texts
    ]
    hits: list[tuple[dict, str]] = []
    for entry in entries:
        for surface in entry["surfaces"]:
            if surface != entry.get("name") and _alias_inside_other_symbol(surface, others):
                continue
            if any(text_mentions_alias(text, surface) for text in texts):
                hits.append((entry, surface))
                break
    hits.sort(key=lambda pair: (-len(pair[1]), pair[0]["name"], pair[0]["id"]))
    return hits[:SYMBOL_ENTRIES_MAX_PER_SYMBOL]


def _shared_sentence_others(
    definition_texts: list[str], own_forms: list[str], other_forms: Mapping[str, list[str]]
) -> list[str]:
    """定義の文が同時に定義しているほかの記号の名前（文ごとの和・出現順）。"""
    names: list[str] = []
    for text in definition_texts:
        for name in _symbol_clause(text, own_forms, other_forms)[1]:
            if name not in names:
                names.append(name)
    return names


def derive_symbol_concept_candidates(
    session: Any,
    *,
    document_ids: Iterable[str],
    entry_ids: Optional[Iterable[str]] = None,
    limit: int = SYMBOL_CANDIDATES_MAX_PER_DOCUMENT,
) -> dict:
    """記号 → 確定済みエントリの candidate リンクを作る（決定論・LLM 0 回）。

    Args:
        document_ids: 照らす document（呼び出し側が可視性・対象を決める）。
        entry_ids: 照合するエントリを絞る（ライブラリ詳細の「記号の候補を探す」）。
            ``None`` なら確定・公開中のすべて。
        limit: 新しく作る候補の上限（document をまたいだ合計）。

    Returns:
        ``{"symbols_scanned", "links_created", "truncated", "existing_matches"}``（内部報告・画面に数値を出さない）。
        ``existing_matches`` は名前が語として現れたが既にリンクの行がある組の
        ``{"symbol", "status"}`` の列（IK-1153: 新しく作らなかった理由を言い分ける）。
    """
    doc_ids = sorted({_clean(d) for d in document_ids or () if _clean(d)})
    stats: dict = {"symbols_scanned": 0, "links_created": 0, "truncated": False, "existing_matches": []}
    if not doc_ids or limit <= 0:
        return stats
    try:
        entries = load_entry_surfaces(session, entry_ids)
    except Exception:  # noqa: BLE001 — エントリが読めなければ何も作らない（fail-soft）
        logger.warning("symbol concept rule: entry lookup failed (non-fatal)", exc_info=True)
        return stats
    if not entries:
        return stats
    try:
        rows = session.execute(sa_text(_SYMBOL_SQL), {"document_ids": doc_ids}).fetchall()
    except Exception:  # noqa: BLE001
        logger.warning("symbol concept rule: symbol lookup failed (non-fatal)", exc_info=True)
        return stats
    # IK-1052: 同じ論文の記号どうしの名前（文の中の区切りと「別名が記号名の一部か」に使う）。
    forms_by_doc: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        doc, canonical = _clean(row[0]), _clean(row[2])
        name = _symbol_key(canonical) or canonical
        forms = _symbol_text_surfaces(canonical)
        if name and forms:
            forms_by_doc.setdefault(doc, {})[name] = forms
    for row in rows:
        definition_texts = _texts(row[3] if len(row) > 3 else [])
        stats["symbols_scanned"] += 1
        if not definition_texts:
            continue
        document_id, symbol_id, canonical = _clean(row[0]), _clean(row[1]), _clean(row[2])
        own_name = _symbol_key(canonical) or canonical
        own_forms = _symbol_text_surfaces(canonical)
        other_forms = {
            n: f for n, f in (forms_by_doc.get(document_id) or {}).items() if n != own_name
        }
        shared_with = _shared_sentence_others(definition_texts, own_forms, other_forms)
        for entry, _surface in _matched_entries(
            definition_texts, entries, own_forms=own_forms, other_forms=other_forms
        ):
            if stats["links_created"] >= limit:
                stats["truncated"] = True
                return stats
            ref = ElementRef(
                scope=SCOPE_DOCUMENT,
                element_type=ELEMENT_SYMBOL,
                element_id=symbol_id,
                document_id=document_id,
            )
            try:
                ref.validate()
                link = _identity_links.create_candidate(
                    ref,
                    entry["id"],
                    local_expression={"name": canonical, "notation": canonical},
                    evidence=[{"definition_text": definition_texts[0][:_DEFINITION_EVIDENCE_MAX_CHARS]}],
                    reason=symbol_link_reason(canonical, entry["name"]) + (
                        SYMBOL_SHARED_SENTENCE_NOTE.format(others="・".join(shared_with))
                        if shared_with else ""
                    ),
                    mapping_justification=schema.JUSTIFICATION_LEXICAL,
                )
            except Exception:  # noqa: BLE001 — 1 本作れなくても他の候補は残す
                logger.warning("symbol concept candidate failed (non-fatal)", exc_info=True)
                continue
            outcome = (link or {}).get("outcome")
            if outcome == _identity_links.LINK_OUTCOME_CREATED:
                stats["links_created"] += 1
            elif outcome in _EXISTING_OUTCOME_STATUS:
                # IK-1153: 一致したが既に行がある（判断済みなら教員が戻せる）。
                stats["existing_matches"].append(
                    {"symbol": canonical, "status": _EXISTING_OUTCOME_STATUS[outcome]}
                )
    _carry_over_same_paper_versions(session, rows, doc_ids, entries, stats, limit)
    return stats


#: IK-1153: create_candidate の既存の結果 → リンクの status。
_EXISTING_OUTCOME_STATUS = {
    _identity_links.LINK_OUTCOME_EXISTING_CANDIDATE: "candidate",
    _identity_links.LINK_OUTCOME_EXISTING_CONFIRMED: "confirmed",
    _identity_links.LINK_OUTCOME_EXISTING_REJECTED: "rejected",
    _identity_links.LINK_OUTCOME_EXISTING_REVOKED: "revoked",
}


def _carry_over_same_paper_versions(
    session: Any, rows: list, doc_ids: list[str], entries: list[dict], stats: dict, limit: int
) -> None:
    """IK-1052: 同じ論文の別の版にある同じ記号（照合キーが同じ）へ、もう一方の版の候補・確定を写す。

    写すのは candidate リンクだけ（確定は教員 — KR2）。見送り・取り消しの組は写さない。
    対象の document は呼び出し側が渡した（可視性を通した）集合の中だけ。fail-soft。
    """
    if len(doc_ids) < 2 or stats.get("truncated"):
        return
    entry_names = {e["id"]: e["name"] for e in entries}
    try:
        from core.library.identity_candidates import _DOCUMENT_IDENTITY_SQL, same_paper

        doc_rows = session.execute(sa_text(_DOCUMENT_IDENTITY_SQL), {"document_ids": doc_ids}).fetchall()
        link_rows = session.execute(
            sa_text(
                """
                SELECT instance_document_id::text, instance_element_id, shared_part_id::text, status
                  FROM element_identity_links
                 WHERE instance_element_type = 'symbol'
                   AND instance_document_id = ANY(CAST(:document_ids AS uuid[]))
                   AND status IN ('candidate', 'confirmed')
                """
            ),
            {"document_ids": doc_ids},
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.warning("symbol same-paper carry-over lookup failed (non-fatal)", exc_info=True)
        return
    identities: dict[str, dict] = {}
    for r in doc_rows or []:
        if len(r) >= 5:
            identities[str(r[0])] = {"title": r[1], "filename": r[2], "source_url": r[3], "source_path": r[4]}
    if len(identities) < 2:
        return
    symbols: dict[tuple[str, str], tuple[str, str]] = {}  # (doc, symbol_id) -> (key, canonical)
    for row in rows:
        doc, sid, canonical = _clean(row[0]), _clean(row[1]), _clean(row[2])
        key = _symbol_key(canonical)
        if doc in identities and sid and key:
            symbols[(doc, sid)] = (key, canonical)
    linked: dict[tuple[str, str], dict[str, str]] = {}
    for r in link_rows or []:
        if len(r) < 4:
            continue
        doc, sid, entry_id, status = _clean(r[0]), _clean(r[1]), _clean(r[2]), _clean(r[3])
        if entry_id in entry_names and (doc, sid) in symbols:
            linked.setdefault((doc, sid), {})[entry_id] = status
    for (doc, sid), (key, canonical) in sorted(symbols.items()):
        mine = linked.get((doc, sid)) or {}
        for (o_doc, o_sid), (o_key, o_canonical) in sorted(symbols.items()):
            if o_doc == doc or o_key != key or not same_paper(identities[doc], identities[o_doc]):
                continue
            for entry_id, status in sorted((linked.get((o_doc, o_sid)) or {}).items()):
                if entry_id in mine:
                    continue
                if stats["links_created"] >= limit:
                    stats["truncated"] = True
                    return
                ref = ElementRef(
                    scope=SCOPE_DOCUMENT, element_type=ELEMENT_SYMBOL, element_id=sid, document_id=doc
                )
                try:
                    ref.validate()
                    link = _identity_links.create_candidate(
                        ref,
                        entry_id,
                        local_expression={"name": canonical, "notation": canonical},
                        evidence=[{"basis": "same_paper_version", "twin_symbol": o_canonical}],
                        reason=SYMBOL_SAME_PAPER_REASON.format(
                            symbol=o_canonical or canonical,
                            entry=entry_names.get(entry_id) or "（名前の無い概念）",
                            state=_SAME_PAPER_STATE_LABELS.get(status, status),
                        ),
                        mapping_justification=schema.JUSTIFICATION_LEXICAL,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("symbol same-paper candidate failed (non-fatal)", exc_info=True)
                    continue
                mine[entry_id] = "candidate"
                if (link or {}).get("outcome") == _identity_links.LINK_OUTCOME_CREATED:
                    stats["links_created"] += 1
