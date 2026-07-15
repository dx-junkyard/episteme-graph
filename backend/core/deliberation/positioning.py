"""面②「文脈的位置づけ」の4レンズ合成（設計書 §4）。

Phase 0 スコープは §4.1（論文内）/ §4.3（分野の地図）/ §4.4（承認・疑義）の3レンズのみ。
§4.2（コーパス横断）は Phase 1（chunk-proxy 実装待ち）のため本モジュールには含めない
（``build()`` は常に ``cross_corpus`` キーを持たない）。

各レンズは既存機構を読むだけの非LLM合成（W6 の縮退先そのもの）。新しい抽出・LLM呼び出し・
書き込みは一切行わない。レンズ単位で fail-soft: 1レンズの例外は握って None にし、他レンズ・
overview 全体は引き続き返す。

外形（設計書のレスポンス契約）::

    {
      "intra_document": {"items": [{"label": str, "value": str}, ...]} | None,
      "atlas":          {"items": [...]} | None,
      "endorsement":    {"items": [...]} | None,
      "epistemic":      {"items": [...]} | None,
    }

value は事実文・段階ラベル・語彙ラベルのみ。生の件数・%・生数値は入れない（W8）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text as sa_text

from core import atlas as atlas_module
from core.atlas_store import load_learner_skeleton
from core.postgres import get_session
from core.deliberation import refs as refs_mod
from core.deliberation.schema import (
    ELEMENT_EQUATION,
    ELEMENT_FIGURE,
    ELEMENT_SHARED_PART,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_THEORY_COMPONENT,
    SCOPE_DOCUMENT,
    SCOPE_DOMAIN,
    ElementRef,
)

logger = logging.getLogger(__name__)

# ── 語彙ラベル（生値を返さない・W8） ─────────────────────────────────────
# epistemic_ledger.verification_status（migration 029 の CHECK 語彙をそのまま日本語化）。
_VERIFICATION_STATUS_LABELS = {
    "directly_verified": "直接検証済み",
    "indirectly_supported": "間接的に支持",
    "untested": "未検証",
    "refuted": "反証あり",
    "unknown": "不明",
}

# challenges.challenge_type（migration 031 の CHECK 語彙をそのまま日本語化）。
_CHALLENGE_TYPE_LABELS = {
    "scope_extrapolation": "検証スコープ外への外挿",
    "untested_in_domain": "この領域では未検証",
    "definitional": "定義の曖昧さ・循環",
    "hidden_lemma": "暗黙の補題への依存",
}

# ThesisReconstructionResult.support_structure のセクション名（agent schema.py の
# SUPPORT_SECTIONS）を日本語ラベル化。未知のセクション名はそのまま表示する。
_SUPPORT_SECTION_LABELS = {
    "direct_supports": "直接支持",
    "assumptions": "前提",
    "derivation_core": "導出の核",
    "correction_sources": "訂正の源",
    "uncertainty_sources": "不確実性の源",
    "diagnostic_consequences": "診断的帰結",
    "future_requirements": "将来要件",
}

# epistemic_ledger.target_type への element_type マップ（figure / shared_part は対象外）。
_EPISTEMIC_TARGET_TYPE = {
    ELEMENT_THEORY_CLAIM: "claim",
    ELEMENT_EQUATION: "equation",
    ELEMENT_THEORY_COMPONENT: "component",
}


# ---------------------------------------------------------------------------
# 純粋ヘルパ（DB非依存・fake データで単体テスト可能。test_deliberation_positioning.py 参照）
# ---------------------------------------------------------------------------


def _item(label: str, value: str) -> dict[str, str]:
    return {"label": label, "value": value}


def _dict_list(value: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    items = value.get(key)
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def _sections_by_id(artifacts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """document_structure artifact の sections[] を section_id 索引に変換する。"""
    structure = artifacts.get("document_structure")
    sections = structure.get("sections") if isinstance(structure, dict) else None
    if not isinstance(sections, list):
        return {}
    return {
        str(s["section_id"]): s
        for s in sections
        if isinstance(s, dict) and s.get("section_id")
    }


def _blocks_by_id(artifacts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """document_structure artifact の blocks[] を block_id 索引に変換する。"""
    structure = artifacts.get("document_structure")
    blocks = structure.get("blocks") if isinstance(structure, dict) else None
    if not isinstance(blocks, list):
        return {}
    return {
        str(b["block_id"]): b
        for b in blocks
        if isinstance(b, dict) and b.get("block_id")
    }


def _section_label(section: dict[str, Any] | None) -> str:
    if not section:
        return ""
    return str(section.get("title") or "").strip()


def _chain_position_for_claim(chains: list[dict[str, Any]], claim_id: str) -> str | None:
    """claim が derivation_chain artifact のどこに現れるか（設計書 §4.1）。"""
    for chain in chains:
        did = str(chain.get("derivation_id") or "")
        for step in _dict_list(chain, "steps"):
            sid = str(step.get("step_id") or "")
            if claim_id in [str(x) for x in (step.get("input_claim_ids") or [])]:
                return f"導出チェーン「{did}」のステップ「{sid}」の入力 claim"
            if claim_id in [str(x) for x in (step.get("output_claim_ids") or [])]:
                return f"導出チェーン「{did}」のステップ「{sid}」の出力 claim"
            if claim_id in [str(x) for x in (step.get("required_claim_ids") or [])]:
                return f"導出チェーン「{did}」のステップ「{sid}」が要求する claim"
            if claim_id in [str(x) for x in (step.get("assumption_ids") or [])]:
                return f"導出チェーン「{did}」のステップ「{sid}」の前提 claim"
        if claim_id in [str(x) for x in (chain.get("input_claim_ids") or [])]:
            return f"導出チェーン「{did}」（システム操作）の入力 claim"
        if claim_id in [str(x) for x in (chain.get("output_claim_ids") or [])]:
            return f"導出チェーン「{did}」（システム操作）の出力 claim"
        if claim_id in [str(x) for x in (chain.get("assumption_ids") or [])]:
            return f"導出チェーン「{did}」の前提 claim"
    return None


def _chain_position_for_equation(chains: list[dict[str, Any]], equation_id: str) -> str | None:
    """equation が derivation_chain artifact のどこに現れるか（入出力の有無・設計書 §4.1）。"""
    for chain in chains:
        did = str(chain.get("derivation_id") or "")
        for step in _dict_list(chain, "steps"):
            sid = str(step.get("step_id") or "")
            if equation_id in [str(x) for x in (step.get("input_equation_ids") or [])]:
                return f"導出チェーン「{did}」のステップ「{sid}」の入力式"
            if equation_id in [str(x) for x in (step.get("output_equation_ids") or [])]:
                return f"導出チェーン「{did}」のステップ「{sid}」の出力式"
        if equation_id in [str(x) for x in (chain.get("input_equation_ids") or [])]:
            return f"導出チェーン「{did}」（システム操作）の入力式"
        if equation_id in [str(x) for x in (chain.get("output_equation_ids") or [])]:
            return f"導出チェーン「{did}」（システム操作）の出力式"
        if equation_id in [str(x) for x in (chain.get("intermediate_equation_ids") or [])]:
            return f"導出チェーン「{did}」の中間式"
    return None


def _component_chain_links(chains: list[dict[str, Any]], component_id: str) -> list[str]:
    """component が derivation_chain の linked_component_ids に現れる chain の一覧。"""
    return [
        str(chain.get("derivation_id") or "")
        for chain in chains
        if component_id in [str(x) for x in (chain.get("linked_component_ids") or [])]
    ]


def _thesis_position_for_claim(thesis: dict[str, Any], claim_id: str) -> str | None:
    """claim が thesis_reconstruction artifact のどこに位置づくか（設計書 §4.1）。"""
    central = thesis.get("central_thesis")
    central = central if isinstance(central, dict) else {}
    if claim_id in [str(x) for x in (central.get("claim_ids") or [])]:
        return "中心命題（central thesis）を構成する claim"
    if claim_id in [str(x) for x in (thesis.get("supporting_subclaim_ids") or [])]:
        return "中心命題を支持するサブclaim"
    support = thesis.get("support_structure")
    support = support if isinstance(support, dict) else {}
    for section_name, entries in support.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and claim_id in [str(x) for x in (entry.get("claim_ids") or [])]:
                label = _SUPPORT_SECTION_LABELS.get(str(section_name), str(section_name))
                return f"支持構造「{label}」に位置づく"
    return None


def _thesis_position_for_equation(thesis: dict[str, Any], equation_id: str) -> str | None:
    """equation が thesis_reconstruction artifact のどこに位置づくか（設計書 §4.1）。"""
    central = thesis.get("central_thesis")
    central = central if isinstance(central, dict) else {}
    if equation_id in [str(x) for x in (central.get("equation_ids") or [])]:
        return "中心命題（central thesis）が参照する式"
    support = thesis.get("support_structure")
    support = support if isinstance(support, dict) else {}
    for section_name, entries in support.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and equation_id in [str(x) for x in (entry.get("equation_ids") or [])]:
                label = _SUPPORT_SECTION_LABELS.get(str(section_name), str(section_name))
                return f"支持構造「{label}」が参照する式"
    return None


def _thesis_anchor_for_component(thesis: dict[str, Any], component_id: str) -> str | None:
    """component が thesis の anchor node（グラフ走査の到達点）か（設計書 §4.1）。"""
    anchor_ids = [str(a) for a in (thesis.get("anchor_node_ids") or [])]
    if component_id in anchor_ids:
        return "thesis の anchor node（グラフ走査の到達点）"
    return None


def _atlas_items_from_skeleton(skeleton: Any, labels: list[str]) -> list[dict[str, str]]:
    """骨格概念と要素の概念ラベルを非LLMの文字列正規化マッチで突き合わせる（設計書 §4.3）。

    ``atlas.match_topic_to_concept`` をそのまま再利用する（要素ラベルを "topic" として渡す）。
    """
    concept_by_id = {c.id: c for r in skeleton.regions for c in r.concepts}
    region_by_concept_id = {c.id: r for r in skeleton.regions for c in r.concepts}
    region_by_id = {r.id: r for r in skeleton.regions}

    matched_ids: list[str] = []
    seen: set[str] = set()
    for label in labels:
        matched = atlas_module.match_topic_to_concept({"title": label}, skeleton)
        if matched and matched not in seen:
            seen.add(matched)
            matched_ids.append(matched)

    items: list[dict[str, str]] = []
    for mid in matched_ids:
        concept = concept_by_id.get(mid)
        if concept is not None:
            region = region_by_concept_id.get(mid)
            value = f"{region.label}の「{concept.label}」" if region else concept.label
            items.append(_item("分野の地図での対応", value))
            continue
        region = region_by_id.get(mid)
        if region is not None:
            items.append(_item("分野の地図での対応", f"領域「{region.label}」（概念単位の対応なし）"))
    return items


def _endorsement_items_from_rows(rows: list[tuple[Any, ...]]) -> list[dict[str, str]]:
    """component_explanations + endorsement summary + citation 集約の行から
    C層レンズを組み立てる（設計書 §4.4: 「誰が承認・引用したか」）。

    rows の各要素は ``(kind, shared, review_status, endorser_count, citation_count)``。
    人数・件数は出さず有無のみカテゴリラベルにする（W8。既存の C層一覧
    ``theory_components.py`` の ``citation_count`` は raw int を返すが、W層のレンズは
    W8 に厳密に従い有無ラベルのみに留める）。
    """
    if not rows:
        return [_item("説明・承認", "説明バージョンなし")]
    kinds = {str(r[0]) for r in rows}
    items: list[dict[str, str]] = []
    if "standard" in kinds:
        items.append(_item("標準説明", "あり"))
    if "personal" in kinds:
        items.append(_item("教員の独自解釈", "あり"))
    any_endorsed = any(int(r[3] or 0) > 0 for r in rows)
    items.append(_item("承認", "承認あり" if any_endorsed else "承認なし"))
    any_cited = any(int(r[4] or 0) > 0 if len(r) > 4 else False for r in rows)
    items.append(_item("引用", "引用あり" if any_cited else "引用なし"))
    if any(bool(r[1]) for r in rows):
        items.append(_item("共有設定", "他教員への共有可"))
    return items


def _epistemic_items_from_ledger(
    verification_status: str | None,
    verification_scopes: list[Any] | None,
    challenge_types: list[str],
) -> list[dict[str, str]]:
    """epistemic_ledger + challenges の値から D層レンズを組み立てる（設計書 §4.4）。

    verification_status=None は「台帳未記帳」として1項目返す（fail-closed で None にはしない。
    未記帳も事実）。検証スコープ 0 件は異常ではなく「未記帳」という事実として書く
    （D層 §「スコープ0件は発見」・警告扱いにしない）。
    """
    items: list[dict[str, str]] = []
    if verification_status is None:
        items.append(_item("検証状態", "台帳未記帳"))
    else:
        items.append(
            _item(
                "検証状態",
                _VERIFICATION_STATUS_LABELS.get(str(verification_status), str(verification_status)),
            )
        )
        items.append(
            _item(
                "検証スコープ",
                "検証スコープは未記帳" if not verification_scopes else "検証スコープを記帳済み",
            )
        )
    if challenge_types:
        labels = sorted({_CHALLENGE_TYPE_LABELS.get(t, t) for t in challenge_types})
        items.append(_item("疑義の種類", "、".join(labels)))
    return items


# ---------------------------------------------------------------------------
# DB 読み出しヘルパ（core.postgres.get_session 経由・try/finally で close）
# ---------------------------------------------------------------------------


def _chunk_section_id(chunk_id: str) -> str | None:
    session = get_session()
    try:
        row = session.execute(
            sa_text("SELECT section_id FROM chunks WHERE id = CAST(:id AS uuid) LIMIT 1"),
            {"id": chunk_id},
        ).fetchone()
    finally:
        session.close()
    return str(row[0]) if row and row[0] else None


def _figure_caption_block_id(figure_id: str) -> str | None:
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                "SELECT caption_block_id FROM document_figures WHERE id = CAST(:id AS uuid) LIMIT 1"
            ),
            {"id": figure_id},
        ).fetchone()
    finally:
        session.close()
    return str(row[0]) if row and row[0] else None


def _document_cartridge_id(document_id: str) -> str | None:
    """document の最新 analysis run から cartridge_id を引く（refs.document_run_artifacts と同型）。"""
    if not str(document_id or "").strip():
        return None
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT cartridge_id
                FROM document_analysis_runs
                WHERE document_id = :document_id
                  AND cartridge_id IS NOT NULL AND cartridge_id <> ''
                ORDER BY (status = 'completed') DESC, updated_at DESC
                LIMIT 1
                """
            ),
            {"document_id": document_id},
        ).fetchone()
    finally:
        session.close()
    return str(row[0]) if row and row[0] else None


def _concept_labels_for_element(ref: ElementRef) -> list[str]:
    """要素の概念ラベル候補（非LLM・既存データの読み出しのみ・設計書 §4.3）。

    theory_claim は concepts[]、theory_component は name、shared_part は
    library_entries.name + aliases[]。figure / equation は信頼できる概念ラベルが
    無いため空を返す（呼び出し側で atlas レンズ全体が None になる）。
    """
    if ref.element_type == ELEMENT_THEORY_CLAIM:
        session = get_session()
        try:
            row = session.execute(
                sa_text("SELECT concepts FROM theory_claims WHERE id = CAST(:id AS uuid) LIMIT 1"),
                {"id": ref.element_id},
            ).fetchone()
        finally:
            session.close()
        concepts = row[0] if row and isinstance(row[0], list) else []
        return [str(c).strip() for c in concepts if str(c or "").strip()]

    if ref.element_type == ELEMENT_THEORY_COMPONENT:
        session = get_session()
        try:
            row = session.execute(
                sa_text("SELECT name FROM theory_components WHERE id = CAST(:id AS uuid) LIMIT 1"),
                {"id": ref.element_id},
            ).fetchone()
        finally:
            session.close()
        name = str(row[0] or "").strip() if row else ""
        return [name] if name else []

    if ref.element_type == ELEMENT_SHARED_PART:
        session = get_session()
        try:
            row = session.execute(
                sa_text("SELECT name, aliases FROM library_entries WHERE id = CAST(:id AS uuid) LIMIT 1"),
                {"id": ref.element_id},
            ).fetchone()
        finally:
            session.close()
        if not row:
            return []
        labels = [str(row[0] or "").strip()]
        aliases = row[1] if isinstance(row[1], list) else []
        labels.extend(str(a).strip() for a in aliases if str(a or "").strip())
        return [label for label in labels if label]

    return []


# ---------------------------------------------------------------------------
# §4.1 論文内（intra_document）
# ---------------------------------------------------------------------------


def _build_intra_document_claim(ref: ElementRef, artifacts: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    chunk_id = ref.provenance.get("chunk_id")
    if chunk_id:
        section_id = _chunk_section_id(str(chunk_id))
        section_label = _section_label(_sections_by_id(artifacts).get(section_id or ""))
        if section_label:
            items.append(_item("掲載セクション", section_label))
    chains = _dict_list(artifacts.get("derivation_chain"), "chains")
    chain_pos = _chain_position_for_claim(chains, ref.element_id)
    if chain_pos:
        items.append(_item("導出チェーン上の位置", chain_pos))
    thesis = artifacts.get("thesis_reconstruction")
    thesis_pos = _thesis_position_for_claim(thesis, ref.element_id) if isinstance(thesis, dict) else None
    if thesis_pos:
        items.append(_item("中心命題との関係", thesis_pos))
    return items


def _build_intra_document_component(ref: ElementRef, artifacts: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    thesis = artifacts.get("thesis_reconstruction")
    if isinstance(thesis, dict):
        anchor_note = _thesis_anchor_for_component(thesis, ref.element_id)
        if anchor_note:
            items.append(_item("中心命題との関係", anchor_note))
    chains = _dict_list(artifacts.get("derivation_chain"), "chains")
    linked = _component_chain_links(chains, ref.element_id)
    if linked:
        joined = "」「".join(linked)
        items.append(_item("導出チェーンとの関連", f"導出チェーン「{joined}」に関連づく"))
    return items


def _build_intra_document_equation(ref: ElementRef, artifacts: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    records = refs_mod.equation_records(ref.document_id or "")
    record = next(
        (r for r in records if str(r.get("equation_id") or "") == str(ref.element_id)),
        None,
    )
    if record:
        src = record.get("source_extraction")
        loc = src.get("source_location") if isinstance(src, dict) else None
        section_id = loc.get("section_id") if isinstance(loc, dict) else None
        if section_id:
            section_label = _section_label(_sections_by_id(artifacts).get(str(section_id)))
            if section_label:
                items.append(_item("掲載セクション", section_label))
    chains = _dict_list(artifacts.get("derivation_chain"), "chains")
    chain_pos = _chain_position_for_equation(chains, ref.element_id)
    # 設計書の例示どおり「入出力の有無」を明示する（無い場合もその事実を書く）。
    items.append(_item("導出チェーン上の位置", chain_pos or "どの導出チェーンにも含まれない"))
    thesis = artifacts.get("thesis_reconstruction")
    thesis_pos = _thesis_position_for_equation(thesis, ref.element_id) if isinstance(thesis, dict) else None
    if thesis_pos:
        items.append(_item("中心命題との関係", thesis_pos))
    return items


def _build_intra_document_figure(ref: ElementRef, artifacts: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    caption_block_id = _figure_caption_block_id(ref.element_id)
    if not caption_block_id:
        return items
    block = _blocks_by_id(artifacts).get(str(caption_block_id))
    if not block:
        return items
    section_id = block.get("section_id")
    section_label = _section_label(_sections_by_id(artifacts).get(str(section_id))) if section_id else ""
    page = block.get("page")
    if section_label and page is not None:
        items.append(_item("掲載セクション", f"{section_label}（p.{page}）"))
    elif section_label:
        items.append(_item("掲載セクション", section_label))
    elif page is not None:
        items.append(_item("掲載ページ", f"p.{page}"))
    return items


_INTRA_DOCUMENT_BUILDERS = {
    ELEMENT_THEORY_CLAIM: _build_intra_document_claim,
    ELEMENT_THEORY_COMPONENT: _build_intra_document_component,
    ELEMENT_EQUATION: _build_intra_document_equation,
    ELEMENT_FIGURE: _build_intra_document_figure,
    # shared_part(domain scope) は論文に紐づかないため対象外（設計書 §4.1 は document-scoped 前提）。
}


def _build_intra_document(ref: ElementRef) -> list[dict[str, str]] | None:
    if ref.scope != SCOPE_DOCUMENT:
        return None
    builder = _INTRA_DOCUMENT_BUILDERS.get(ref.element_type)
    if builder is None:
        return None
    artifacts = refs_mod.document_run_artifacts(ref.document_id or "")
    if not artifacts:
        return None
    return builder(ref, artifacts) or None


# ---------------------------------------------------------------------------
# §4.3 分野の地図（atlas）
# ---------------------------------------------------------------------------


def _build_atlas(ref: ElementRef) -> list[dict[str, str]] | None:
    if ref.scope == SCOPE_DOCUMENT:
        cartridge_id = _document_cartridge_id(ref.document_id or "")
    elif ref.scope == SCOPE_DOMAIN:
        # L層: domain_key は cartridge_id と同一名前空間（設計書「分野別ナレッジライブラリ」節）。
        cartridge_id = ref.domain_key
    else:
        cartridge_id = None
    if not cartridge_id:
        return None
    # 骨格の読みは必ず atlas_store.load_learner_skeleton を使う（cartridge 直読み禁止・CLAUDE.md）。
    skeleton = load_learner_skeleton(cartridge_id)
    if skeleton is None:
        # 骨格なし・cartridge 不明 → 地図レンズは非表示（atlas の fail-closed を継承）。
        return None
    labels = _concept_labels_for_element(ref)
    if not labels:
        return None
    return _atlas_items_from_skeleton(skeleton, labels) or None


# ---------------------------------------------------------------------------
# §4.4 承認（C層 endorsement）
# ---------------------------------------------------------------------------


def _build_endorsement(ref: ElementRef) -> list[dict[str, str]] | None:
    if ref.element_type != ELEMENT_THEORY_COMPONENT:
        return None
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT e.kind, e.shared, e.review_status,
                       COALESCE(s.endorser_count, 0) AS endorser_count,
                       COALESCE(c.cnt, 0) AS citation_count
                FROM component_explanations e
                LEFT JOIN component_explanation_endorsement_summary s
                       ON s.explanation_id = e.id
                LEFT JOIN (
                    SELECT explanation_id, COUNT(*) AS cnt
                    FROM component_citations
                    GROUP BY explanation_id
                ) c ON c.explanation_id = e.id
                WHERE e.component_id = CAST(:id AS uuid)
                """
            ),
            {"id": ref.element_id},
        ).fetchall()
    finally:
        session.close()
    return _endorsement_items_from_rows([tuple(r) for r in rows]) or None


# ---------------------------------------------------------------------------
# §4.4 疑義・検証（D層 epistemic）
# ---------------------------------------------------------------------------


def _build_epistemic(ref: ElementRef) -> list[dict[str, str]] | None:
    target_type = _EPISTEMIC_TARGET_TYPE.get(ref.element_type)
    if target_type is None:
        return None
    session = get_session()
    try:
        ledger_row = session.execute(
            sa_text(
                """
                SELECT verification_status, verification_scopes
                FROM epistemic_ledger
                WHERE target_type = :target_type AND target_id = :target_id
                LIMIT 1
                """
            ),
            {"target_type": target_type, "target_id": ref.element_id},
        ).fetchone()
        challenge_rows = []
        # challenges.target_type は 'assumption' / 'claim' のみ（migration 031 の CHECK）。
        if target_type == "claim":
            challenge_rows = session.execute(
                sa_text(
                    """
                    SELECT DISTINCT challenge_type
                    FROM challenges
                    WHERE target_type = :target_type AND target_id = :target_id
                      AND status IN ('open', 'answered')
                    """
                ),
                {"target_type": target_type, "target_id": ref.element_id},
            ).fetchall()
    finally:
        session.close()

    verification_status = str(ledger_row[0]) if ledger_row else None
    verification_scopes = ledger_row[1] if ledger_row and isinstance(ledger_row[1], list) else []
    challenge_types = [str(r[0]) for r in challenge_rows]
    items = _epistemic_items_from_ledger(verification_status, verification_scopes, challenge_types)
    return items or None


# ---------------------------------------------------------------------------
# 合成
# ---------------------------------------------------------------------------


def _safe_lens(name: str, builder: Any, ref: ElementRef) -> dict[str, Any] | None:
    """レンズ単位 fail-soft（設計書 §4 冒頭・W6）。例外は握って None にし、他レンズへ波及させない。"""
    try:
        items = builder(ref)
    except Exception:  # noqa: BLE001
        logger.warning(
            "deliberation positioning lens '%s' failed for %s:%s",
            name,
            ref.element_type,
            ref.element_id,
            exc_info=True,
        )
        return None
    if not items:
        return None
    return {"items": items}


def build(ref: ElementRef) -> dict[str, Any]:
    """ElementRef の面②位置づけ（Phase 0: §4.1 / §4.3 / §4.4）を合成して返す。"""
    return {
        "intra_document": _safe_lens("intra_document", _build_intra_document, ref),
        "atlas": _safe_lens("atlas", _build_atlas, ref),
        "endorsement": _safe_lens("endorsement", _build_endorsement, ref),
        "epistemic": _safe_lens("epistemic", _build_epistemic, ref),
    }
