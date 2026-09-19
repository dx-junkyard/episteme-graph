"""束の項目 → :func:`core.knowledge_objects.sync.sync_live_rows` の incoming 行（§4.2）。

すべて純関数（DB を読まない・SQL を書かない）。不変条項のうち本モジュールが担うのは:

- **KT4 stable_key は取り込み先で再計算する**。束の ``stable_key`` は材料に
  ``document_id`` を含むので別 document では一致しない。Phase 1 と同じ
  :mod:`core.knowledge_objects.stable_key` の関数で作り直し、束の値は
  ``import`` ブロックの ``source_stable_key`` に事実として残す。
- **KT2 / T-1 取り込みは候補で着地する**。``review_status`` は常に
  :data:`IMPORT_REVIEW_STATUS`、component の ``status`` は :data:`IMPORT_COMPONENT_STATUS`。
  束の値は ``source_review_status`` に残すだけで、**このインスタンスの承認にしない**。
- **KO7 型語彙の丸め**。``claim_type`` / ``component_type`` は Phase 1 と同じく語彙表の
  値へ丸め、自称は ``*_text`` 列に残す。

claim には ``agent_payload`` 列が無いため、出所は ``source_scope["import"]`` に置く
（列を増やさない = KT5 / 積層）。component / equation / evidence / derivation step は
``agent_payload["import"]``。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from core.knowledge_objects import stable_key as ko_keys
from core.knowledge_objects.schema import (
    CLAIM_ORIGIN_EQUATION_SYNTHESIS,
    CLAIM_ORIGINS,
    CLAIM_TIERS,
    DEFAULT_REVIEW_STATUS,
    normalize_claim_type,
    normalize_component_type,
)

#: 取り込み行の確定状態（T-1。束の値は引き継がない）。
IMPORT_REVIEW_STATUS = DEFAULT_REVIEW_STATUS
IMPORT_COMPONENT_STATUS = "candidate"

#: ``support_status`` が束に書かれていないときの値（P4-R6）。
#: かつては ``source_backed`` を既定にしていたが、それは**この教材で出典に当たった**
#: という主張であり、外から来た束について既定で名乗ってよい状態ではない
#: （``source_backed`` の claim は R層の出題対象・グラフの強い backing に使われる）。
#: 書いていないものは「確認が要る」に落とす。
IMPORT_SUPPORT_STATUS_FALLBACK = "review_required"

#: 出所ブロックのキー（``source_scope`` / ``agent_payload`` の中）。
IMPORT_PROVENANCE_KEY = "import"

#: 装置系の component_type は migration 041 の CHECK にそのまま載る（persist_components と同じ）。
_APPARATUS_COMPONENT_TYPES = ("apparatus", "instrument", "part")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _id_list(values: Any) -> list[str]:
    """順序を保った重複なしの ID 列（persistence の ``_id_list`` と同じ扱い）。"""
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = _text(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _remap(values: Iterable[str], id_map: dict[str, str]) -> list[str]:
    """agent ID 列を DB UUID へ写す（写せない ID はそのまま残す＝情報を落とさない）。"""
    return [id_map.get(value, value) for value in _id_list(values)]


# ---------------------------------------------------------------------------
# 出所（KT8: 束の中に人は載っていない。載せられるのは束の事実だけ）
# ---------------------------------------------------------------------------


def provenance(
    source: dict,
    *,
    source_id: str,
    source_stable_key: str = "",
    source_review_status: str = "",
    extra: dict | None = None,
) -> dict:
    """1 行分の ``import`` ブロック（束のどの項目から来たか）。"""
    block = {
        "export_id": _text(source.get("export_id")),
        "source_object_type": _text(source.get("object_type")),
        "source_object_id": _text(source.get("object_id")),
        "source_document_id": _text(source.get("source_document_id")),
        "exported_at": _text(source.get("exported_at")),
        "bundle_sha256": _text(source.get("bundle_sha256")),
        "schema_version": _text(source.get("schema_version")),
        "source_id": _text(source_id),
        # KT4: 束が持っていた stable_key は「別 document でのキー」なので、
        # 取り込み先のキーとしては使わず事実として残すだけ。
        "source_stable_key": _text(source_stable_key),
        # T-1: 他インスタンスの承認はこのインスタンスの承認にしない。
        "source_review_status": _text(source_review_status),
    }
    if extra:
        block.update(extra)
    return block


def _source_document_id(source: dict, item: dict) -> str:
    """項目が名乗る出所 document（無ければ束の scope の先頭）。"""
    own = _text(item.get("document_id"))
    if own:
        return own
    document_ids = source.get("document_ids") or []
    return _text(document_ids[0]) if document_ids else ""


def _with_source_document(source: dict, item: dict) -> dict:
    return {**source, "source_document_id": _source_document_id(source, item)}


# ---------------------------------------------------------------------------
# 索引（stable_key の材料）
# ---------------------------------------------------------------------------


def evidence_block_index(evidence: list[dict]) -> dict[str, str]:
    """``evidence_id -> block_id``（束の evidence_snippets 由来）。"""
    index: dict[str, str] = {}
    for record in evidence or []:
        evidence_id = _text(record.get("evidence_id"))
        block_id = _text(record.get("block_id"))
        if evidence_id and block_id:
            index.setdefault(evidence_id, block_id)
    return index


def claim_block_index(claims: list[dict], evidence_blocks: dict[str, str]) -> dict[str, list[str]]:
    """``claim_id -> [block_id]``（claim の ``source_evidence_ids`` 経由。推測しない）。"""
    index: dict[str, list[str]] = {}
    for claim in claims or []:
        claim_id = _text(claim.get("claim_id"))
        if not claim_id:
            continue
        blocks = _id_list(
            evidence_blocks[ev_id]
            for ev_id in _id_list(claim.get("source_evidence_ids"))
            if ev_id in evidence_blocks
        )
        index[claim_id] = blocks
    return index


def equation_key_map(document_id: str, equations: list[dict]) -> dict[str, str]:
    """``agent equation_id -> 取り込み先での stable_key``（KT4 の再計算）。"""
    out: dict[str, str] = {}
    for record in equations or []:
        agent_id = _text(record.get("equation_id"))
        if not agent_id:
            continue
        location = record.get("source_location") if isinstance(record.get("source_location"), dict) else {}
        out[agent_id] = ko_keys.equation_stable_key(
            document_id,
            record.get("latex"),
            record.get("plain_text") or record.get("raw_text"),
            location.get("block_id"),
            record.get("label"),
        )
    return out


def _resolved_equation_keys(agent_ids: Any, key_map: dict[str, str]) -> list[str]:
    """persistence の同名ヘルパと同じ扱い（解決できない ID はそのまま材料にする）。"""
    return [key_map.get(agent_id, agent_id) for agent_id in _id_list(agent_ids)]


def _claim_origin(claim: dict) -> str:
    """束の claim の ``origin``。**分からないときは空**（V-11）。

    かつては不明を ``claim_object`` で埋めていたが、``origin`` は内容列なので、
    束が黙っている値で既存行を上書きすると ``atomic_rewrite`` のような由来が
    往復のたびに消える。束が明示した語彙 → 合成 claim の決定論判定 → 空、の順に落とす
    （空のときは呼び出し側が ``values`` にキー自体を入れない = 既存行を触らない）。
    """
    declared = _text(claim.get("origin"))
    if declared in CLAIM_ORIGINS:
        return declared
    claim_id = _text(claim.get("claim_id"))
    if _text(claim.get("synthesis_method")) or claim_id.startswith("synth_claim_"):
        return CLAIM_ORIGIN_EQUATION_SYNTHESIS
    return ""


def claim_parent_links(claims: list[dict]) -> list[tuple[str, str]]:
    """束の claim の親子（``[(子の agent ID, 親の agent ID)]``）。

    束の ``parent_claim_id`` は**束の中の claim_id 空間**で書かれている。取り込み側は
    sync 後の id 写像で DB UUID に張り直す（:func:`core.knowledge_import.apply.link_claim_parents`）。
    自分自身を親にする行・束に無い親は落とす（推測で結ばない）。
    """
    known = {_text(c.get("claim_id")) for c in claims or []} - {""}
    links: list[tuple[str, str]] = []
    for claim in claims or []:
        child = _text(claim.get("claim_id"))
        parent = _text(claim.get("parent_claim_id"))
        if child and parent and parent != child and parent in known:
            links.append((child, parent))
    return links


# ---------------------------------------------------------------------------
# 行の組み立て
# ---------------------------------------------------------------------------


def claim_rows(
    document_id: str,
    claims: list[dict],
    *,
    evidence: list[dict] | None = None,
    equations: list[dict] | None = None,
    source: dict | None = None,
) -> list[dict]:
    """claims.json → ``theory_claims`` の incoming 行。"""
    source = dict(source or {})
    evidence_blocks = evidence_block_index(evidence or [])
    blocks_by_claim = claim_block_index(claims or [], evidence_blocks)
    equation_keys = equation_key_map(document_id, equations or [])

    items: list[dict] = []
    for claim in claims or []:
        agent_id = _text(claim.get("claim_id"))
        if not agent_id:
            continue
        text_value = str(claim.get("text") or "")
        normalized = str(claim.get("normalized_text") or "") or text_value
        block_ids = blocks_by_claim.get(agent_id, [])
        primary_block = block_ids[0] if block_ids else ""
        raw_claim_type = _text(claim.get("claim_type"))
        equation_ids = _id_list(claim.get("equation_ids")) or _id_list(
            (claim.get("equation") or {}).get("equation_ids")
            if isinstance(claim.get("equation"), dict) else []
        )
        bundle_scope = claim.get("source_scope") if isinstance(claim.get("source_scope"), dict) else {}
        tier = _text(claim.get("claim_tier"))
        items.append({
            "agent_id": agent_id,
            "stable_key": ko_keys.claim_stable_key(document_id, normalized, block_ids),
            "source_stable_key": _text(claim.get("stable_key")),
            "values": {
                # 束に chunks 本文は入っていない（§4.2 非対象）ので出典 chunk には
                # 着地しない。NULL のままにして「着地していない」を事実として残す。
                "chunk_id": None,
                "source_scope": {
                    "section_id": bundle_scope.get("section_id"),
                    "section_title": bundle_scope.get("section_title"),
                    "block_id": primary_block or None,
                    "span_id": bundle_scope.get("span_id"),
                    "legacy_ids": [agent_id],
                    IMPORT_PROVENANCE_KEY: provenance(
                        _with_source_document(source, claim),
                        source_id=agent_id,
                        source_stable_key=_text(claim.get("stable_key")),
                        source_review_status=_text(claim.get("review_status")),
                        extra={
                            "source_knowledge_object_id": _text(claim.get("knowledge_object_id")),
                            "source_evidence_ids": _id_list(claim.get("source_evidence_ids")),
                        },
                    ),
                },
                "claim_type": normalize_claim_type(raw_claim_type),
                "claim_type_text": raw_claim_type,
                "text": text_value,
                "normalized_text": normalized,
                "concepts": list(claim.get("concepts") or []),
                "equation": (
                    {
                        "equation_ids": equation_ids,
                        "equation_stable_keys": _resolved_equation_keys(equation_ids, equation_keys),
                    }
                    if equation_ids else {}
                ),
                "support_status": (
                    _text(claim.get("support_status")) or IMPORT_SUPPORT_STATUS_FALLBACK
                ),
                "evidence_text": str(claim.get("evidence_text") or ""),
                "claim_tier": tier if tier in CLAIM_TIERS else "",
                # T-1: 束の review_status は引き継がない。
                "review_status": IMPORT_REVIEW_STATUS,
            },
        })
        # V-11: origin は分かるときだけ書く（空なら既存行の値を触らない）。
        origin = _claim_origin(claim)
        if origin:
            items[-1]["values"]["origin"] = origin
    return items


def component_rows(
    document_id: str,
    components: list[dict],
    *,
    claims: list[dict] | None = None,
    evidence: list[dict] | None = None,
    claim_id_map: dict[str, str] | None = None,
    source: dict | None = None,
) -> list[dict]:
    """components.json → ``theory_components`` の incoming 行。

    ``claim_id_map`` は claims の同期結果（agent claim ID → DB UUID）。claim を指す
    参照（``evidence_claims`` / ``linked_claim_ids``）だけを写し、写せない ID は
    そのまま残す（情報を落とさない）。
    """
    source = dict(source or {})
    claim_id_map = dict(claim_id_map or {})
    evidence_blocks = evidence_block_index(evidence or [])
    blocks_by_claim = claim_block_index(claims or [], evidence_blocks)

    items: list[dict] = []
    for comp in components or []:
        agent_id = _text(comp.get("component_id"))
        if not agent_id:
            continue
        label = _text(comp.get("name")) or _text(comp.get("label"))
        operation = _text(comp.get("primary_operation")) or _text(comp.get("operation"))
        evidence_refs = comp.get("evidence_refs") if isinstance(comp.get("evidence_refs"), dict) else {}
        linked_claim_ids = _id_list(comp.get("linked_claim_ids"))
        evidence_claims = _id_list(
            list(comp.get("evidence_claims") or []) + list(evidence_refs.get("claim_ids") or [])
        )
        # stable_key の材料になる出典 block 集合（persistence の _component_block_ids と同型）。
        block_ids: set[str] = set()
        for claim_id in _id_list(linked_claim_ids + evidence_claims):
            block_ids.update(blocks_by_claim.get(claim_id, ()))
        for evidence_id in _id_list(comp.get("linked_evidence_ids")):
            block = evidence_blocks.get(evidence_id)
            if block:
                block_ids.add(block)

        raw_component_type = _text(comp.get("component_type"))
        bundle_scope = comp.get("source_scope") if isinstance(comp.get("source_scope"), dict) else {}
        source_scope = dict(bundle_scope)
        source_scope["document_id"] = document_id
        source_scope["legacy_ids"] = [agent_id]

        agent_payload = {
            "responsibility_type": _text(comp.get("responsibility_type")),
            "secondary_operations": list(comp.get("secondary_operations") or []),
            "input_equation_ids": _id_list(comp.get("input_equation_ids")),
            "intermediate_equation_ids": _id_list(comp.get("intermediate_equation_ids")),
            "output_equation_ids": _id_list(comp.get("output_equation_ids")),
            "constraint_equation_ids": _id_list(comp.get("constraint_equation_ids")),
            "definition_equation_ids": _id_list(comp.get("definition_equation_ids")),
            "bundle_legacy_ids": _id_list(comp.get("legacy_ids")),
            IMPORT_PROVENANCE_KEY: provenance(
                _with_source_document(source, comp),
                source_id=agent_id,
                source_stable_key=_text(comp.get("stable_key")),
                source_review_status=_text(comp.get("review_status")),
                extra={
                    "source_knowledge_object_id": _text(comp.get("knowledge_object_id")),
                    "source_status": _text(comp.get("status")),
                },
            ),
        }

        items.append({
            "agent_id": agent_id,
            "stable_key": ko_keys.component_stable_key(
                document_id, label, operation, sorted(block_ids)
            ),
            "source_stable_key": _text(comp.get("stable_key")),
            "values": {
                # コースには結び付けない（束はコースを運ばない = §4.2 非対象）。
                "course_id": None,
                "name": label or "Untitled",
                "component_type": (
                    raw_component_type
                    if raw_component_type in _APPARATUS_COMPONENT_TYPES
                    else normalize_component_type(raw_component_type)
                ),
                "component_type_text": raw_component_type,
                "summary": str(comp.get("summary") or ""),
                # T-1: 取り込みは候補で着地する。
                "status": IMPORT_COMPONENT_STATUS,
                "review_status": IMPORT_REVIEW_STATUS,
                "source_chunks": [],
                "inputs": list(comp.get("inputs") or []),
                "outputs": list(comp.get("outputs") or []),
                "preconditions": list(comp.get("preconditions") or []),
                "constraints": list(comp.get("constraints") or []),
                "invalid_conditions": list(comp.get("invalid_conditions") or []),
                "dependencies": list(comp.get("dependencies") or []),
                "blackbox_policy": {"default_level": "summary", "expand_if_unlearned": True},
                "validation_warnings": [],
                "teacher_notes": "",
                "source_scope": source_scope,
                "evidence_claims": _remap(evidence_claims, claim_id_map),
                "maturity_level": _text(comp.get("maturity_level")) or "paper_claim",
                # 成熟度の出所は「取り込み」であって教員のレビューではない。
                "maturity_source": "imported",
                "cautions": list(comp.get("cautions") or []),
                "connectors": comp.get("connectors") if isinstance(comp.get("connectors"), dict) else {},
                "internal_flow": list(comp.get("internal_flow") or []),
                "duplicate_candidates": [],
                "operation": operation,
                "teaching_takeaway": str(comp.get("teacher_notes") or ""),
                "teaching_granularity": {},
                "prerequisite_concepts": [],
                "assumptions": list(comp.get("assumptions") or []),
                "approximations": list(comp.get("approximations") or []),
                "linked_claim_ids": _remap(linked_claim_ids, claim_id_map),
                "linked_equation_ids": _id_list(comp.get("linked_equation_ids")),
                "linked_evidence_ids": _id_list(comp.get("linked_evidence_ids")),
                "linked_derivation_ids": _id_list(comp.get("linked_derivation_ids")),
                "agent_payload": agent_payload,
            },
        })
    return items


def equation_rows(
    document_id: str,
    equations: list[dict],
    *,
    source: dict | None = None,
) -> list[dict]:
    """equations.json → ``knowledge_equations`` の incoming 行。"""
    source = dict(source or {})
    keys = equation_key_map(document_id, equations or [])
    items: list[dict] = []
    for record in equations or []:
        agent_id = _text(record.get("equation_id"))
        if not agent_id:
            continue
        location = record.get("source_location") if isinstance(record.get("source_location"), dict) else {}
        items.append({
            "agent_id": agent_id,
            "stable_key": keys.get(agent_id, ""),
            "source_stable_key": _text(record.get("stable_key")),
            "values": {
                "label": _text(record.get("label")),
                "latex": str(record.get("latex") or ""),
                "plain_text": str(record.get("plain_text") or ""),
                "raw_text": str(record.get("raw_text") or ""),
                "block_id": _text(location.get("block_id")),
                "section_id": _text(location.get("section_id")),
                "page": location.get("page"),
                "equation_type": _text(record.get("equation_type")),
                "semantic_status": _text(record.get("semantic_status")),
                "defined_symbols": list(record.get("defined_symbols") or []),
                "used_symbols": list(record.get("used_symbols") or []),
                "input_equation_ids": _id_list(record.get("input_equation_ids")),
                "output_equation_ids": _id_list(record.get("output_equation_ids")),
                "linked_claim_ids": _id_list(record.get("linked_claim_ids")),
                "source_evidence_ids": _id_list(record.get("source_evidence_ids")),
                "needs_math_review": bool(record.get("needs_math_review")),
                "agent_payload": {
                    IMPORT_PROVENANCE_KEY: provenance(
                        _with_source_document(source, record),
                        source_id=agent_id,
                        source_stable_key=_text(record.get("stable_key")),
                        source_review_status=_text(record.get("review_status")),
                    ),
                },
                "review_status": IMPORT_REVIEW_STATUS,
            },
        })
    return items


def evidence_rows(
    document_id: str,
    evidence: list[dict],
    *,
    source: dict | None = None,
) -> list[dict]:
    """evidence_snippets.json → ``knowledge_evidence`` の incoming 行。"""
    source = dict(source or {})
    items: list[dict] = []
    for record in evidence or []:
        agent_id = _text(record.get("evidence_id"))
        if not agent_id:
            continue
        block_id = _text(record.get("block_id"))
        evidence_text = str(record.get("evidence_text") or "")
        items.append({
            "agent_id": agent_id,
            "stable_key": ko_keys.evidence_stable_key(document_id, block_id, evidence_text),
            "source_stable_key": _text(record.get("stable_key")),
            "values": {
                "block_id": block_id,
                "section_id": _text(record.get("section_id")),
                "page": record.get("page"),
                "span_start": record.get("span_start") or 0,
                "span_end": record.get("span_end") or 0,
                "evidence_text": evidence_text,
                "evidence_role": _text(record.get("evidence_role")) or "source_quote",
                "parent_evidence_id": _text(record.get("parent_evidence_id")),
                "public_export_policy": _text(record.get("public_export_policy")) or "location_only",
                "agent_payload": {
                    IMPORT_PROVENANCE_KEY: provenance(
                        _with_source_document(source, record),
                        source_id=agent_id,
                        source_stable_key=_text(record.get("stable_key")),
                    ),
                },
                # evidence は逐語の写しなので人間の確定列を持たない
                # （migration 078 の ``knowledge_evidence`` に ``review_status`` 列は無い。
                # 書き手 ``persistence._evidence_items`` と同じ扱い = V-1）。
            },
        })
    return items


def derivation_step_rows(
    document_id: str,
    chains: list[dict],
    *,
    equations: list[dict] | None = None,
    source: dict | None = None,
) -> list[dict]:
    """derivation_chains.json → ``knowledge_derivation_steps`` の incoming 行。"""
    source = dict(source or {})
    equation_keys = equation_key_map(document_id, equations or [])
    items: list[dict] = []
    for chain in chains or []:
        derivation_id = _text(chain.get("derivation_id"))
        chain_type = _text(chain.get("chain_type")) or "equation_chain"
        steps = [s for s in (chain.get("steps") or []) if isinstance(s, dict)]
        for index, step in enumerate(steps):
            agent_step_id = _text(step.get("step_id"))
            if not agent_step_id:
                continue
            operation = _text(step.get("operation"))
            input_keys = _resolved_equation_keys(step.get("input_equation_ids"), equation_keys)
            output_keys = _resolved_equation_keys(step.get("output_equation_ids"), equation_keys)
            items.append({
                # V-2: agent 側の step_id（``step_001``）はチェーン内でしか一意でない。
                # 文書内で一意な ID の作り方は knowledge_objects 側が正本（書き手＝
                # パイプラインと同じ規則を使う）。
                "agent_id": ko_keys.derivation_step_agent_id(derivation_id, agent_step_id),
                "stable_key": ko_keys.derivation_step_stable_key(
                    document_id, operation, input_keys, output_keys,
                    derivation_id=derivation_id, step_index=index,
                ),
                "source_stable_key": _text(step.get("stable_key")),
                "values": {
                    "agent_derivation_id": derivation_id,
                    "step_index": index,
                    "operation": operation,
                    "operation_subtype": _text(step.get("operation_subtype")),
                    "chain_type": chain_type,
                    "input_equation_ids": _id_list(step.get("input_equation_ids")),
                    "output_equation_ids": _id_list(step.get("output_equation_ids")),
                    "input_claim_ids": _id_list(step.get("input_claim_ids")),
                    "output_claim_ids": _id_list(step.get("output_claim_ids")),
                    "required_claim_ids": _id_list(step.get("required_claim_ids")),
                    "assumption_ids": _id_list(step.get("assumption_ids") or step.get("assumption_refs")),
                    "source_evidence_ids": _id_list(step.get("source_evidence_ids")),
                    "teaching_takeaway": str(chain.get("teaching_takeaway") or ""),
                    "agent_payload": {
                        IMPORT_PROVENANCE_KEY: provenance(
                            _with_source_document(source, chain),
                            source_id=agent_step_id,
                            source_stable_key=_text(step.get("stable_key")),
                            source_review_status=_text(step.get("review_status")),
                            extra={"source_derivation_id": derivation_id},
                        ),
                    },
                    "review_status": IMPORT_REVIEW_STATUS,
                },
            })
    return items


def dedupe(items: list[dict]) -> list[dict]:
    """同一取り込み内で衝突した stable_key に ``#2`` … を付ける（Phase 1 と同じ規則）。"""
    finals = ko_keys.assign_stable_keys(
        items,
        key_of=lambda item: item["stable_key"],
        agent_id_of=lambda item: item["agent_id"],
    )
    for item, final in zip(items, finals):
        item["stable_key"] = final
    return items
