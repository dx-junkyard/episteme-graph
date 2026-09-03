"""グラフの論文層 — 読み時射影の本体（純関数）。

設計: ``docs/features/graph_paper_layer_design.md`` §3（DTO 契約）/ §3.1（結び付け
規則）/ §3.2（被覆）。

``build_paper_layer`` は **DB にも LLM にも触れない**。入力は
``routes/theory_components.py`` が組み立てた graph payload（``reference_index`` 付き）と
``document_analysis_runs.stage_outputs["_artifacts"]``、それに小さな DB 行の list
（``document_figures`` / ``element_explanations``）だけで、出力は §3 の dict である。

不変条項の実装上の要点:

- PL1/PL8: 入力 dict を**書き換えない**（全て新しい dict を組む）。artifact の欠落は
  例外にせず、その部品を空にして ``facts`` に事実文を1行足す。
- PL3: 章への結び付けは実所在のみ（式の ``source_location.section_id`` → evidence の
  ``source.section_id`` → claim の ``section_id`` → ``block_id`` → 未特定）。
  名寄せ・類似度・見出し推定はしない。
- PL4/PL7: ``confidence`` 等の生数値と内部 ID を DTO の表示面に出さない。
"""

from __future__ import annotations

from typing import Any, Iterable

from core.label_vocab import SUPPORT_SECTION_LABELS

from core.graph_paper_layer.schema import (
    EQUATION_ROLE_LINKED,
    EQUATION_ROLE_NODE_KEYS,
    EXPLANATION_STATUS_PRIORITY,
    MISSING_ARTIFACT_FACTS,
    NODE_CLAIM_ID_KEYS,
    NODE_COMPONENT_REF_KEYS,
    NODE_COMPONENT_REF_LIST_KEYS,
    NODE_MEMBER_ID_KEYS,
    SYMBOL_ROLE_NODE_KEYS,
    TEXT_SNIPPET_MAX,
    UNBOUND_CLAIM_SUPPORT_STATUS,
    FACT_NO_GRAPH,
    equation_body,
    equation_display_label,
    figure_display_label,
    normalize_figure_join_key,
    thesis_ref_for,
    truncate_snippet,
)

_SUPPORT_SECTION_LABELS = SUPPORT_SECTION_LABELS


# ---------------------------------------------------------------------------
# 小さなユーティリティ
# ---------------------------------------------------------------------------


def _dicts(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _id_list(container: Any, key: str) -> list[str]:
    values = _mapping(container).get(key)
    if not isinstance(values, list):
        return []
    return [str(v) for v in values if str(v or "").strip()]


def _node_id(node: dict) -> str:
    return str(node.get("id") or node.get("component_id") or "").strip()


def _dedup(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _label_sort_key(label: str) -> tuple:
    """印字番号の自然順（``2`` < ``10``、``3.2`` < ``3.10``）。番号なしは末尾。"""
    text = str(label or "").strip()
    if not text:
        return (1, ())
    parts: list[tuple[int, Any]] = []
    for chunk in text.replace("-", ".").split("."):
        if chunk.isdigit():
            parts.append((0, int(chunk)))
        else:
            parts.append((1, chunk))
    return (0, tuple(parts))


# ---------------------------------------------------------------------------
# artifact の索引化
# ---------------------------------------------------------------------------


class _PaperIndex:
    """artifact 群を「読むための索引」に決定論的に畳む（保存しない）。"""

    def __init__(self, artifacts: dict, figure_rows: list[dict], explanation_rows: list[dict]):
        self.artifacts = _mapping(artifacts)

        structure = _mapping(self.artifacts.get("document_structure"))
        self.metadata = _mapping(structure.get("metadata"))
        self.sections: list[dict] = []
        self.sections_by_id: dict[str, dict] = {}
        for order_fallback, section in enumerate(_dicts(structure.get("sections"))):
            section_id = str(section.get("section_id") or "").strip()
            if not section_id or section_id in self.sections_by_id:
                continue
            entry = {
                "section_id": section_id,
                "title": str(section.get("title") or ""),
                "level": int(section.get("level") or 1),
                "order": int(section.get("order") if section.get("order") is not None else order_fallback),
                "page_start": section.get("page_start"),
                "page_end": section.get("page_end"),
                "parent_section_id": str(section.get("parent_section_id") or "") or None,
            }
            self.sections.append(entry)
            self.sections_by_id[section_id] = entry
        self.sections.sort(key=lambda s: (s["order"], s["section_id"]))
        self.section_rank = {s["section_id"]: idx for idx, s in enumerate(self.sections)}

        self.block_section: dict[str, str] = {}
        for block in _dicts(structure.get("blocks")):
            block_id = str(block.get("block_id") or "").strip()
            section_id = str(block.get("section_id") or "").strip()
            if block_id and section_id:
                self.block_section.setdefault(block_id, section_id)

        # 式（ネスト形のみ受理。§6 非スコープ: フラット形 export は受けない）。
        self.equations: dict[str, dict] = {}
        for record in _dicts(_mapping(self.artifacts.get("equation_semantics")).get("equations")):
            equation_id = str(record.get("equation_id") or "").strip()
            if equation_id:
                self.equations.setdefault(equation_id, record)

        self.evidence: dict[str, dict] = {}
        for record in _dicts(_mapping(self.artifacts.get("evidence_registry")).get("records")):
            evidence_id = str(record.get("evidence_id") or "").strip()
            if evidence_id:
                self.evidence.setdefault(evidence_id, record)

        self.claim_objects: dict[str, dict] = {}
        for record in _dicts(_mapping(self.artifacts.get("claim_object_builder")).get("claims")):
            claim_id = str(record.get("claim_id") or "").strip()
            if claim_id:
                self.claim_objects.setdefault(claim_id, record)

        self.symbols: dict[str, dict] = {}
        for record in _dicts(_mapping(self.artifacts.get("symbol_registry")).get("records")):
            keys = [str(record.get("canonical_symbol") or "")]
            keys.extend(str(v) for v in (record.get("notation_variants") or []) if v)
            for key in keys:
                if key.strip():
                    self.symbols.setdefault(key.strip(), record)
                    self.symbols.setdefault(key.strip().casefold(), record)

        self.derivations: dict[str, dict] = {}
        self.step_to_derivation: dict[str, str] = {}
        for chain in _dicts(_mapping(self.artifacts.get("derivation_chain")).get("chains")):
            derivation_id = str(chain.get("derivation_id") or "").strip()
            if not derivation_id:
                continue
            self.derivations.setdefault(derivation_id, chain)
            for step in _dicts(chain.get("steps")):
                step_id = str(step.get("step_id") or "").strip()
                if step_id:
                    self.step_to_derivation.setdefault(step_id, derivation_id)

        figure_stage = _mapping(self.artifacts.get("figure_table_semantics"))
        self.figures = [r for r in _dicts(figure_stage.get("figures")) if str(r.get("figure_id") or "").strip()]
        self.tables = [r for r in _dicts(figure_stage.get("tables")) if str(r.get("table_id") or "").strip()]

        # document_figures 行（figure_key の表記ゆれを吸収して join する）。
        self.figure_rows_by_key: dict[str, dict] = {}
        for row in _dicts(figure_rows):
            key = normalize_figure_join_key(row.get("figure_key"))
            if key:
                self.figure_rows_by_key.setdefault(key, row)

        self.skeleton = _mapping(self.artifacts.get("paper_skeleton"))
        self.thesis = _mapping(self.artifacts.get("thesis_reconstruction"))

        self.components: dict[str, dict] = {}
        for record in _dicts(_mapping(self.artifacts.get("component_assembly")).get("components")):
            component_id = str(record.get("component_id") or "").strip()
            if component_id:
                self.components.setdefault(component_id, record)

        # contextual 説明（approved 優先）。
        self.explanations: dict[str, dict] = {}
        for row in _dicts(explanation_rows):
            element_id = str(row.get("element_id") or "").strip()
            body = str(row.get("body") or "")
            status = str(row.get("status") or "")
            if not element_id or not body:
                continue
            current = self.explanations.get(element_id)
            if current is None or _explanation_rank(status) < _explanation_rank(current["status"]):
                self.explanations[element_id] = {"body": body, "status": status}

        # thesis 上の役割: claim agent ID → [{thesis_ref, section_label, text}]
        self.thesis_roles: dict[str, list[dict]] = {}
        self.central_thesis_claim_ids: list[str] = []
        for entry in self._thesis_entries():
            for claim_id in entry["claim_ids"]:
                bucket = self.thesis_roles.setdefault(claim_id, [])
                if not any(e["thesis_ref"] == entry["ref"]["thesis_ref"] for e in bucket):
                    bucket.append(entry["ref"])

    # -- thesis ------------------------------------------------------------

    def _thesis_entries(self) -> list[dict]:
        """``persistence._thesis_ref_nodes`` と同じ規約で thesis ノードを平坦化する。"""
        if not self.thesis:
            return []
        entries: list[dict] = []
        central = _mapping(self.thesis.get("central_thesis"))
        central_claim_ids = _id_list(central, "claim_ids")
        self.central_thesis_claim_ids = central_claim_ids
        entries.append({
            "claim_ids": central_claim_ids,
            "ref": {
                "thesis_ref": thesis_ref_for(None, None),
                "section_label": "",
                "text": truncate_snippet(central.get("text")),
            },
        })
        support = _mapping(self.thesis.get("support_structure"))
        for section, items in support.items():
            for idx, item in enumerate(_dicts(items)):
                entries.append({
                    "claim_ids": _id_list(item, "claim_ids"),
                    "ref": {
                        "thesis_ref": thesis_ref_for(str(section), idx),
                        "section_label": _SUPPORT_SECTION_LABELS.get(str(section), str(section)),
                        "text": truncate_snippet(item.get("text")),
                    },
                })
        return entries

    # -- 章の解決（PL3） ----------------------------------------------------

    def resolve_section(self, direct_section_id: Any, block_id: Any) -> str:
        """実所在から章を決める。直接 → block_id → 未特定（空文字）。"""
        direct = str(direct_section_id or "").strip()
        if direct:
            return direct
        block = str(block_id or "").strip()
        if block:
            return self.block_section.get(block, "")
        return ""

    def section_entry(self, section_id: str) -> dict:
        known = self.sections_by_id.get(section_id)
        if known:
            return {
                "section_id": known["section_id"],
                "title": known["title"],
                "page_start": known["page_start"],
            }
        # document_structure が無い / 未知の章 ID。捏造せず ID だけ返す。
        return {"section_id": section_id, "title": "", "page_start": None}

    def section_sort_key(self, section_id: str) -> tuple:
        return (self.section_rank.get(section_id, len(self.sections)), section_id)

    # -- 要素の章 -----------------------------------------------------------

    def equation_section(self, equation_id: str) -> str:
        body = equation_body(self.equations.get(equation_id))
        return self.resolve_section(body["section_id"], body["block_id"])

    def evidence_section(self, evidence_id: str) -> str:
        source = _mapping(_mapping(self.evidence.get(evidence_id)).get("source"))
        return self.resolve_section(source.get("section_id"), source.get("block_id"))

    def claim_section(self, agent_id: str) -> str:
        record = self.claim_objects.get(agent_id)
        if not record:
            return ""
        direct = self.resolve_section(record.get("section_id"), None)
        if direct:
            return direct
        for evidence_id in _id_list(record, "source_evidence_ids"):
            section_id = self.evidence_section(evidence_id)
            if section_id:
                return section_id
        return ""


def _explanation_rank(status: str) -> int:
    try:
        return EXPLANATION_STATUS_PRIORITY.index(str(status or ""))
    except ValueError:
        return len(EXPLANATION_STATUS_PRIORITY)


# ---------------------------------------------------------------------------
# ノードの「論文側の顔」
# ---------------------------------------------------------------------------


def _node_own_refs(node: dict) -> dict:
    """1ノードが直接持つ論文への指し先（役割つき）。"""
    equations: list[tuple[str, str]] = []
    seen_equations: set[str] = set()
    for role, key in EQUATION_ROLE_NODE_KEYS:
        for equation_id in _id_list(node, key):
            if equation_id in seen_equations:
                continue
            seen_equations.add(equation_id)
            equations.append((equation_id, role))
    claims: list[str] = []
    for key in NODE_CLAIM_ID_KEYS:
        claims.extend(_id_list(node, key))
    symbols: list[tuple[str, str]] = []
    seen_symbols: set[str] = set()
    for role, key in SYMBOL_ROLE_NODE_KEYS:
        for symbol in _id_list(node, key):
            if symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            symbols.append((symbol, role))
    return {
        "equations": equations,
        "claims": _dedup(claims),
        "evidence": _dedup(_id_list(node, "linked_evidence_ids")),
        "derivations": _dedup(_id_list(node, "linked_derivation_ids")),
        "symbols": symbols,
    }


def _merge_refs(refs: list[dict]) -> dict:
    """main ノード = 自身 ∪ member detail ノードの指し先（重複除去・先勝ち）。"""
    equations: list[tuple[str, str]] = []
    seen_equations: set[str] = set()
    symbols: list[tuple[str, str]] = []
    seen_symbols: set[str] = set()
    claims: list[str] = []
    evidence: list[str] = []
    derivations: list[str] = []
    for ref in refs:
        for equation_id, role in ref["equations"]:
            if equation_id in seen_equations:
                continue
            seen_equations.add(equation_id)
            equations.append((equation_id, role))
        for symbol, role in ref["symbols"]:
            if symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            symbols.append((symbol, role))
        claims.extend(ref["claims"])
        evidence.extend(ref["evidence"])
        derivations.extend(ref["derivations"])
    return {
        "equations": equations,
        "claims": _dedup(claims),
        "evidence": _dedup(evidence),
        "derivations": _dedup(derivations),
        "symbols": symbols,
    }


def _component_ref_candidates(node: dict) -> list[str]:
    candidates: list[str] = []
    for key in NODE_COMPONENT_REF_KEYS:
        value = str(node.get(key) or "").strip()
        if value:
            candidates.append(value)
    for key in NODE_COMPONENT_REF_LIST_KEYS:
        candidates.extend(_id_list(node, key))
    return _dedup(candidates)


def _equation_items(index: _PaperIndex, refs: dict) -> list[dict]:
    items: list[dict] = []
    for equation_id, role in refs["equations"]:
        record = index.equations.get(equation_id)
        body = equation_body(record)
        section_id = index.resolve_section(body["section_id"], body["block_id"])
        items.append({
            "equation_id": equation_id,
            "display_label": equation_display_label(record) if record else "番号なし",
            "latex": body["latex"],
            "plain_text": body["plain_text"],
            "role": role if role in {r for r, _ in EQUATION_ROLE_NODE_KEYS} else EQUATION_ROLE_LINKED,
            "section_id": section_id,
            "page": body["page"],
            "needs_math_review": body["needs_math_review"],
        })
    items.sort(key=lambda item: (
        index.section_sort_key(item["section_id"]),
        _label_sort_key(_equation_label(index, item["equation_id"])),
        item["equation_id"],
    ))
    return items


def _equation_label(index: _PaperIndex, equation_id: str) -> str:
    record = index.equations.get(equation_id)
    return str(_mapping(record).get("label") or "")


def _claim_items(index: _PaperIndex, reference_claims: dict, refs: dict) -> list[dict]:
    items: list[dict] = []
    for agent_id in refs["claims"]:
        entry = _mapping(reference_claims.get(agent_id))
        record = index.claim_objects.get(agent_id)
        text = str(entry.get("text") or "")
        if not text and record:
            text = str(record.get("text") or record.get("normalized_text") or "")
        is_atomic = entry.get("is_atomic")
        if is_atomic is None and record is not None:
            is_atomic = record.get("is_atomic")
        items.append({
            "agent_id": agent_id,
            "claim_id": str(entry.get("claim_id") or ""),
            "text": truncate_snippet(text),
            "review_status": str(entry.get("review_status") or ""),
            "resolution": str(entry.get("resolution") or ""),
            "section_id": index.claim_section(agent_id),
            "is_atomic": bool(is_atomic),
        })
    return items


def _evidence_items(index: _PaperIndex, reference_evidence: dict, refs: dict) -> list[dict]:
    items: list[dict] = []
    for evidence_id in refs["evidence"]:
        record = index.evidence.get(evidence_id)
        fallback = _mapping(reference_evidence.get(evidence_id))
        source = _mapping(_mapping(record).get("source"))
        block_id = str(source.get("block_id") or fallback.get("block_id") or "")
        text = str(_mapping(record).get("evidence_text") or fallback.get("text") or "")
        items.append({
            "evidence_id": evidence_id,
            "text": truncate_snippet(text),
            "section_id": index.resolve_section(source.get("section_id"), block_id),
            "page": source.get("page"),
            "block_id": block_id,
            "role": str(_mapping(record).get("evidence_role") or ""),
        })
    return items


def _figure_items(index: _PaperIndex, claim_ids: set[str]) -> tuple[list[dict], list[dict]]:
    figures: list[dict] = []
    for record in index.figures:
        linked = set(_id_list(record, "linked_claim_ids"))
        shared = sorted(linked & claim_ids)
        if not shared:
            continue
        figure_id = str(record.get("figure_id") or "")
        row = index.figure_rows_by_key.get(normalize_figure_join_key(figure_id))
        location = _mapping(record.get("source_location"))
        figures.append({
            "figure_id": figure_id,
            "db_id": str(_mapping(row).get("id") or "") or None,
            "display_label": figure_display_label(
                figure_id,
                figure_label=_mapping(row).get("figure_label"),
                caption=record.get("caption"),
                kind="figure",
            ),
            "caption": truncate_snippet(record.get("caption")),
            "page": location.get("page") if location.get("page") is not None else _mapping(row).get("page"),
            "via_claim_ids": shared,
        })
    tables: list[dict] = []
    for record in index.tables:
        linked = set(_id_list(record, "linked_claim_ids"))
        shared = sorted(linked & claim_ids)
        if not shared:
            continue
        table_id = str(record.get("table_id") or "")
        location = _mapping(record.get("source_location"))
        tables.append({
            "table_id": table_id,
            "display_label": figure_display_label(table_id, caption=record.get("caption"), kind="table"),
            "caption": truncate_snippet(record.get("caption")),
            "page": location.get("page"),
            "via_claim_ids": shared,
        })
    return figures, tables


def _symbol_items(index: _PaperIndex, refs: dict) -> list[dict]:
    items: list[dict] = []
    for symbol, role in refs["symbols"]:
        record = index.symbols.get(symbol) or index.symbols.get(symbol.casefold())
        quotes = _mapping(record).get("definition_evidence_texts")
        definition_quote = ""
        if isinstance(quotes, list) and quotes:
            definition_quote = truncate_snippet(quotes[0])
        labels: list[str] = []
        for equation_id in _id_list(record, "defining_equation_ids"):
            equation = index.equations.get(equation_id)
            if equation is not None:
                labels.append(equation_display_label(equation))
        items.append({
            "symbol": symbol,
            "kind": str(_mapping(record).get("kind") or ""),
            "role": role,
            "definition_quote": definition_quote,
            "defining_equation_labels": _dedup(labels),
        })
    return items


def _derivation_items(index: _PaperIndex, refs: dict) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for ref_id in refs["derivations"]:
        derivation_id = ref_id if ref_id in index.derivations else index.step_to_derivation.get(ref_id, "")
        if not derivation_id or derivation_id in seen:
            continue
        seen.add(derivation_id)
        chain = index.derivations.get(derivation_id)
        if chain is None:
            continue
        steps: list[dict] = []
        for step in _dicts(chain.get("steps")):
            steps.append({
                "step_id": str(step.get("step_id") or ""),
                "operation": str(step.get("operation") or ""),
                "input_labels": _equation_labels(index, _id_list(step, "input_equation_ids")),
                "output_labels": _equation_labels(index, _id_list(step, "output_equation_ids")),
                # PL4: step の reason は非LLM の決定論的な理由文なので残す
                # （claim / evidence / figure の LLM reason は載せない）。
                "reason": str(step.get("reason") or ""),
            })
        items.append({
            "derivation_id": derivation_id,
            "operation": str(chain.get("operation") or ""),
            "chain_type": str(chain.get("chain_type") or ""),
            "steps": steps,
        })
    return items


def _equation_labels(index: _PaperIndex, equation_ids: list[str]) -> list[str]:
    labels: list[str] = []
    for equation_id in equation_ids:
        record = index.equations.get(equation_id)
        if record is not None:
            labels.append(equation_display_label(record))
    return _dedup(labels)


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------


def build_paper_layer(
    graph: dict,
    artifacts: dict,
    *,
    figure_rows: list[dict] | None = None,
    explanation_rows: list[dict] | None = None,
) -> dict:
    """論文層 DTO（設計 §3）を組み立てる。入力は一切 mutate しない。"""
    graph_payload = _mapping(graph)
    nodes = [n for n in _dicts(graph_payload.get("nodes")) if _node_id(n)]
    index = _PaperIndex(_mapping(artifacts), _dicts(figure_rows), _dicts(explanation_rows))

    facts: list[str] = []
    for stage, collection_key, fact in MISSING_ARTIFACT_FACTS:
        stage_payload = _mapping(index.artifacts.get(stage))
        collection = stage_payload.get(collection_key)
        present = bool(collection) if not isinstance(collection, dict) else bool(collection.get("text"))
        if not present:
            facts.append(fact)

    document_id = str(graph_payload.get("document_id") or "")
    graph_updated_at = str(graph_payload.get("graph_updated_at") or "") or None
    narrative = _mapping(graph_payload.get("narrative"))
    node_narratives = _mapping(narrative.get("node_narratives"))
    edge_narratives = _mapping(narrative.get("edge_narratives"))
    reference_index = _mapping(graph_payload.get("reference_index"))
    reference_claims = _mapping(reference_index.get("claims"))
    reference_evidence = _mapping(reference_index.get("evidence"))

    if not nodes:
        facts.insert(0, FACT_NO_GRAPH)
        return {
            "document_id": document_id,
            "available": False,
            "facts": facts,
            "graph_updated_at": graph_updated_at,
            "paper": _empty_paper(index),
            "nodes": {},
            "edges": {},
            "coverage": {
                "unbound_sections": [],
                "unbound_equations": [],
                "unbound_figures": [],
                "unbound_claims": [],
            },
            "narrative": {"graph_summary": str(narrative.get("graph_summary") or "")},
        }

    nodes_by_id = {_node_id(node): node for node in nodes}
    own_refs = {node_id: _node_own_refs(node) for node_id, node in nodes_by_id.items()}

    node_dtos: dict[str, dict] = {}
    section_nodes: dict[str, list[str]] = {}
    bound_equation_ids: set[str] = set()
    bound_claim_ids: set[str] = set()

    for node in sorted(nodes, key=lambda n: (int(n.get("display_order") or 0), _node_id(n))):
        node_id = _node_id(node)
        graph_layer = str(node.get("graph_layer") or "main")
        member_ids = _dedup(
            member_id
            for key in NODE_MEMBER_ID_KEYS
            for member_id in _id_list(node, key)
            if member_id in nodes_by_id and member_id != node_id
        )
        refs = _merge_refs([own_refs[node_id]] + [own_refs[m] for m in member_ids])

        equations = _equation_items(index, refs)
        claims = _claim_items(index, reference_claims, refs)
        evidence = _evidence_items(index, reference_evidence, refs)
        claim_id_set = {c["agent_id"] for c in claims}
        figures, tables = _figure_items(index, claim_id_set)
        symbols = _symbol_items(index, refs)
        derivations = _derivation_items(index, refs)

        bound_equation_ids.update(item["equation_id"] for item in equations)
        bound_claim_ids.update(claim_id_set)

        section_ids = _dedup(
            [item["section_id"] for item in equations if item["section_id"]]
            + [item["section_id"] for item in evidence if item["section_id"]]
            + [item["section_id"] for item in claims if item["section_id"]]
        )
        section_ids.sort(key=index.section_sort_key)
        for section_id in section_ids:
            section_nodes.setdefault(section_id, []).append(node_id)

        thesis_roles: list[dict] = []
        seen_refs: set[str] = set()
        for agent_id in refs["claims"]:
            for role in index.thesis_roles.get(agent_id, []):
                if role["thesis_ref"] in seen_refs:
                    continue
                seen_refs.add(role["thesis_ref"])
                thesis_roles.append(dict(role))

        component = None
        explanation = None
        candidates = _component_ref_candidates(node)
        for member_id in member_ids:
            candidates.extend(_component_ref_candidates(nodes_by_id[member_id]))
        for candidate in _dedup(candidates):
            if component is None and candidate in index.components:
                record = index.components[candidate]
                component = {
                    "summary": str(record.get("summary") or ""),
                    "teaching_takeaway": str(record.get("teaching_takeaway") or ""),
                    "role_in_thesis": str(record.get("role_in_thesis") or ""),
                }
            if explanation is None and candidate in index.explanations:
                explanation = dict(index.explanations[candidate])
            if component is not None and explanation is not None:
                break

        node_dtos[node_id] = {
            "node_id": node_id,
            "graph_layer": graph_layer,
            "label": str(node.get("label") or ""),
            "narrative_role": str(_mapping(node_narratives.get(node_id)).get("narrative_role") or ""),
            "component": component,
            "explanation": explanation,
            "thesis_roles": thesis_roles,
            "sections": [index.section_entry(section_id) for section_id in section_ids],
            "equations": equations,
            "claims": claims,
            "evidence": evidence,
            "figures": figures,
            "tables": tables,
            "symbols": symbols,
            "derivations": derivations,
            "unlocated": not section_ids,
        }

    edges = _build_edges(graph_payload, index, edge_narratives)
    paper = _build_paper(index, section_nodes, bound_claim_ids, reference_claims, node_dtos)
    coverage = _build_coverage(index, section_nodes, bound_equation_ids, bound_claim_ids, reference_claims)

    return {
        "document_id": document_id,
        "available": True,
        "facts": facts,
        "graph_updated_at": graph_updated_at,
        "paper": paper,
        "nodes": node_dtos,
        "edges": edges,
        "coverage": coverage,
        "narrative": {"graph_summary": str(narrative.get("graph_summary") or "")},
    }


def _build_edges(graph_payload: dict, index: _PaperIndex, edge_narratives: dict) -> dict:
    edges: dict[str, dict] = {}
    for edge in _dicts(graph_payload.get("edges")):
        source = str(edge.get("source_component_id") or edge.get("from") or "").strip()
        target = str(edge.get("target_component_id") or edge.get("to") or "").strip()
        edge_id = str(edge.get("edge_id") or edge.get("id") or "").strip()
        if not edge_id:
            if not source or not target:
                continue
            edge_id = f"{source}->{target}"
        equation_ids = _id_list(edge, "evidence_equation_ids")
        if not equation_ids:
            equation_ids = _id_list(edge.get("evidence"), "evidence_equation_ids")
        labels = _equation_labels(index, equation_ids)
        transition_text = str(_mapping(edge_narratives.get(edge_id)).get("transition_text") or "")
        if not transition_text and not labels:
            continue
        edges[edge_id] = {"transition_text": transition_text, "equation_labels": labels}
    return edges


def _empty_paper(index: _PaperIndex) -> dict:
    return {
        "title": str(index.metadata.get("title") or ""),
        "goal": None,
        "central_question": None,
        "central_thesis": None,
        "sections": [],
        "backbone": [],
    }


def _build_paper(
    index: _PaperIndex,
    section_nodes: dict[str, list[str]],
    bound_claim_ids: set[str],
    reference_claims: dict,
    node_dtos: dict[str, dict],
) -> dict:
    goal = str(_mapping(index.skeleton.get("paper_goal")).get("text") or "") or None
    central_question = (
        str(index.thesis.get("central_question") or "").strip()
        or str(_mapping(index.skeleton.get("central_question")).get("text") or "").strip()
        or None
    )

    central_thesis = None
    central = _mapping(index.thesis.get("central_thesis"))
    if central.get("text") or index.central_thesis_claim_ids:
        claim_uuids = _dedup(
            str(_mapping(reference_claims.get(agent_id)).get("claim_id") or "")
            for agent_id in index.central_thesis_claim_ids
        )
        node_ids = sorted(
            node_id
            for node_id, dto in node_dtos.items()
            if any(role["thesis_ref"] == "central_thesis" for role in dto["thesis_roles"])
        )
        central_thesis = {
            "text": truncate_snippet(central.get("text")),
            "claim_ids": [uuid for uuid in claim_uuids if uuid],
            "node_ids": node_ids,
        }

    # 章ごとの要素配置（論文順）。載せるのは**所在の分かった要素だけ**（PL3）。
    equations_by_section: dict[str, list[dict]] = {}
    for equation_id, record in index.equations.items():
        section_id = index.equation_section(equation_id)
        if not section_id:
            continue
        equations_by_section.setdefault(section_id, []).append({
            "equation_id": equation_id,
            "display_label": equation_display_label(record),
            "node_ids": sorted(
                node_id
                for node_id, dto in node_dtos.items()
                if any(item["equation_id"] == equation_id for item in dto["equations"])
            ),
            "_label": str(record.get("label") or ""),
        })
    for items in equations_by_section.values():
        items.sort(key=lambda item: (_label_sort_key(item["_label"]), item["equation_id"]))
        for item in items:
            item.pop("_label", None)

    figures_by_section: dict[str, list[dict]] = {}
    tables_by_section: dict[str, list[dict]] = {}
    for record in index.figures:
        figure_id = str(record.get("figure_id") or "")
        location = _mapping(record.get("source_location"))
        section_id = index.resolve_section(location.get("section_id"), location.get("caption_block_id"))
        if not section_id:
            continue
        row = index.figure_rows_by_key.get(normalize_figure_join_key(figure_id))
        figures_by_section.setdefault(section_id, []).append({
            "figure_id": figure_id,
            "display_label": figure_display_label(
                figure_id,
                figure_label=_mapping(row).get("figure_label"),
                caption=record.get("caption"),
                kind="figure",
            ),
            "node_ids": sorted(
                node_id
                for node_id, dto in node_dtos.items()
                if any(item["figure_id"] == figure_id for item in dto["figures"])
            ),
        })
    for record in index.tables:
        table_id = str(record.get("table_id") or "")
        location = _mapping(record.get("source_location"))
        section_id = index.resolve_section(location.get("section_id"), location.get("caption_block_id"))
        if not section_id:
            continue
        tables_by_section.setdefault(section_id, []).append({
            "table_id": table_id,
            "display_label": figure_display_label(table_id, caption=record.get("caption"), kind="table"),
            "node_ids": sorted(
                node_id
                for node_id, dto in node_dtos.items()
                if any(item["table_id"] == table_id for item in dto["tables"])
            ),
        })

    claims_by_section: dict[str, list[dict]] = {}
    for agent_id, record in index.claim_objects.items():
        bound = agent_id in bound_claim_ids
        atomic_backed = bool(record.get("is_atomic")) and str(
            record.get("support_status") or ""
        ) == UNBOUND_CLAIM_SUPPORT_STATUS
        if not bound and not atomic_backed:
            continue
        section_id = index.claim_section(agent_id)
        if not section_id:
            continue
        entry = _mapping(reference_claims.get(agent_id))
        text = str(entry.get("text") or record.get("text") or record.get("normalized_text") or "")
        claims_by_section.setdefault(section_id, []).append({
            "claim_id": str(entry.get("claim_id") or ""),
            "agent_id": agent_id,
            "text": truncate_snippet(text),
            "node_ids": sorted(
                node_id
                for node_id, dto in node_dtos.items()
                if any(item["agent_id"] == agent_id for item in dto["claims"])
            ),
        })
    for items in claims_by_section.values():
        items.sort(key=lambda item: item["agent_id"])

    sections: list[dict] = []
    for section in index.sections:
        section_id = section["section_id"]
        sections.append({
            "section_id": section_id,
            "title": section["title"],
            "level": section["level"],
            "order": section["order"],
            "page_start": section["page_start"],
            "page_end": section["page_end"],
            "parent_section_id": section["parent_section_id"],
            "node_ids": _dedup(section_nodes.get(section_id, [])),
            "equations": equations_by_section.get(section_id, []),
            "figures": figures_by_section.get(section_id, []),
            "tables": tables_by_section.get(section_id, []),
            "claims": claims_by_section.get(section_id, []),
        })

    backbone: list[dict] = []
    for block in _dicts(index.skeleton.get("logical_blocks")):
        section_ids = _id_list(block, "section_ids")
        node_ids = _dedup(
            node_id
            for section_id in section_ids
            for node_id in section_nodes.get(section_id, [])
        )
        backbone.append({
            "block_type": str(block.get("block_type") or ""),
            "label": str(block.get("label") or ""),
            "summary": str(block.get("summary") or ""),
            "section_ids": section_ids,
            "node_ids": node_ids,
        })

    return {
        "title": str(index.metadata.get("title") or ""),
        "goal": goal,
        "central_question": central_question,
        "central_thesis": central_thesis,
        "sections": sections,
        "backbone": backbone,
    }


def _build_coverage(
    index: _PaperIndex,
    section_nodes: dict[str, list[str]],
    bound_equation_ids: set[str],
    bound_claim_ids: set[str],
    reference_claims: dict,
) -> dict:
    unbound_sections = [
        {"section_id": section["section_id"], "title": section["title"]}
        for section in index.sections
        if not section_nodes.get(section["section_id"])
    ]

    unbound_equations: list[dict] = []
    for equation_id, record in index.equations.items():
        if equation_id in bound_equation_ids:
            continue
        unbound_equations.append({
            "equation_id": equation_id,
            "display_label": equation_display_label(record),
            "section_id": index.equation_section(equation_id),
            "_label": str(record.get("label") or ""),
        })
    unbound_equations.sort(key=lambda item: (
        index.section_sort_key(item["section_id"]),
        _label_sort_key(item["_label"]),
        item["equation_id"],
    ))
    for item in unbound_equations:
        item.pop("_label", None)

    unbound_figures: list[dict] = []
    for record in index.figures:
        if set(_id_list(record, "linked_claim_ids")) & bound_claim_ids:
            continue
        figure_id = str(record.get("figure_id") or "")
        row = index.figure_rows_by_key.get(normalize_figure_join_key(figure_id))
        unbound_figures.append({
            "figure_id": figure_id,
            "display_label": figure_display_label(
                figure_id,
                figure_label=_mapping(row).get("figure_label"),
                caption=record.get("caption"),
                kind="figure",
            ),
        })

    unbound_claims: list[dict] = []
    for agent_id, record in index.claim_objects.items():
        if agent_id in bound_claim_ids:
            continue
        if not record.get("is_atomic"):
            continue
        if str(record.get("support_status") or "") != UNBOUND_CLAIM_SUPPORT_STATUS:
            continue
        entry = _mapping(reference_claims.get(agent_id))
        text = str(entry.get("text") or record.get("text") or record.get("normalized_text") or "")
        unbound_claims.append({
            "agent_id": agent_id,
            "claim_id": str(entry.get("claim_id") or ""),
            "text": truncate_snippet(text, TEXT_SNIPPET_MAX),
            "section_id": index.claim_section(agent_id),
        })
    unbound_claims.sort(key=lambda item: (index.section_sort_key(item["section_id"]), item["agent_id"]))

    return {
        "unbound_sections": unbound_sections,
        "unbound_equations": unbound_equations,
        "unbound_figures": unbound_figures,
        "unbound_claims": unbound_claims,
    }
