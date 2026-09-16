"""学ぶ単位の導出（learning_units_design.md §5・親文書 Phase 2 / P2-1）。

A層 artifact（paper_skeleton / thesis_reconstruction / component_assembly /
dsl_linking / figure_table_semantics）から ``learning_units`` の行を**決定論的・非LLM**に
組み立てる純関数モジュール（LU3）。素材が無い種別はスキップし、推測で単位を作らない。

出力は :func:`core.knowledge_objects.sync.sync_live_rows` の ``incoming`` 形
（``{"stable_key", "agent_id", "values"}``）で、書き込みは
``core/document_pipeline/persistence.py::persist_learning_units`` が行う。

DB・FastAPI・LLM を import しない。入力（agent の結果オブジェクト）を mutate しない。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from episteme_graph.agents.component_assembly.schema import concept_name_list

from . import stable_key as ko_keys
from .schema import DEFAULT_UNIT_REVIEW_STATUS, LEARNING_UNIT_KINDS

#: 種別ごとの導出順（``LEARNING_UNIT_KINDS`` と同じ並び）。
KIND_SECTION_BLOCK = "section_block"
KIND_THESIS_SUPPORT = "thesis_support"
KIND_PARENT_COMPONENT = "parent_component"
KIND_DSL_NODE = "dsl_node"
KIND_FIGURE = "figure"

#: label の長さ上限（表示用。summary には全文が残るので情報は落ちない）。
LABEL_MAX_CHARS = 80

#: 内部 ID とみなす綴り（``fig_3.3`` / ``eq_op_12`` / ``theory_op_3`` / ``ev_001``）。
#: 「Figure 3.3」のような人間が読めるラベルは**内部 ID とみなさない**（narrow に判定する）。
_INTERNAL_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9.\-]+)+$")


# ---------------------------------------------------------------------------
# 小さな純ヘルパ
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    return str(value or "").strip()


def _plain(value: Any) -> Any:
    """agent の結果（dataclass / 単純オブジェクト）を素の JSON 化可能値にする。

    ``persistence._plain`` と同じ規則。``persistence`` は sqlalchemy / core.llm を
    import するため、ここでは同等の純粋な実装を持つ（本モジュールの純粋性を保つ）。
    """
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if (
        not isinstance(value, (str, bytes, bool, int, float, type(None)))
        and hasattr(value, "__dict__")
        and not isinstance(value, type)
    ):
        return _plain(vars(value))
    return value


def _id_list(values: Any) -> list[str]:
    out: list[str] = []
    for value in values or []:
        item = _text(value)
        if item and item not in out:
            out.append(item)
    return out


def _truncate(value: Any, limit: int = LABEL_MAX_CHARS) -> str:
    text = " ".join(_text(value).split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def is_internal_id_label(value: Any) -> bool:
    """``fig_3.3`` / ``eq_op_12`` のような内部 ID かどうか（label に使わない）。"""
    text = _text(value)
    return bool(text) and bool(_INTERNAL_ID_RE.match(text))


def _display_label(*candidates: Any) -> str:
    """内部 ID でない最初の候補を表示ラベルにする（無ければ空文字）。"""
    for candidate in candidates:
        text = _truncate(candidate)
        if text and not is_internal_id_label(text):
            return text
    return ""


def _claim_blocks_by_agent_id(claim_id_map: Mapping[str, str]) -> dict[str, list[str]]:
    """``agent claim ID -> [block_id]``。

    ``claim_id_map`` は「突合キー → DB UUID」で、``"{block_id}:{span_id}"`` 形のキー
    だけが block を含む。同じ UUID を指す他の agent ID にもその block を配る
    （``persistence._claim_block_index`` と同じ規則。推測はしない）。
    """
    blocks_by_uuid: dict[str, set[str]] = {}
    for key, db_id in (claim_id_map or {}).items():
        if ":" not in str(key):
            continue
        block = str(key).split(":", 1)[0].strip()
        if block:
            blocks_by_uuid.setdefault(str(db_id), set()).add(block)
    out: dict[str, list[str]] = {}
    for key, db_id in (claim_id_map or {}).items():
        blocks = blocks_by_uuid.get(str(db_id))
        if blocks:
            out[str(key)] = sorted(blocks)
    return out


def _claim_uuids_by_block(claim_id_map: Mapping[str, str]) -> dict[str, list[str]]:
    """``block_id -> [claim の DB UUID]``（``"{block_id}:{span_id}"`` キーのみ）。"""
    out: dict[str, list[str]] = {}
    for key in sorted(str(k) for k in (claim_id_map or {})):
        if ":" not in key:
            continue
        block = key.split(":", 1)[0].strip()
        db_id = _text(claim_id_map.get(key))
        if not block or not db_id:
            continue
        bucket = out.setdefault(block, [])
        if db_id not in bucket:
            bucket.append(db_id)
    return out


def _evidence_blocks(evidence_registry: Any) -> dict[str, str]:
    """``evidence_id -> block_id``（``persistence._evidence_block_index`` と同じ）。"""
    index: dict[str, str] = {}
    for record in getattr(evidence_registry, "records", None) or []:
        ev_id = _text(getattr(record, "evidence_id", ""))
        block_id = _text(getattr(getattr(record, "source", None), "block_id", ""))
        if ev_id and block_id:
            index.setdefault(ev_id, block_id)
    return index


def _resolve_claims(agent_ids: Iterable[Any], claim_id_map: Mapping[str, str]) -> list[str]:
    """agent claim ID の並びを DB UUID に写す（解決できないものは落とす = 推測しない）。"""
    out: list[str] = []
    for agent_id in _id_list(agent_ids):
        db_id = _text((claim_id_map or {}).get(agent_id))
        if db_id and db_id not in out:
            out.append(db_id)
    return out


def _teaches(
    *,
    concepts: Sequence[str] = (),
    claim_ids: Sequence[str] = (),
    equation_ids: Sequence[str] = (),
) -> list[dict]:
    """``teaches``（LRMI 相当）を組む。label が引けない参照は空文字で持つ（捏造しない）。"""
    items: list[dict] = []
    for name in concepts:
        text = _text(name)
        if text:
            items.append({"kind": "concept", "ref": text, "label": text})
    for claim_id in claim_ids:
        text = _text(claim_id)
        if text:
            items.append({"kind": "claim", "ref": text, "label": ""})
    for equation_id in equation_ids:
        text = _text(equation_id)
        if text:
            items.append({"kind": "equation", "ref": text, "label": ""})
    return items


def _item(
    document_id: str,
    *,
    unit_kind: str,
    agent_unit_id: str,
    key_text: str,
    key_refs: Iterable[str],
    label: str,
    summary: str,
    order_index: int,
    teaches: list[dict],
    section_ids: Sequence[str] = (),
    source_block_ids: Sequence[str] = (),
    linked_claim_ids: Sequence[str] = (),
    linked_equation_ids: Sequence[str] = (),
    linked_component_ids: Sequence[str] = (),
    linked_figure_ids: Sequence[str] = (),
    agent_payload: dict | None = None,
) -> dict:
    return {
        "agent_id": agent_unit_id,
        "stable_key": ko_keys.learning_unit_stable_key(
            document_id, unit_kind, key_text, key_refs
        ),
        "values": {
            "unit_kind": unit_kind,
            "label": label,
            "summary": summary,
            "teaches": teaches,
            "order_index": order_index,
            "section_ids": list(section_ids),
            "source_block_ids": list(source_block_ids),
            "linked_claim_ids": list(linked_claim_ids),
            "linked_equation_ids": list(linked_equation_ids),
            "linked_component_ids": list(linked_component_ids),
            "linked_figure_ids": list(linked_figure_ids),
            "agent_payload": dict(agent_payload or {}),
            "review_status": DEFAULT_UNIT_REVIEW_STATUS,
            "teacher_notes": "",
        },
    }


# ---------------------------------------------------------------------------
# 種別ごとの導出
# ---------------------------------------------------------------------------


def _section_block_units(
    document_id: str, skeleton: Any, claim_id_map: Mapping[str, str]
) -> list[dict]:
    """``PaperSkeletonResult.logical_blocks`` → 章立ての論理ブロック。

    ``prior_work`` / ``meta`` も落とさない（LU3「情報を落とさない」）。
    """
    blocks = getattr(skeleton, "logical_blocks", None) or []
    claims_by_block = _claim_uuids_by_block(claim_id_map)
    items: list[dict] = []
    for index, block in enumerate(blocks):
        data = _plain(block)
        if not isinstance(data, dict):
            continue
        agent_unit_id = _text(data.get("block_id"))
        if not agent_unit_id:
            continue
        block_type = _text(data.get("block_type"))
        evidence_blocks = _id_list(data.get("evidence_block_ids"))
        claim_ids: list[str] = []
        for evidence_block in evidence_blocks:
            for claim_id in claims_by_block.get(evidence_block, ()):
                if claim_id not in claim_ids:
                    claim_ids.append(claim_id)
        label = _display_label(data.get("label"), data.get("summary"), block_type)
        items.append(_item(
            document_id,
            unit_kind=KIND_SECTION_BLOCK,
            agent_unit_id=agent_unit_id,
            # block_type + 正規化 label + 出典 block 集合（設計書 §5）。
            key_text=f"{block_type} {_text(data.get('label'))}",
            key_refs=evidence_blocks,
            label=label,
            summary=_text(data.get("summary")),
            order_index=index,
            teaches=_teaches(claim_ids=claim_ids),
            section_ids=_id_list(data.get("section_ids")),
            source_block_ids=evidence_blocks,
            linked_claim_ids=claim_ids,
            agent_payload={"block_type": block_type},
        ))
    return items


def uncovered_sections(structure: Any, skeleton: Any) -> list[dict]:
    """``section_block`` の単位が1つも立たなかった章を列挙する（P2-R11）。

    ``_section_block_units`` は ``PaperSkeletonResult.logical_blocks`` を材料にするので、
    skeleton が論理ブロックを出さなかった章はどの単位にも現れない。**その事実を黙って
    落とさない**ために、文書構造の章一覧と論理ブロックが張っている ``section_ids`` の
    差を返す（LU3「情報を落とさない」）。

    - **推定しない**（PL3）: 章の中身から単位を作り直したりはせず、名前を並べるだけ。
    - **件数を持たせない**（LU5）: 呼び出し側が事実文に添えるのは章の題名の列挙。
    - 材料が無い（構造が読めない・skeleton が無い）ときは空リスト —
      「単位が立たなかった」と「素材が無い」を混同しない。

    Args:
        structure: DocumentStructureResult 相当（dataclass でも素の dict でも良い）。
        skeleton: PaperSkeletonResult 相当。

    Returns:
        ``[{"section_id": str, "title": str}, ...]``（文書構造の ``order`` 順）。
        題名が無い章は落とす（内部 ID を章の名前として表示させない）。
    """
    if structure is None or skeleton is None:
        return []
    data = _plain(structure)
    if not isinstance(data, dict):
        return []
    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        return []

    skeleton_data = _plain(skeleton)
    blocks = (
        skeleton_data.get("logical_blocks")
        if isinstance(skeleton_data, dict)
        else getattr(skeleton, "logical_blocks", None)
    )
    covered: set[str] = set()
    for block in (blocks or []):
        block_data = _plain(block)
        if not isinstance(block_data, dict):
            continue
        covered.update(_id_list(block_data.get("section_ids")))

    ordered = [s for s in sections if isinstance(s, dict)]
    ordered.sort(key=lambda s: s.get("order") if isinstance(s.get("order"), int) else 0)

    out: list[dict] = []
    seen: set[str] = set()
    for section in ordered:
        section_id = _text(section.get("section_id"))
        title = _text(section.get("title"))
        if not section_id or section_id in covered or section_id in seen or not title:
            continue
        seen.add(section_id)
        out.append({"section_id": section_id, "title": title})
    return out


def _thesis_nodes(thesis: Any) -> list[dict]:
    """``central_thesis`` + ``support_structure`` を ref ノードへ平坦化する。

    ``thesis_ref`` の表記（``"central_thesis"`` / ``"support:{section}:{idx}"``）は
    ``persistence._thesis_ref_nodes`` と**同じ語彙**で、claim の ``thesis_refs`` や
    component の ``thesis_context`` と翻訳なしで突き合わせられる。
    """
    if not thesis:
        return []
    central = getattr(thesis, "central_thesis", None)
    if not isinstance(central, dict):
        central = {}
    nodes: list[dict] = [{
        "thesis_ref": "central_thesis",
        "kind": "central_thesis",
        "text": _text(central.get("text")),
        "claim_ids": _id_list(central.get("claim_ids")),
        "equation_ids": _id_list(central.get("equation_ids")),
        "evidence_block_ids": _id_list(central.get("evidence_block_ids")),
    }]
    support_structure = getattr(thesis, "support_structure", None)
    if isinstance(support_structure, dict):
        for section, entries in support_structure.items():
            if not isinstance(entries, list):
                continue
            for idx, entry in enumerate(entries):
                data = _plain(entry)
                if not isinstance(data, dict):
                    continue
                nodes.append({
                    "thesis_ref": f"support:{section}:{idx}",
                    "kind": _text(section),
                    "text": _text(data.get("text")),
                    "claim_ids": _id_list(data.get("claim_ids")),
                    "equation_ids": _id_list(data.get("equation_ids")),
                    "evidence_block_ids": _id_list(data.get("evidence_block_ids")),
                })
    return nodes


def _thesis_support_units(
    document_id: str, thesis: Any, claim_id_map: Mapping[str, str]
) -> list[dict]:
    items: list[dict] = []
    for index, node in enumerate(_thesis_nodes(thesis)):
        text = node["text"]
        claim_ids = node["claim_ids"]
        equation_ids = node["equation_ids"]
        if not text and not claim_ids and not equation_ids:
            # 中身の無い（fallback の）中心命題は単位にしない。
            continue
        resolved_claims = _resolve_claims(claim_ids, claim_id_map)
        items.append(_item(
            document_id,
            unit_kind=KIND_THESIS_SUPPORT,
            agent_unit_id=node["thesis_ref"],
            # 出所 ID + 正規化 text + claim / equation 集合（設計書 §5。reason は載せない）。
            key_text=text,
            key_refs=[node["thesis_ref"], *claim_ids, *equation_ids],
            label=_display_label(text),
            summary=text,
            order_index=index,
            teaches=_teaches(claim_ids=resolved_claims, equation_ids=equation_ids),
            source_block_ids=node["evidence_block_ids"],
            linked_claim_ids=resolved_claims,
            linked_equation_ids=equation_ids,
            agent_payload={"thesis_kind": node["kind"]},
        ))
    return items


def _parent_component_units(
    document_id: str,
    component_result: Any,
    claim_id_map: Mapping[str, str],
    component_id_map: Mapping[str, str],
    evidence_registry: Any,
) -> list[dict]:
    """``component_refinement.component_refinement_records`` → LLM 原案 1 件 = 1 単位。

    決定論 refinement が分割した子（``theory_components`` の行）を束ねる親を、
    ``theory_components`` に行として作らずに一級の単位として持つ（設計書 §5.1）。
    """
    refinement = getattr(component_result, "component_refinement", None)
    records = []
    if isinstance(refinement, dict):
        records = refinement.get("component_refinement_records") or []
    if not records:
        return []

    report = getattr(component_result, "refinement_report", None)
    parent_labels: dict[str, str] = {}
    if isinstance(report, dict):
        for action in report.get("split_actions") or []:
            if not isinstance(action, dict):
                continue
            parent_id = _text(action.get("parent_component_id"))
            if parent_id:
                parent_labels.setdefault(parent_id, _text(action.get("parent_label")))

    components_by_id: dict[str, dict] = {}
    for component in getattr(component_result, "components", None) or []:
        data = _plain(component)
        if isinstance(data, dict) and _text(data.get("component_id")):
            components_by_id[_text(data["component_id"])] = data

    claim_blocks = _claim_blocks_by_agent_id(claim_id_map)
    evidence_blocks = _evidence_blocks(evidence_registry)

    items: list[dict] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        agent_unit_id = _text(record.get("original_component_id"))
        if not agent_unit_id:
            continue
        child_ids = _id_list(record.get("split_into"))
        provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}

        # 原案そのものが残っている（unchanged）ときは、その component の label / summary。
        # 分割されたときは split_actions の parent_label（親の summary は artifact に無い
        # ので空のままにする — 子の要約で埋めない）。
        own = components_by_id.get(agent_unit_id) or {}
        raw_label = _text(own.get("label")) or parent_labels.get(agent_unit_id, "")
        summary = _text(own.get("summary"))

        source_claim_ids = _id_list(provenance.get("source_claim_ids"))
        source_equation_ids = _id_list(provenance.get("source_equation_ids"))
        source_evidence_ids = _id_list(provenance.get("source_evidence_ids"))

        blocks: set[str] = set()
        for claim_id in source_claim_ids:
            blocks.update(claim_blocks.get(claim_id, ()))
        for evidence_id in source_evidence_ids:
            block = evidence_blocks.get(evidence_id)
            if block:
                blocks.add(block)
        block_ids = sorted(blocks)

        concepts: list[str] = []
        for child_id in [agent_unit_id, *child_ids]:
            child = components_by_id.get(child_id)
            if not child:
                continue
            for name in concept_name_list(child.get("concepts")):
                if name not in concepts:
                    concepts.append(name)

        linked_component_ids = [
            db_id for db_id in (
                _text((component_id_map or {}).get(child_id)) for child_id in child_ids
            ) if db_id
        ]
        items.append(_item(
            document_id,
            unit_kind=KIND_PARENT_COMPONENT,
            agent_unit_id=agent_unit_id,
            # 正規化 label + 子の出典 block 集合（設計書 §5）。
            key_text=raw_label,
            key_refs=block_ids,
            label=_display_label(raw_label, summary),
            summary=summary,
            order_index=index,
            teaches=_teaches(
                concepts=concepts,
                claim_ids=_resolve_claims(source_claim_ids, claim_id_map),
                equation_ids=source_equation_ids,
            ),
            source_block_ids=block_ids,
            linked_claim_ids=_resolve_claims(source_claim_ids, claim_id_map),
            linked_equation_ids=source_equation_ids,
            linked_component_ids=linked_component_ids,
            agent_payload={
                # コース側（freeze）が artifact と突合するために必須（設計書 §4.1 / §6.3）。
                "linked_component_agent_ids": child_ids,
                "refinement_status": _text(record.get("refinement_status")),
            },
        ))
    return items


def _dsl_node_units(
    document_id: str, dsl: Any, claim_id_map: Mapping[str, str]
) -> list[dict]:
    nodes = getattr(dsl, "nodes", None) or []
    items: list[dict] = []
    for index, node in enumerate(nodes):
        data = _plain(node)
        if not isinstance(data, dict):
            continue
        agent_unit_id = _text(data.get("node_id"))
        node_value = _text(data.get("node_value"))
        if not agent_unit_id or not node_value:
            continue
        node_type = _text(data.get("node_type"))
        source_refs = data.get("source_refs") if isinstance(data.get("source_refs"), dict) else {}
        claim_ids = _id_list(source_refs.get("claim_ids"))
        equation_ids = _id_list(source_refs.get("equation_ids"))
        items.append(_item(
            document_id,
            unit_kind=KIND_DSL_NODE,
            agent_unit_id=agent_unit_id,
            # node_type + 正規化 node_value（設計書 §5）。
            key_text=f"{node_type} {node_value}",
            key_refs=(),
            label=_display_label(node_value),
            summary=node_type,
            order_index=index,
            teaches=_teaches(concepts=[node_value]),
            linked_claim_ids=_resolve_claims(claim_ids, claim_id_map),
            linked_equation_ids=equation_ids,
            agent_payload={
                "node_type": node_type,
                "is_thesis_anchor": bool(data.get("is_thesis_anchor")),
            },
        ))
    return items


def _figure_units(
    document_id: str, figures: Any, claim_id_map: Mapping[str, str]
) -> list[dict]:
    records = getattr(figures, "figures", None) or []
    items: list[dict] = []
    for index, record in enumerate(records):
        data = _plain(record)
        if not isinstance(data, dict):
            continue
        agent_unit_id = _text(data.get("figure_id"))
        if not agent_unit_id:
            continue
        caption = _text(data.get("caption"))
        figure_type = _text(data.get("figure_type"))
        location = data.get("source_location") if isinstance(data.get("source_location"), dict) else {}
        claim_ids = _resolve_claims(data.get("linked_claim_ids"), claim_id_map)
        items.append(_item(
            document_id,
            unit_kind=KIND_FIGURE,
            agent_unit_id=agent_unit_id,
            # figure_id + 正規化 caption（設計書 §5）。
            key_text=caption,
            key_refs=[agent_unit_id],
            # ``fig_3.3`` のような内部表記は label にしない → caption 先頭（§5）。
            label=_display_label(caption, figure_type),
            summary=caption,
            order_index=index,
            teaches=_teaches(claim_ids=claim_ids),
            section_ids=_id_list([location.get("section_id")]),
            source_block_ids=_id_list([location.get("caption_block_id")]),
            linked_claim_ids=claim_ids,
            linked_figure_ids=[agent_unit_id],
            agent_payload={"figure_type": figure_type},
        ))
    return items


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_learning_unit_items(
    document_id: str,
    *,
    skeleton: Any = None,
    thesis: Any = None,
    component_result: Any = None,
    dsl: Any = None,
    figures: Any = None,
    claim_id_map: Mapping[str, str] | None = None,
    component_id_map: Mapping[str, str] | None = None,
    evidence_registry: Any = None,
) -> list[dict]:
    """5 種別の学ぶ単位を導出する（決定論・非LLM）。

    Args:
        document_id: 対象 document の UUID。
        skeleton / thesis / component_result / dsl / figures: A層の結果。``None`` の
            種別は**スキップ**する（素材が無いことを「単位ゼロ」と解釈しない）。
        claim_id_map: 突合キー → claim の DB UUID（``persist_qualified_claims`` の結果）。
        component_id_map: agent component_id → DB UUID（``persist_components`` の結果）。
        evidence_registry: EvidenceRegistryResult。出典 block 集合の解決に使う。

    Returns:
        ``sync_live_rows`` の ``incoming`` 形の並び。並びは
        (``LEARNING_UNIT_KINDS`` の順, ``order_index``) で決定論。同一 run 内で
        ``stable_key`` が衝突した組には ``#2`` … が付く（:func:`dedupe_stable_keys`）。
    """
    claim_id_map = dict(claim_id_map or {})
    component_id_map = dict(component_id_map or {})

    by_kind: dict[str, list[dict]] = {kind: [] for kind in LEARNING_UNIT_KINDS}
    if skeleton is not None:
        by_kind[KIND_SECTION_BLOCK] = _section_block_units(document_id, skeleton, claim_id_map)
    if thesis is not None:
        by_kind[KIND_THESIS_SUPPORT] = _thesis_support_units(document_id, thesis, claim_id_map)
    if component_result is not None:
        by_kind[KIND_PARENT_COMPONENT] = _parent_component_units(
            document_id, component_result, claim_id_map, component_id_map, evidence_registry
        )
    if dsl is not None:
        by_kind[KIND_DSL_NODE] = _dsl_node_units(document_id, dsl, claim_id_map)
    if figures is not None:
        by_kind[KIND_FIGURE] = _figure_units(document_id, figures, claim_id_map)

    items: list[dict] = []
    for kind in LEARNING_UNIT_KINDS:
        items.extend(by_kind.get(kind, []))

    final_keys = ko_keys.dedupe_stable_keys(
        items,
        key_of=lambda item: item["stable_key"],
        agent_id_of=lambda item: item["agent_id"],
    )
    for item in items:
        item["stable_key"] = final_keys.get(item["agent_id"], item["stable_key"])
    return items


def available_kinds(
    *,
    skeleton: Any = None,
    thesis: Any = None,
    component_result: Any = None,
    dsl: Any = None,
    figures: Any = None,
) -> tuple[list[str], list[str]]:
    """``(素材のある種別, スキップした種別)`` を返す（保存側の正直な報告用）。"""
    present = {
        KIND_SECTION_BLOCK: skeleton is not None,
        KIND_THESIS_SUPPORT: thesis is not None,
        KIND_PARENT_COMPONENT: component_result is not None,
        KIND_DSL_NODE: dsl is not None,
        KIND_FIGURE: figures is not None,
    }
    return (
        [kind for kind in LEARNING_UNIT_KINDS if present.get(kind)],
        [kind for kind in LEARNING_UNIT_KINDS if not present.get(kind)],
    )
