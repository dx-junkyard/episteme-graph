"""Build LLM input for DSLLinkingAgent."""
from __future__ import annotations

from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult
from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult
from episteme_graph.agents.thesis_reconstruction.schema import ThesisReconstructionResult
from episteme_graph.agents.claim_selection import (
    headline_text_for_selection,
    select_claim_rows,
    thesis_claim_id_set,
)
from episteme_graph.agents.equation_selection import (
    linked_equation_ids_from_claims,
    select_equation_indices,
)
from episteme_graph.agents.id_canonicalization import (
    canonicalize_claim_refs,
    legacy_claim_id_for_span,
)

from .schema import DSLLLMInput

_MAX_CLAIMS = 48
_MAX_EQUATIONS = 24
_MAX_THESIS_NODES = 24


class DSLLinkingInputBuilder:
    def build(
        self,
        qualified_claims: ClaimQualificationResult,
        equations: EquationSemanticsResult | None = None,
        thesis: ThesisReconstructionResult | None = None,
        config: dict | None = None,
        claim_objects=None,
    ) -> DSLLLMInput:
        cfg = config or {}
        parent_children = _parent_to_atomic_children(
            list(getattr(claim_objects, "claims", []) or [])
        )
        claim_selection = self._accepted_claims(
            qualified_claims,
            int(cfg.get("max_claims", _MAX_CLAIMS)),
            claim_objects=claim_objects,
            thesis=thesis,
        )
        accepted_claims = claim_selection.selected
        aliases = {
            c["legacy_claim_id"]: c["claim_id"]
            for c in accepted_claims
            if c.get("legacy_claim_id") and c.get("claim_id") != c.get("legacy_claim_id")
        }
        return DSLLLMInput(
            document_id=qualified_claims.document_id,
            accepted_claims=accepted_claims,
            equations=self._equations(
                equations,
                int(cfg.get("max_equations", _MAX_EQUATIONS)),
                linked_equation_ids=_dsl_linked_equation_ids(
                    accepted_claims, claim_objects, thesis
                ),
            ),
            thesis_nodes=self._thesis_nodes(
                thesis,
                int(cfg.get("max_thesis_nodes", _MAX_THESIS_NODES)),
                claim_objects=claim_objects,
                claim_aliases=aliases,
                parent_children=parent_children,
            ),
            excluded_from_core=canonicalize_claim_refs(
                list(thesis.excluded_from_core if thesis else []),
                claim_objects,
                aliases,
            ),
            excluded_from_pipeline_input=claim_selection.excluded,
        )

    @staticmethod
    def _accepted_claims(
        qualified_claims: ClaimQualificationResult,
        limit: int,
        claim_objects=None,
        thesis=None,
    ):
        """主張の入力行を組む（IK-0807 / IK-0719 の dsl_linking 側）。

        claim object があるときは **claim object を母集合**にし、ID は claim object の
        ``claim_id`` 一系統に揃える。原子化された親（atomic な子を持つ親）は子に
        置き換え、親は子が無いときだけ ``atomicity`` を明示して渡す。qualified span は
        block_id・役割・tier を補う材料として突合するだけ。claim object が無い
        単独実行では従来どおり span 行を組み、``atomicity`` を span の atomic 候補から
        正直に付ける（判定できなければ ``unknown``）。
        """
        claims = list(getattr(claim_objects, "claims", []) or [])
        if claims:
            rows = _rows_from_claim_objects(qualified_claims, claims)
        else:
            rows = [_span_row(record) for record in qualified_claims.qualified_spans]
        return select_claim_rows(
            rows,
            limit,
            stage="dsl_linking",
            thesis_claim_ids=_expand_parent_refs(
                thesis_claim_id_set(thesis), _parent_to_atomic_children(claims)
            ),
            headline_text=headline_text_for_selection(thesis=thesis),
        )

    @staticmethod
    def _equations(
        equations: EquationSemanticsResult | None,
        limit: int,
        linked_equation_ids=(),
    ) -> list[dict]:
        if not equations:
            return []
        # 先頭切り捨てをやめ、thesis・主張が参照する式 → 結果の式 → 残りを節単位の
        # 層化サンプリングで選ぶ（正本 ``equation_selection``・J-3）。
        records = list(equations.equations)
        selection = select_equation_indices(
            records, limit, linked_equation_ids=linked_equation_ids
        )
        result = []
        for record in (records[i] for i in selection.indices):
            loc = record.source_extraction.source_location
            sem = record.semantics
            result.append({
                "equation_id": record.equation_id,
                "block_id": loc.get("block_id", ""),
                "section_id": loc.get("section_id", ""),
                "label": record.label,
                "role": sem.equation_type,
                "secondary_roles": sem.secondary_types,
                "summary": sem.summary,
                "defined_symbols": [
                    {
                        "symbol": s.symbol,
                        "definition_status": s.definition_status,
                    }
                    for s in sem.defined_symbols
                ],
                "local_assumptions": sem.assumptions,
                "from_equations": sem.input_equation_ids,
                "confidence": sem.confidence,
            })
        return result

    @staticmethod
    def _thesis_nodes(
        thesis: ThesisReconstructionResult | None,
        limit: int,
        claim_objects=None,
        claim_aliases: dict[str, str] | None = None,
        parent_children: dict[str, list[str]] | None = None,
    ) -> list[dict]:
        if not thesis:
            return []

        def _refs(ids) -> list[str]:
            # 主張 ID は accepted_claims と同じ claim object の ``claim_id`` に揃え、
            # 原子化された親は atomic な子に展開する（IK-0807 / IK-0719）。
            canonical = canonicalize_claim_refs(
                {"claim_ids": list(ids or [])}, claim_objects, claim_aliases
            ).get("claim_ids", [])
            return _expand_parent_refs(canonical, parent_children or {}, ordered=True)

        nodes = [{
            "thesis_ref": "central_thesis",
            "text": thesis.central_thesis.get("text", ""),
            "claim_ids": _refs(thesis.central_thesis.get("claim_ids", [])),
            "equation_ids": thesis.central_thesis.get("equation_ids", []),
            "evidence_block_ids": thesis.central_thesis.get("evidence_block_ids", []),
            "kind": "central_thesis",
            "confidence": thesis.central_thesis.get("confidence", thesis.confidence),
        }]
        for section, entries in (thesis.support_structure or {}).items():
            if not isinstance(entries, list):
                continue
            for idx, entry in enumerate(entries):
                nodes.append({
                    "thesis_ref": f"support:{section}:{idx}",
                    "text": entry.get("text", ""),
                    "claim_ids": _refs(entry.get("claim_ids", [])),
                    "equation_ids": entry.get("equation_ids", []),
                    "kind": section,
                    "support_type": entry.get("support_type"),
                    "confidence": entry.get("confidence", 0.0),
                })
                if len(nodes) >= limit:
                    return nodes
        return nodes[:limit]


def _dsl_linked_equation_ids(accepted_claims, claim_objects, thesis) -> set[str]:
    """thesis（中心命題・支持構造）と入力に載る主張が参照する式 ID。"""
    linked = linked_equation_ids_from_claims(
        (row.get("claim_id") for row in accepted_claims or []), claim_objects
    )
    if thesis is not None:
        for eid in (thesis.central_thesis or {}).get("equation_ids", []) or []:
            if eid:
                linked.add(str(eid))
        for entries in (thesis.support_structure or {}).values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    for eid in entry.get("equation_ids", []) or []:
                        if eid:
                            linked.add(str(eid))
    return linked


def _claim_is_non_atomic(claim: object) -> bool:
    atomicity = str(getattr(claim, "atomicity", "atomic") or "atomic").lower()
    is_atomic = bool(getattr(claim, "is_atomic", atomicity == "atomic"))
    return atomicity in {
        "composite",
        "split_required",
        "compound",
        "non_atomic",
        "split_pending",
    } or not is_atomic


def _attach_claim_object_metadata(row: dict, claim: object) -> None:
    row.update({
        "text": str(getattr(claim, "text", row.get("text", "")) or row.get("text", "")),
        "claim_type_candidate": str(
            getattr(claim, "claim_type", row.get("claim_type_candidate", "unknown"))
            or row.get("claim_type_candidate", "unknown")
        ),
        "atomicity": str(getattr(claim, "atomicity", "atomic") or "atomic"),
        "is_atomic": bool(getattr(claim, "is_atomic", True)),
        "split_suggestions": list(getattr(claim, "split_suggestions", []) or []),
        "source_evidence_ids": list(getattr(claim, "source_evidence_ids", []) or []),
        # Human-readable section title (issue #359) so downstream prompts can
        # show where the claim came from without the structure artifact.
        "section_title": getattr(claim, "section_title", None),
    })
    if getattr(claim, "qualification_reason", None):
        row["reason"] = str(getattr(claim, "qualification_reason"))
    if getattr(claim, "confidence", None) is not None:
        row["confidence"] = float(
            getattr(claim, "confidence", row.get("confidence", 0.0)) or 0.0
        )


def _claim_object_row(claim: object, record=None) -> dict:
    source_span_ids = list(getattr(claim, "source_span_ids", []) or [])
    qualification = dict(getattr(record, "qualification", None) or {}) if record is not None else {}
    row = {
        "claim_id": str(getattr(claim, "claim_id", "") or ""),
        "legacy_claim_id": legacy_claim_id_for_span(record) if record is not None else "",
        "span_id": str(source_span_ids[0]) if source_span_ids else "",
        "block_id": str(getattr(record, "block_id", "") or "") if record is not None else "",
        "section_id": getattr(claim, "section_id", None),
        "text": str(getattr(claim, "text", "") or ""),
        "role_labels": (
            list(getattr(record, "role_labels", None) or [])
            if record is not None
            else [str(getattr(claim, "claim_type", "unknown") or "unknown")]
        ),
        "claim_tier": qualification.get("claim_tier"),
        "claim_type_candidate": str(getattr(claim, "claim_type", "unknown") or "unknown"),
        "reason": str(getattr(claim, "qualification_reason", "") or getattr(record, "reason", "") or ""),
        "confidence": float(getattr(claim, "confidence", 0.0) or 0.0),
    }
    _attach_claim_object_metadata(row, claim)
    parent_id = str(getattr(claim, "parent_claim_id", "") or "")
    if parent_id:
        row["parent_claim_id"] = parent_id
    return row


# ---------------------------------------------------------------------------
# accepted_claims の母集合（IK-0807）
# ---------------------------------------------------------------------------

_NON_ATOMIC_VALUES = {
    "composite",
    "split_required",
    "compound",
    "non_atomic",
    "split_pending",
}


def _is_atomic_obj(claim: object) -> bool:
    return not _claim_is_non_atomic(claim)


def _parent_to_atomic_children(claims: list) -> dict[str, list[str]]:
    """親 claim_id → atomic な子 claim_id の列（claim object の順）。

    子の ``parent_claim_id`` と親の ``subclaim_ids`` の両方から張る。atomic で
    ない子（split_required / split_pending）は置き換え先に数えない。
    """
    by_id = {
        str(getattr(c, "claim_id", "") or ""): c
        for c in claims
        if getattr(c, "claim_id", None)
    }
    children: dict[str, list[str]] = {}

    def _add(parent_id: str, child_id: str) -> None:
        child = by_id.get(child_id)
        if not parent_id or child is None or not _is_atomic_obj(child):
            return
        bucket = children.setdefault(parent_id, [])
        if child_id not in bucket:
            bucket.append(child_id)

    for claim in claims:
        cid = str(getattr(claim, "claim_id", "") or "")
        parent = str(getattr(claim, "parent_claim_id", "") or "")
        if parent:
            _add(parent, cid)
        for sub in getattr(claim, "subclaim_ids", None) or []:
            _add(cid, str(sub))
    # 階層リンクの無い旧 artifact: 親の split_suggestions が同じ節の atomic な
    # claim object と ID か本文で一致するときだけ、それを子とみなす。
    for claim in claims:
        cid = str(getattr(claim, "claim_id", "") or "")
        if not cid or cid in children or _is_atomic_obj(claim):
            continue
        section = str(getattr(claim, "section_id", "") or "")
        for sug in getattr(claim, "split_suggestions", None) or []:
            if not isinstance(sug, dict):
                continue
            sug_id = str(sug.get("claim_id") or "")
            sug_text = " ".join(str(sug.get("text") or "").split())
            for other in claims:
                oid = str(getattr(other, "claim_id", "") or "")
                if oid == cid or not _is_atomic_obj(other):
                    continue
                if str(getattr(other, "section_id", "") or "") != section:
                    continue
                other_text = " ".join(str(getattr(other, "text", "") or "").split())
                if (sug_id and sug_id == oid) or (sug_text and sug_text == other_text):
                    _add(cid, oid)
    return children


def _expand_parent_refs(ids, parent_children: dict[str, list[str]], ordered: bool = False):
    """原子化された親の参照を atomic な子の参照に置き換える。"""
    out: list[str] = []
    for ref in ids or []:
        value = str(ref)
        for target in parent_children.get(value) or [value]:
            if target not in out:
                out.append(target)
    return out if ordered else set(out)


def _norm_text(value) -> str:
    return " ".join(str(value or "").split())


def _span_texts(record) -> set[str]:
    texts = {_norm_text(getattr(record, "text", ""))}
    for cand in getattr(record, "atomic_claims", None) or []:
        if isinstance(cand, dict):
            for key in ("normalized_text", "text"):
                if cand.get(key):
                    texts.add(_norm_text(cand.get(key)))
    texts.discard("")
    return texts


def _match_span_record(claim: object, records: list, parent_record=None):
    """claim object を qualified span に突合する（block_id を補うため）。

    span_id はブロックごとに振り直されるので、同じ節に同じ span_id が複数ある。
    節 + span_id で 1 つに絞れなければ本文（span 本文 / atomic 候補の本文）で、
    それでも決まらなければ親の突合先を継ぐ。決まらなければ None（推測しない）。
    """
    span_ids = [str(v) for v in getattr(claim, "source_span_ids", None) or [] if v]
    if not span_ids:
        return parent_record
    section = str(getattr(claim, "section_id", "") or "")
    candidates = [
        r for r in records
        if str(getattr(r, "span_id", "") or "") in span_ids
        and (not section or str(getattr(r, "section_id", "") or "") == section)
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return parent_record
    text = _norm_text(getattr(claim, "text", ""))
    by_text = [r for r in candidates if text and text in _span_texts(r)]
    if len(by_text) == 1:
        return by_text[0]
    if parent_record is not None and parent_record in candidates:
        return parent_record
    return None


def _rows_from_claim_objects(qualified_claims: ClaimQualificationResult, claims: list) -> list[dict]:
    records = list(qualified_claims.qualified_spans or [])
    parent_children = _parent_to_atomic_children(claims)
    by_id = {str(getattr(c, "claim_id", "") or ""): c for c in claims}
    matched: dict[str, object] = {}

    def _record_for(claim) -> object | None:
        cid = str(getattr(claim, "claim_id", "") or "")
        if cid in matched:
            return matched[cid]
        parent_id = str(getattr(claim, "parent_claim_id", "") or "")
        parent_record = None
        if parent_id and parent_id in by_id and parent_id != cid:
            parent_record = _match_span_record(by_id[parent_id], records)
        record = _match_span_record(claim, records, parent_record)
        matched[cid] = record
        return record

    rows: list[dict] = []
    used_records: set[int] = set()
    for claim in claims:
        cid = str(getattr(claim, "claim_id", "") or "")
        if not cid:
            continue
        record = _record_for(claim)
        if record is not None:
            used_records.add(id(record))
        # 原子化された親は子に置き換える（子が無いときだけ親を明示付きで渡す）。
        if parent_children.get(cid):
            continue
        rows.append(_claim_object_row(claim, record))
    # claim object に対応の無い採択 span（旧 artifact など）は落とさず、
    # atomic 判定ができないことを明示して渡す（P4）。
    for record in records:
        if id(record) in used_records:
            continue
        row = _span_row(record)
        row["atomicity"] = "unknown"
        row["is_atomic"] = False
        rows.append(row)
    return rows


def _span_row(record) -> dict:
    candidates = [c for c in getattr(record, "atomic_claims", None) or [] if isinstance(c, dict)]
    accepted_atomic = [
        c for c in candidates
        if str(c.get("atomicity") or "") == "atomic" and str(c.get("status") or "") == "accepted"
    ]
    if len(candidates) == 1 and len(accepted_atomic) == 1:
        atomicity = "atomic"
    elif candidates:
        atomicity = "composite"
    else:
        atomicity = "unknown"
    return {
        "claim_id": legacy_claim_id_for_span(record),
        "legacy_claim_id": legacy_claim_id_for_span(record),
        "span_id": record.span_id,
        "block_id": record.block_id,
        "section_id": record.section_id,
        "text": record.text,
        "role_labels": record.role_labels,
        "claim_tier": record.qualification.get("claim_tier"),
        "claim_type_candidate": record.qualification.get("claim_type_candidate"),
        "reason": record.reason,
        "confidence": record.confidence,
        "atomicity": atomicity,
        "is_atomic": atomicity == "atomic",
    }
