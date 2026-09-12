"""Adapters from agent results to existing Postgres tables (issue #226)."""
from __future__ import annotations

import copy
import json
import logging
import re
import uuid
from dataclasses import asdict, is_dataclass
from typing import Any

from sqlalchemy import text as sa_text

from core.knowledge_objects import learning_units as ko_units
from core.knowledge_objects import remap as ko_remap
from core.knowledge_objects import stable_key as ko_keys
from core.knowledge_objects.schema import (
    DEFAULT_REVIEW_STATUS,
    TABLE_ARTIFACTS,
    TABLE_CLAIMS,
    TABLE_COMPONENTS,
    TABLE_DERIVATION_STEPS,
    TABLE_EQUATIONS,
    TABLE_EVIDENCE,
    TABLE_LEARNING_UNITS,
    TABLE_SYMBOLS,
    normalize_claim_type,
    normalize_component_type,
)
from core.knowledge_objects.sync import sync_live_rows
from core.llm import generate_embeddings
from core.postgres import get_session as _pg_session
from core.schema import (
    AUDIT_ENTITY_KNOWLEDGE_OBJECT,
    AUDIT_ENTITY_REVISION_RUN,
    CLAIM_ORIGIN_ATOMIC_REWRITE,
    CLAIM_ORIGIN_CLAIM_OBJECT,
    CLAIM_ORIGIN_EQUATION_SYNTHESIS,
    CLAIM_ORIGIN_SPAN,
    CLAIM_TIERS,
)

logger = logging.getLogger(__name__)

# Stage-outputs sub-key holding the per-stage agent artifacts (mirrors
# orchestrator.ARTIFACTS_KEY). Used for deep-merge of revision artifacts.
ARTIFACTS_KEY = "_artifacts"


def merge_revision_stage_outputs(existing: dict | None, payload: dict | None) -> dict:
    """Pure model of the **legacy** stage_outputs JSONB merge (#410 P0).

    知識オブジェクト層（§6 / KO6）以降、artifact の格納先は生成ログ表
    ``document_analysis_artifacts`` であり、``stage_outputs`` に ``_artifacts`` は
    書かれない。本関数は旧 blob の意味論（兄弟 artifact を潰さない deep merge）を
    記述したモデルとして残す（旧 run の blob を読み直すときの参照）。

    Mirrors the SQL exactly: non-``_artifacts`` keys are shallow-merged onto the
    existing stage_outputs, while ``_artifacts`` is merged key-by-key into the
    existing ``_artifacts`` so previously-stored artifacts are preserved. Kept as
    a pure function so the merge semantics are unit-testable without a database.
    """
    existing = dict(existing or {})
    payload = dict(payload or {})
    artifacts_delta = payload.pop(ARTIFACTS_KEY, None)
    merged = {**existing, **payload}
    if artifacts_delta is not None:
        merged[ARTIFACTS_KEY] = {
            **dict(existing.get(ARTIFACTS_KEY) or {}),
            **dict(artifacts_delta or {}),
        }
    return merged


class DeterministicFallbackPersistError(RuntimeError):
    """component_assembly の deterministic-fallback 結果を persist しようとした (#347)。

    ExportValidationGate を通らない経路（resume / 古い export_validation
    artifact）でも、fallback-only の結果を theory_components へ反映させず、
    かつ既存の通常成果物を silent に残さないために hard fail する。
    """


def _strip_nuls(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_nuls(v) for v in value]
    if isinstance(value, dict):
        return {str(k).replace("\x00", ""): _strip_nuls(v) for k, v in value.items()}
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_strip_nuls(value), ensure_ascii=False)


def _plain(value: Any) -> Any:
    """dataclass を含む agent の出力を JSON 化可能な素データにする。

    ``knowledge_objects.sync`` は dict / list をそのまま ``CAST(... AS jsonb)`` に
    渡すため、渡す前にこの関数を通す（agent 側 dataclass をそのまま JSON 化しない）。
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
        # agent の record 相当（dataclass 以外の単純オブジェクト）も素の dict にする。
        return _plain(vars(value))
    return value


def _text(value: Any) -> str:
    return str(value or "").strip()


def _id_list(values: Any) -> list[str]:
    """agent 側 ID のリストを正規化する（空文字・重複を落とし順序は保つ）。"""
    out: list[str] = []
    for value in values or []:
        item = _text(value)
        if item and item not in out:
            out.append(item)
    return out


def _record_knowledge_audit(
    session,
    *,
    document_id: str,
    run_id: str | None,
    stats: dict,
    changed_by: str | None = None,
) -> None:
    """知識オブジェクト同期の run 単位サマリを1行記帳する（KO10）。

    ``theory_review_events`` への直接 INSERT は persistence の既存例外
    （呼び出し元トランザクションに同乗する）。数値は件数のみで、本文・逐語引用は
    載せない。**監査は必須**（KO10）: INSERT の失敗は握らず、同じトランザクションの
    知識行の保存と一緒に巻き戻す（PostgreSQL では失敗した文の後の commit は
    どのみち通らないので、黙って続ける方が事故になる）。
    """
    session.execute(
        sa_text(
            """
            INSERT INTO theory_review_events (
                entity_type, entity_id, old_status, new_status, changed_by, metadata
            )
            VALUES (
                :entity_type, :entity_id, :old_status, :new_status,
                CAST(:changed_by AS uuid), CAST(:metadata AS jsonb)
            )
            """
        ),
        {
            "entity_type": AUDIT_ENTITY_KNOWLEDGE_OBJECT,
            "entity_id": _text(document_id),
            "old_status": "",
            "new_status": "synced",
            "changed_by": changed_by,
            "metadata": _json_dumps({**dict(stats or {}), "run_id": run_id}),
        },
    )



def _apply_remaps(
    session,
    *,
    document_id: str,
    run_id: str | None,
    kind: str,
    remaps: list[tuple[str, str, str]],
) -> dict:
    """:func:`core.knowledge_objects.remap.record_and_reanchor` の薄いラッパ。"""
    if not remaps:
        return {"recorded": 0, "reanchored": {}, "skipped": {}}
    return ko_remap.record_and_reanchor(
        session,
        document_id=document_id,
        run_id=run_id,
        kind=kind,
        remaps=remaps,
    )


# ---------------------------------------------------------------------------
# equation の stable_key（claim / derivation / symbol のキー材料にもなる）
# ---------------------------------------------------------------------------


def _equation_records(equations: Any) -> list[Any]:
    if equations is None:
        return []
    records = getattr(equations, "equations", None)
    if records is None and isinstance(equations, dict):
        records = equations.get("equations")
    return list(records or [])


def _equation_fields(record: Any) -> dict:
    """EquationRecord（dataclass / dict 双方）から永続化に使う素の値を取り出す。"""
    data = _plain(record)
    if not isinstance(data, dict):
        return {}
    extraction = data.get("source_extraction") or {}
    reconstruction = data.get("reconstruction") or {}
    semantics = data.get("semantics") or {}
    location = extraction.get("source_location") or {}
    latex = extraction.get("latex") or reconstruction.get("latex") or ""
    plain_text = extraction.get("plain_text") or reconstruction.get("plain_text") or ""
    return {
        "equation_id": _text(data.get("equation_id")),
        "label": _text(data.get("label")),
        "latex": _text(latex),
        "plain_text": _text(plain_text),
        "raw_text": _text(extraction.get("raw_text")),
        "block_id": _text(location.get("block_id")),
        "section_id": _text(location.get("section_id")),
        "page": location.get("page"),
        "equation_type": _text(semantics.get("equation_type")),
        "semantic_status": _text(semantics.get("semantic_status")),
        "content_hash": _text(data.get("content_hash")),
        "defined_symbols": _plain(semantics.get("defined_symbols") or []),
        "used_symbols": _id_list(semantics.get("used_symbols")),
        "input_equation_ids": _id_list(semantics.get("input_equation_ids")),
        "output_equation_ids": _id_list(semantics.get("output_equation_ids")),
        "linked_claim_ids": _id_list(semantics.get("linked_claim_ids")),
        "source_evidence_ids": _id_list(semantics.get("source_evidence_ids")),
        "needs_math_review": bool(extraction.get("needs_math_review")),
        "payload": data,
    }


def _equation_stable_key_map(document_id: str, equations: Any) -> dict[str, str]:
    """``agent equation_id -> stable_key``（純計算・DB を読まない）。

    claim の ``equation_stable_keys`` / derivation step / symbol のキー材料に使う。
    """
    out: dict[str, str] = {}
    for record in _equation_records(equations):
        fields = _equation_fields(record)
        agent_id = fields.get("equation_id") or ""
        if not agent_id:
            continue
        out[agent_id] = ko_keys.equation_stable_key(
            document_id,
            fields.get("latex"),
            fields.get("plain_text") or fields.get("raw_text"),
            fields.get("block_id"),
            fields.get("label"),
        )
    return out


def _resolved_equation_keys(agent_ids: Any, key_map: dict[str, str]) -> list[str]:
    """式 agent ID 集合 → stable_key 集合（解決できない ID はそのまま材料にする）。"""
    return [key_map.get(agent_id, agent_id) for agent_id in _id_list(agent_ids)]


# ---------------------------------------------------------------------------
# artifact（生成ログ・1 run × 1 stage = 1 行。§6 / KO6）
# ---------------------------------------------------------------------------


def _upsert_run_artifacts(session, run_id: str, artifacts: Any) -> None:
    """``document_analysis_artifacts`` に stage ごと upsert する（commit しない）。"""
    if not run_id or not isinstance(artifacts, dict) or not artifacts:
        return
    for stage, payload in artifacts.items():
        stage_name = _text(stage)
        if not stage_name:
            continue
        session.execute(
            sa_text(
                f"""
                INSERT INTO {TABLE_ARTIFACTS} (run_id, stage, payload)
                VALUES (CAST(:run_id AS uuid), :stage, CAST(:payload AS jsonb))
                ON CONFLICT (run_id, stage) DO UPDATE
                SET payload = EXCLUDED.payload, updated_at = now()
                """
            ),
            {"run_id": run_id, "stage": stage_name, "payload": _json_dumps(payload)},
        )


def load_run_artifacts(session, run_ids: list[str]) -> dict[str, dict]:
    """``{run_id: {stage: payload}}`` を生成ログ表から読む（§6）。

    旧 blob（``stage_outputs._artifacts``）が残る run では、呼び出し側が blob を
    下敷きにしてこの表の値を上書きする（表が勝つ）。
    """
    ids = [_text(rid) for rid in (run_ids or []) if _text(rid)]
    if not ids:
        return {}
    placeholders = ", ".join(f"CAST(:run_{i} AS uuid)" for i in range(len(ids)))
    params = {f"run_{i}": rid for i, rid in enumerate(ids)}
    try:
        rows = session.execute(
            sa_text(
                f"""
                SELECT run_id::text, stage, payload
                FROM {TABLE_ARTIFACTS}
                WHERE run_id IN ({placeholders})
                """
            ),
            params,
        ).fetchall()
    except Exception:
        # 生成ログ表が無い環境（migration 未適用・fake session）でも読み取りを止めない。
        logger.debug("load_run_artifacts failed; falling back to stage_outputs blob", exc_info=True)
        return {}
    out: dict[str, dict] = {}
    for row in rows or []:
        run_id = _text(row[0])
        stage = _text(row[1])
        if not run_id or not stage:
            continue
        payload = row[2]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                continue
        out.setdefault(run_id, {})[stage] = payload
    return out


def _hydrate_run_artifacts(session, run: dict | None) -> dict | None:
    """run 行の ``stage_outputs['_artifacts']`` を生成ログ表から組み立て直す。

    読み手の契約（``document_run_artifacts()`` が ``{stage: payload}`` を返す）は
    不変で、blob が残る旧 run では blob を下敷きに表の値が勝つ。
    """
    if not run:
        return run
    run_id = _text(run.get("id") or run.get("run_id"))
    if not run_id:
        return run
    stored = load_run_artifacts(session, [run_id]).get(run_id) or {}
    if not stored:
        return run
    stage_outputs = run.get("stage_outputs")
    if isinstance(stage_outputs, str):
        try:
            stage_outputs = json.loads(stage_outputs)
        except (ValueError, TypeError):
            stage_outputs = {}
    stage_outputs = dict(stage_outputs or {})
    blob = stage_outputs.get(ARTIFACTS_KEY)
    stage_outputs[ARTIFACTS_KEY] = {**(blob if isinstance(blob, dict) else {}), **stored}
    run["stage_outputs"] = stage_outputs
    return run


def claim_span_key(block_id: Any, span_id: Any) -> str:
    """``"{block_id}:{span_id}"``（文書内で一意な span の論理キー）を返す。

    rhetorical_role の ``span_id`` は **block ごとに ``span_001`` から振り直される**ため
    単独では文書内一意にならない（知識構造の見直し 2026-09-12 S-3 / F-4: 論文Aの
    claim 9行すべてが ``legacy_ids=["claim_span_001","span_001"]`` になり agent ID →
    DB 行の逆引きが 9-way に曖昧だった）。``block_id`` は DocumentStructure が採番する
    文書内一意 ID なので、両者の組は文書内で一意になる。片方でも欠ける場合は
    「一意キーは作れない」意味で空文字を返す（推測でキーを作らない）。
    """
    block = str(block_id or "").strip()
    span = str(span_id or "").strip()
    if not block or not span:
        return ""
    return f"{block}:{span}"


def _claim_legacy_keys(claim: dict) -> set[str]:
    """claim（span）1件の突合キー集合。

    既存の ``claim_id`` / ``span_id`` 分岐はそのまま維持しつつ（旧データ・旧参照の
    解決を落とさない）、``block_id`` も渡された場合は文書内一意な
    ``"{block_id}:{span_id}"``（:func:`claim_span_key`）を追加する。読み側はいずれも
    「集合に含まれるか」で突合しているので、キーの追加は解決率を上げるだけで
    既存の突合を壊さない。
    """
    keys: set[str] = set()
    claim_id = claim.get("claim_id")
    span_id = claim.get("span_id")
    if claim_id:
        keys.add(str(claim_id))
    # 知識オブジェクト層（KO4）: claim object も1行ずつ保存されるので、その行を
    # 出した agent 側 ID も突合キーにする（claim_id は DB UUID なので別物）。
    agent_claim_id = claim.get("agent_claim_id")
    if agent_claim_id:
        keys.add(str(agent_claim_id))
    if span_id:
        safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(span_id))
        keys.add(str(span_id))
        keys.add(f"claim_{safe}")
        unique_key = claim_span_key(claim.get("block_id"), span_id)
        if unique_key:
            keys.add(unique_key)
    return keys


# ---------------------------------------------------------------------------
# thesis_context / thesis_refs (hierarchical_context_explanation_design.md §4)
# ---------------------------------------------------------------------------


def _thesis_ref_nodes(thesis_result: Any) -> list[dict]:
    """Flatten a ThesisReconstructionResult-like artifact into ref nodes.

    Mirrors ``component_assembly/input_builder.py``'s ``_thesis_nodes`` (same
    ``thesis_ref`` / ``kind`` vocabulary: ``"central_thesis"`` for the central
    node, ``f"support:{section}:{idx}"`` for each ``support_structure`` entry)
    so a persisted claim's ``thesis_refs`` and a persisted component's
    ``thesis_context.supports_thesis_node_ids`` use the exact same identifiers
    and can be cross-referenced without any translation step.
    """
    if not thesis_result:
        return []
    central = getattr(thesis_result, "central_thesis", None)
    if not isinstance(central, dict):
        central = {}
    nodes: list[dict] = [{
        "thesis_ref": "central_thesis",
        "kind": "central_thesis",
        "text": str(central.get("text") or ""),
        "claim_ids": list(central.get("claim_ids") or []),
    }]
    support_structure = getattr(thesis_result, "support_structure", None)
    if isinstance(support_structure, dict):
        for section, entries in support_structure.items():
            if not isinstance(entries, list):
                continue
            for idx, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                nodes.append({
                    "thesis_ref": f"support:{section}:{idx}",
                    "kind": str(section),
                    "text": str(entry.get("text") or ""),
                    "claim_ids": list(entry.get("claim_ids") or []),
                })
    return nodes


def _truncate_excerpt(text: str, limit: int = 240) -> str:
    excerpt = str(text or "").strip()
    if len(excerpt) <= limit:
        return excerpt
    return excerpt[:limit].rstrip() + "…"


def _claim_thesis_ref_index(thesis_result: Any) -> dict[str, list[dict]]:
    """Invert thesis ref nodes into ``{agent claim key: [{thesis_ref, kind, text_excerpt}]}``.

    Keys are the same span-derived identifiers ``_claim_legacy_keys`` produces
    for a persisted claim (its ``span_id`` and ``claim_{safe(span_id)}``), so
    matching a about-to-be-persisted span's keys against this index is a plain
    dict lookup — no separate ID-canonicalization machinery is introduced here.
    Claim references that resolve to neither key (e.g. equation-claim-synthesis
    IDs like ``synth_claim_0001``, which never become a ``theory_claims`` row,
    or counter-suffixed atomic sub-claim ids) are simply not found. This is
    deliberate: only exact matches are recorded, nothing is guessed.
    """
    index: dict[str, list[dict]] = {}
    for node in _thesis_ref_nodes(thesis_result):
        ref_entry = {
            "thesis_ref": node["thesis_ref"],
            "kind": node["kind"],
            "text_excerpt": _truncate_excerpt(node["text"]),
        }
        for claim_id in node.get("claim_ids") or []:
            key = str(claim_id or "").strip()
            if not key:
                continue
            bucket = index.setdefault(key, [])
            if not any(e["thesis_ref"] == ref_entry["thesis_ref"] for e in bucket):
                bucket.append(ref_entry)
    return index


def _claim_thesis_refs_for_span(
    span_id: Any,
    thesis_ref_index: dict[str, list[dict]],
) -> list[dict]:
    """Look up the thesis refs backing one qualified span (by span_id)."""
    if not thesis_ref_index or not span_id:
        return []
    matches: list[dict] = []
    seen_refs: set[str] = set()
    for key in sorted(_claim_legacy_keys({"span_id": span_id})):
        for entry in thesis_ref_index.get(key, []):
            if entry["thesis_ref"] in seen_refs:
                continue
            seen_refs.add(entry["thesis_ref"])
            matches.append(entry)
    matches.sort(key=lambda e: (e["kind"], e["thesis_ref"]))
    return matches


def _component_thesis_context(comp: Any) -> dict | None:
    """Build ``theory_components.thesis_context`` from a ComponentRecord.

    Transcribes ``role_in_thesis`` / ``supports_thesis_node_ids`` /
    ``support_role`` / ``support_distance_to_headline_claim`` — fields the
    component_assembly agent already derives deterministically (issue #354 /
    #440) but that were previously artifact-only (never reaching the DB).
    Returns ``None`` (persisted as SQL NULL) when all four are at their empty
    default, so old rows and rows with genuinely no thesis backing look alike.
    """
    role_in_thesis = str(getattr(comp, "role_in_thesis", "") or "")
    supports_thesis_node_ids = list(getattr(comp, "supports_thesis_node_ids", []) or [])
    support_role = str(getattr(comp, "support_role", "") or "")
    support_distance = int(getattr(comp, "support_distance_to_headline_claim", 0) or 0)
    if not (role_in_thesis or supports_thesis_node_ids or support_role or support_distance):
        return None
    return {
        "role_in_thesis": role_in_thesis,
        "supports_thesis_node_ids": supports_thesis_node_ids,
        "support_role": support_role,
        "support_distance_to_headline_claim": support_distance,
    }


def _remap_string_list(values: Any, id_map: dict[str, str]) -> list[str]:
    out: list[str] = []
    for value in values or []:
        mapped = id_map.get(str(value), str(value))
        if mapped not in out:
            out.append(mapped)
    return out


def _remap_nested_claim_refs(value: Any, id_map: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [_remap_nested_claim_refs(item, id_map) for item in value]
    if not isinstance(value, dict):
        return value
    out = {
        key: _remap_nested_claim_refs(item, id_map)
        for key, item in value.items()
    }
    for key in ("claim_ids", "evidence_claims", "linked_claim_ids"):
        if isinstance(out.get(key), list):
            out[key] = _remap_string_list(out[key], id_map)
    return out


# ---------------------------------------------------------------------------
# chunks
# ---------------------------------------------------------------------------


def persist_source_chunks(
    *,
    document_id: str,
    material_id: str,
    chunks: list,
) -> list[dict]:
    """source chunk を embedding して `chunks` テーブルに保存する。

    旧実装と同じカラム (text/embedding/material_id/document_id/page_start/page_end)
    に加え、issue #226 で追加された section_id / block_ids / source_metadata を
    埋める。

    Returns:
        各 chunk について {chunk_id, chunk_index, section_id, block_ids,
        page_start, page_end, text} の dict を返す。後段 agent の evidence ref
        を解決する際に使う。
    """
    if not chunks:
        return []

    texts = [str(c.text or "") for c in chunks]
    # 空テキストを embedding に投げるとエラーになるため、空でもプレースホルダ化
    safe_texts = [t if t.strip() else " " for t in texts]
    embeddings = generate_embeddings(safe_texts)

    saved: list[dict] = []
    session = _pg_session()
    try:
        # 同じ document の既存 chunk を消してから入れ直す（再実行時の整合のため）
        session.execute(
            sa_text("DELETE FROM chunks WHERE document_id = CAST(:doc_id AS uuid)"),
            {"doc_id": document_id},
        )
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            chunk_id = uuid.uuid4()
            session.execute(
                sa_text(
                    """
                    INSERT INTO chunks (
                        id, document_id, chunk_index, text, embedding,
                        display_text, spoken_text, formulas, latex_formulas,
                        material_id, page_start, page_end,
                        section_id, block_ids, source_metadata
                    )
                    VALUES (
                        :id, CAST(:doc_id AS uuid), :idx, :text, :embedding,
                        :display_text, :spoken_text, CAST(:formulas AS jsonb),
                        :latex_formulas,
                        :material_id, :page_start, :page_end,
                        :section_id, CAST(:block_ids AS jsonb),
                        CAST(:metadata AS jsonb)
                    )
                    """
                ),
                {
                    "id": chunk_id,
                    "doc_id": document_id,
                    "idx": chunk.chunk_index,
                    "text": _strip_nuls(chunk.text or ""),
                    "embedding": str(list(embedding)),
                    "display_text": _strip_nuls(chunk.text or ""),
                    "spoken_text": _strip_nuls(_spoken_text_from_formulas(chunk.text or "", getattr(chunk, "formulas", []) or [])),
                    "formulas": _json_dumps(getattr(chunk, "formulas", []) or []),
                    "latex_formulas": [
                        _strip_nuls(str(f.get("latex") or ""))
                        for f in (getattr(chunk, "formulas", []) or [])
                        if isinstance(f, dict) and f.get("latex")
                    ],
                    "material_id": material_id,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "section_id": chunk.section_id,
                    "block_ids": _json_dumps(chunk.block_ids),
                    "metadata": _json_dumps(chunk.metadata),
                },
            )
            saved.append({
                "chunk_id": str(chunk_id),
                "chunk_index": chunk.chunk_index,
                "section_id": chunk.section_id,
                "block_ids": list(chunk.block_ids),
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "text": chunk.text,
                "formulas": list(getattr(chunk, "formulas", []) or []),
            })
        session.commit()
        logger.info(
            "Persisted %d source chunks for document %s", len(saved), document_id
        )
        return saved
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def persist_equation_previews_to_chunks(document_id: str, equations: Any) -> int:
    """Persist reconstructed equation previews into chunk display_text/formulas.

    ``chunks.text`` remains the raw source chunk for auditability. This updates
    display-oriented fields so other UI/API consumers that use chunks directly
    see formula placeholders instead of broken PDF math fragments.
    """
    previews = _equation_previews(equations)
    if not previews:
        return 0

    session = _pg_session()
    updated = 0
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT id, display_text, spoken_text, formulas, page_start, page_end
                FROM chunks
                WHERE document_id = CAST(:doc_id AS uuid)
                ORDER BY chunk_index
                """
            ),
            {"doc_id": document_id},
        ).fetchall()
        for row in rows:
            chunk_id = str(row[0])
            display_text = row[1] or ""
            spoken_text = row[2] or display_text
            formulas = row[3] if isinstance(row[3], list) else []
            page_start = row[4]
            page_end = row[5]

            merged = _merge_equation_previews_for_chunk(formulas, previews, page_start, page_end)
            patched_display = _replace_equation_preview_text(display_text, merged)
            patched_spoken = _spoken_text_from_formulas(patched_display, merged)
            if patched_display == display_text and _json_dumps(merged) == _json_dumps(formulas):
                continue

            session.execute(
                sa_text(
                    """
                    UPDATE chunks
                    SET display_text = :display_text,
                        spoken_text = :spoken_text,
                        formulas = CAST(:formulas AS jsonb),
                        latex_formulas = :latex_formulas
                    WHERE id = CAST(:chunk_id AS uuid)
                    """
                ),
                {
                    "chunk_id": chunk_id,
                    "display_text": _strip_nuls(patched_display),
                    "spoken_text": _strip_nuls(patched_spoken),
                    "formulas": _json_dumps(merged),
                    "latex_formulas": [
                        _strip_nuls(str(f.get("latex") or ""))
                        for f in merged
                        if isinstance(f, dict) and f.get("latex")
                    ],
                },
            )
            updated += 1
        session.commit()
        if updated:
            logger.info("Persisted equation previews into %d chunks for document %s", updated, document_id)
        return updated
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _equation_previews(equations: Any) -> list[dict]:
    records = getattr(equations, "equations", []) or []
    previews: list[dict] = []
    for record in records:
        src = getattr(record, "source_extraction", None)
        rec = getattr(record, "reconstruction", None)
        if not src:
            continue
        source_location = dict(getattr(src, "source_location", {}) or {})
        source_image = getattr(src, "source_image", None)
        latex = getattr(rec, "latex", None) if rec and getattr(rec, "status", "none") != "none" else getattr(src, "latex", None)
        plain_text = getattr(rec, "plain_text", None) if rec and getattr(rec, "status", "none") != "none" else getattr(src, "plain_text", None)
        previews.append({
            "id": getattr(record, "equation_id", "") or f"eq_{len(previews)}",
            "latex": latex or "",
            "spoken": plain_text or "",
            "is_display": True,
            "label": getattr(record, "label", None),
            "block_id": source_location.get("block_id"),
            "source_location": source_location,
            "source_image": source_image if isinstance(source_image, dict) else None,
            "raw_text": getattr(src, "raw_text", "") or "",
            "needs_math_review": bool(getattr(src, "needs_math_review", False)),
            "review_reason": list(getattr(src, "review_reason", []) or []),
        })
    return previews


def _merge_equation_previews_for_chunk(
    formulas: list[dict],
    previews: list[dict],
    page_start: int | None,
    page_end: int | None,
) -> list[dict]:
    merged = [dict(f) for f in formulas if isinstance(f, dict)]
    by_block = {str(f.get("block_id")): f for f in merged if f.get("block_id")}
    by_id = {str(f.get("id")): f for f in merged if f.get("id")}
    for preview in previews:
        if not _preview_matches_page(preview, page_start, page_end):
            continue
        target = by_block.get(str(preview.get("block_id") or "")) or by_id.get(str(preview.get("id") or ""))
        if target is None:
            merged.append(dict(preview))
        else:
            target.update({k: v for k, v in preview.items() if v not in (None, "", [])})
    return merged


def _preview_matches_page(preview: dict, page_start: int | None, page_end: int | None) -> bool:
    loc = preview.get("source_location") if isinstance(preview.get("source_location"), dict) else {}
    try:
        page = int(loc.get("page"))
    except Exception:
        return False
    if page_start is None and page_end is None:
        return False
    start = page_start if page_start is not None else page
    end = page_end if page_end is not None else start
    return int(start) <= page <= int(end)


def _replace_equation_preview_text(display_text: str, formulas: list[dict]) -> str:
    updated = display_text or ""
    for formula in formulas or []:
        if not isinstance(formula, dict):
            continue
        raw_text = str(formula.get("raw_text") or "").strip()
        formula_id = str(formula.get("id") or "").strip()
        if not raw_text or not formula_id:
            continue
        placeholder = formula_id if formula_id.startswith("[[") else f"[[{formula_id}]]"
        for variant in sorted(_equation_text_variants(raw_text), key=len, reverse=True):
            if len(variant.strip()) >= 4:
                updated = updated.replace(variant, placeholder)
    return updated


def _equation_text_variants(raw_text: str) -> list[str]:
    lines = [ln.strip() for ln in str(raw_text or "").strip().splitlines() if ln.strip()]
    if not lines:
        return []
    return list({
        str(raw_text).strip(),
        "\n".join(lines),
        "\n\n".join(lines),
        " ".join(lines),
    })


def _spoken_text_from_formulas(text: str, formulas: list[dict]) -> str:
    spoken = text or ""
    for idx, formula in enumerate(formulas or []):
        if not isinstance(formula, dict):
            continue
        placeholder = str(formula.get("id") or f"[[FORMULA_{idx}]]")
        replacement = str(formula.get("spoken") or "")
        if not replacement:
            label = str(formula.get("label") or "").strip()
            replacement = f"equation {label}" if label else f"equation {idx + 1}"
        spoken = spoken.replace(placeholder, replacement)
    return spoken


# ---------------------------------------------------------------------------
# theory_claims
# ---------------------------------------------------------------------------


_VALID_CLAIM_TYPES = {
    "definition", "assumption", "approximation", "equation", "relation",
    "derivation_step", "observable_definition", "correction",
    "uncertainty", "limitation", "result", "diagnostic_claim",
    "equation_definition", "equation_relation", "equation_transformation",
    "equation_approximation", "equation_constraint",
}


def _normalize_claim_type(qualification: dict | None) -> str:
    if not isinstance(qualification, dict):
        return "diagnostic_claim"
    for key in ("claim_type", "tier", "type"):
        v = qualification.get(key)
        if isinstance(v, str) and v in _VALID_CLAIM_TYPES:
            return v
    return "diagnostic_claim"


def _evidence_block_index(evidence_registry: Any) -> dict[str, str]:
    """``evidence_id -> block_id``（EvidenceRegistry の逆引き）。

    evidence_id は文書内で一意で、``EvidenceRecord.source.block_id`` は
    その evidence が属する block そのものなので、この写像は曖昧にならない。
    orchestrator の figure クロスリンク（``_build_figure_table_semantics`` の
    ``claim_link_index``）が使う joins のうち **(1) の経路と同一**。
    """
    index: dict[str, str] = {}
    for record in getattr(evidence_registry, "records", []) or []:
        ev_id = str(getattr(record, "evidence_id", "") or "").strip()
        block_id = str(
            getattr(getattr(record, "source", None), "block_id", "") or ""
        ).strip()
        if ev_id and block_id:
            index.setdefault(ev_id, block_id)
    return index


def _claim_object_ids_by_span_key(
    qualified_result: Any,
    claim_objects: Any,
    evidence_registry: Any = None,
) -> dict[str, list[str]]:
    """``"{block_id}:{span_id}" -> [claim_object の claim_id, ...]`` を組む。

    ClaimObjectBuilder の ``ClaimObjectRecord`` は **block_id 相当のフィールドを
    持たない**（``source_span_ids`` / ``source_evidence_ids`` / ``section_id`` のみ）。
    ``source_span_ids`` の span_id は block ごとに振り直されるため単独では
    block を跨いで衝突する。そこで orchestrator の figure クロスリンクと同じ
    2つの join を、向きを逆にして使う:

    1. **evidence 経路（主・曖昧さなし）** — ``claim.source_evidence_ids`` →
       EvidenceRegistry の ``source.block_id``。builder は claim の evidence を
       その claim 自身の block からのみ解決する（``_resolve_evidence_ids`` /
       ``_refine_evidence_ids`` とも block スコープ）ので、evidence_id → block_id は
       一意に決まる。
    2. **span_id 一意経路（従）** — ``span_id`` が文書内でちょうど1つの block に
       しか現れない場合に限り、その block を採用する。

    どちらでも block が1つに絞れない claim（両方が空 / 複数 block に割れる）は
    **何も記録しない**。artifact 側には元の対応が残っているので情報は落ちておらず、
    ここで推測して別の span に誤って結び付ける方が害が大きい
    （``_claim_thesis_ref_index`` と同じ「完全一致だけを記録する」方針）。

    親 claim が解決できた場合はその ``subclaim_ids``（atomic rewrite の子）も
    同じ span に載せる。子自身も 1./2. の経路で解決できることが多いが、
    子の evidence が文単位に絞り込まれている場合（#363）でもその文 record は
    同じ block を指すため、いずれの経路でも同じ span に着地する。
    """
    spans = list(getattr(qualified_result, "qualified_spans", []) or [])
    claims = list(getattr(claim_objects, "claims", []) or [])
    if not spans or not claims:
        return {}

    # span_id -> {block_id}（一意に決まるときだけ従経路で使う）
    span_to_blocks: dict[str, set[str]] = {}
    # 実在する (block_id, span_id) の組だけを受け付ける（捏造キーを作らない）
    known_span_keys: set[str] = set()
    for span in spans:
        span_id = str(getattr(span, "span_id", "") or "").strip()
        block_id = str(getattr(span, "block_id", "") or "").strip()
        if not span_id or not block_id:
            continue
        span_to_blocks.setdefault(span_id, set()).add(block_id)
        known_span_keys.add(claim_span_key(block_id, span_id))

    evidence_blocks = _evidence_block_index(evidence_registry)

    by_id = {
        str(getattr(c, "claim_id", "") or ""): c
        for c in claims
        if str(getattr(c, "claim_id", "") or "")
    }

    index: dict[str, list[str]] = {}

    def _record(span_key: str, claim_id: str) -> None:
        bucket = index.setdefault(span_key, [])
        if claim_id not in bucket:
            bucket.append(claim_id)

    def _span_key_for(claim: Any) -> str:
        span_ids = [
            str(v or "").strip()
            for v in (getattr(claim, "source_span_ids", []) or [])
        ]
        span_ids = [v for v in span_ids if v]
        if not span_ids:
            return ""
        blocks = {
            evidence_blocks[ev_id]
            for ev_id in (
                str(v or "").strip()
                for v in (getattr(claim, "source_evidence_ids", []) or [])
            )
            if ev_id in evidence_blocks
        }
        if not blocks:
            # 従経路: span_id が1 block にしか現れないときだけ採用する。
            blocks = {
                next(iter(span_to_blocks[sid]))
                for sid in span_ids
                if len(span_to_blocks.get(sid, ())) == 1
            }
        candidates = {
            key
            for block_id in blocks
            for sid in span_ids
            if (key := claim_span_key(block_id, sid)) in known_span_keys
        }
        return next(iter(candidates)) if len(candidates) == 1 else ""

    for claim_id, claim in by_id.items():
        span_key = _span_key_for(claim)
        if not span_key:
            continue
        _record(span_key, claim_id)
        for sub_id in getattr(claim, "subclaim_ids", []) or []:
            sub_id = str(sub_id or "").strip()
            if sub_id and sub_id in by_id:
                _record(span_key, sub_id)

    return {key: sorted(values) for key, values in index.items()}


_CLAIM_CONTENT_COLUMNS = (
    "chunk_id", "source_scope", "claim_type", "claim_type_text", "text",
    "normalized_text", "concepts", "equation", "support_status", "evidence_text",
    "thesis_refs", "origin", "claim_tier", "content_hash",
)

#: 人間の確定列（一致時に触らない。§5.3）。
_CLAIM_PRESERVED_COLUMNS = ("review_status", "created_by")

_CLAIM_COLUMN_CASTS = {"chunk_id": "uuid"}


def _claim_span_index(spans: list) -> tuple[dict, dict]:
    """``{span_key: span}`` と ``{block_id: span_key}`` 的な索引をまとめて作る。"""
    span_by_key: dict[str, Any] = {}
    for span in spans:
        key = claim_span_key(getattr(span, "block_id", None), getattr(span, "span_id", None))
        if key and key not in span_by_key:
            span_by_key[key] = span
    return span_by_key, {}


def _claim_object_span_keys(claim_ids_by_span_key: dict[str, list[str]]) -> dict[str, str]:
    """``claim_object の claim_id -> span_key``（1対1に決まるものだけ）。

    :func:`_claim_object_ids_by_span_key` の逆引き。複数 span に跨る claim は
    **記録しない**（推測で1つに寄せない）。
    """
    owners: dict[str, set[str]] = {}
    for span_key, claim_ids in (claim_ids_by_span_key or {}).items():
        for claim_id in claim_ids:
            owners.setdefault(str(claim_id), set()).add(span_key)
    return {
        claim_id: next(iter(keys))
        for claim_id, keys in owners.items()
        if len(keys) == 1
    }


def _build_claim_items(
    *,
    document_id: str,
    spans: list,
    claim_objects: Any,
    claim_ids_by_span_key: dict[str, list[str]],
    evidence_blocks: dict[str, str],
    block_to_chunk: dict[str, str],
    thesis_ref_index: dict,
    equation_keys: dict[str, str],
) -> list[dict]:
    """claim object（親 → 子 → 式由来合成）+ 残りの span を同期用 item 列にする（§5.4）。

    1 item = ``{"agent_id", "stable_key", "values", "span_id", "block_id", "text",
    "parent_agent_id", "legacy_ids"}``。同じ stable_key を持つ span は claim object の
    item に legacy_ids を合流させて **行を二重に作らない**（同じ命題を2行にしない）。
    """
    span_by_key, _ = _claim_span_index(spans)
    span_key_of_claim = _claim_object_span_keys(claim_ids_by_span_key)

    items: list[dict] = []
    # claim object の item だけを stable_key で索く（span 同士は合流させない。
    # 同じキーの span が2本あるのは「同じ命題が2箇所にある」ので、行は分けたまま
    # dedupe_stable_keys が #2 … で区別する = KO2）。
    by_key: dict[str, dict] = {}

    def _register(item: dict, *, indexed: bool = False) -> None:
        items.append(item)
        if indexed:
            by_key.setdefault(item["stable_key"], item)

    for record in (getattr(claim_objects, "claims", None) or []):
        data = _plain(record)
        if not isinstance(data, dict):
            continue
        agent_id = _text(data.get("claim_id"))
        if not agent_id:
            continue
        text_value = str(data.get("text") or "")
        normalized = str(data.get("normalized_text") or "") or text_value
        evidence_ids = _id_list(data.get("source_evidence_ids"))
        block_ids = _id_list(
            evidence_blocks[ev_id] for ev_id in evidence_ids if ev_id in evidence_blocks
        )
        span = span_by_key.get(span_key_of_claim.get(agent_id, ""))
        span_block = _text(getattr(span, "block_id", "")) if span is not None else ""
        span_id = getattr(span, "span_id", None) if span is not None else None
        if not block_ids and span_block:
            block_ids = [span_block]
        primary_block = block_ids[0] if block_ids else (span_block or "")

        parent_agent_id = _text(data.get("parent_claim_id"))
        synthesis_method = _text(data.get("synthesis_method"))
        if parent_agent_id:
            origin = CLAIM_ORIGIN_ATOMIC_REWRITE
        elif synthesis_method or agent_id.startswith("synth_claim_"):
            origin = CLAIM_ORIGIN_EQUATION_SYNTHESIS
        else:
            origin = CLAIM_ORIGIN_CLAIM_OBJECT

        qualification = getattr(span, "qualification", None) if span is not None else None
        tier = qualification.get("tier") if isinstance(qualification, dict) else ""
        claim_tier = _text(tier) if _text(tier) in CLAIM_TIERS else ""

        equation_ids = _id_list(data.get("equation_ids"))
        equation_payload = (
            {
                "equation_ids": equation_ids,
                "equation_stable_keys": _resolved_equation_keys(equation_ids, equation_keys),
            }
            if equation_ids else {}
        )
        raw_claim_type = _text(data.get("claim_type"))
        thesis_refs = _claim_thesis_refs_for_span(span_id, thesis_ref_index)
        legacy_ids = {agent_id}
        _register(indexed=True, item={
            "agent_id": agent_id,
            "stable_key": ko_keys.claim_stable_key(document_id, normalized, block_ids),
            "parent_agent_id": parent_agent_id,
            "span_id": span_id,
            "block_id": primary_block or None,
            "chunk_id": block_to_chunk.get(primary_block or ""),
            "text": text_value,
            "legacy_ids": legacy_ids,
            "values": {
                "chunk_id": block_to_chunk.get(primary_block or "") or None,
                "source_scope": {
                    "section_id": data.get("section_id"),
                    "block_id": primary_block or None,
                    "span_id": span_id,
                    "legacy_ids": sorted(legacy_ids),
                    "qualification_reason": _text(data.get("qualification_reason") or ""),
                },
                "claim_type": normalize_claim_type(raw_claim_type),
                "claim_type_text": raw_claim_type,
                "text": text_value,
                "normalized_text": normalized,
                "concepts": _plain(data.get("concepts") or []),
                "equation": equation_payload,
                "support_status": _text(data.get("support_status")) or "source_backed",
                # PDF 原文根拠は EvidenceRegistry に委任する (#257)。
                "evidence_text": "",
                "thesis_refs": thesis_refs or None,
                "origin": origin,
                "claim_tier": claim_tier,
                "content_hash": _text(data.get("content_hash")),
                "review_status": DEFAULT_REVIEW_STATUS,
            },
        })

    for span in spans:
        qualification = getattr(span, "qualification", {}) or {}
        decision = qualification.get("decision") if isinstance(qualification, dict) else None
        if decision == "rejected":
            continue
        block_id = getattr(span, "block_id", None)
        span_id = getattr(span, "span_id", None)
        text_value = str(getattr(span, "text", "") or "")
        span_key = claim_span_key(block_id, span_id)
        legacy_ids = _claim_legacy_keys({"span_id": span_id, "block_id": block_id})
        legacy_ids.update(claim_ids_by_span_key.get(span_key, []))
        stable_key = ko_keys.claim_stable_key(
            document_id, text_value, [block_id] if block_id else []
        )
        existing = by_key.get(stable_key)
        if existing is not None:
            # §5.4-2: 同じ命題を span 行と claim object 行の2本に分けて持たない。
            # span 側の突合キー（span_id / claim_{span} / block:span）は claim object の
            # 行に合流させるので、読み手の解決は落ちない（P4）。
            existing["legacy_ids"] |= legacy_ids
            existing["values"]["source_scope"]["legacy_ids"] = sorted(existing["legacy_ids"])
            if not existing.get("span_id"):
                existing["span_id"] = span_id
                existing["values"]["source_scope"]["span_id"] = span_id
            if not existing.get("block_id") and block_id:
                existing["block_id"] = block_id
                existing["values"]["source_scope"]["block_id"] = block_id
                existing["values"]["chunk_id"] = block_to_chunk.get(block_id) or None
            continue
        chunk_id = block_to_chunk.get(block_id or "")
        thesis_refs = _claim_thesis_refs_for_span(span_id, thesis_ref_index)
        _register({
            "agent_id": span_key or _text(span_id),
            "stable_key": stable_key,
            "parent_agent_id": "",
            "span_id": span_id,
            "block_id": block_id,
            "chunk_id": chunk_id,
            "text": text_value,
            "legacy_ids": set(legacy_ids),
            "values": {
                "chunk_id": chunk_id or None,
                "source_scope": {
                    "section_id": getattr(span, "section_id", None),
                    "block_id": block_id,
                    "span_id": span_id,
                    "legacy_ids": sorted(legacy_ids),
                    # span.reason は LLM 判定理由 (review note) であり PDF 原文根拠ではない。
                    # source-backed 判定の根拠として evidence_text に保存しない (#257)。
                    "qualification_reason": _text(getattr(span, "reason", "") or ""),
                },
                "claim_type": normalize_claim_type(_normalize_claim_type(qualification)),
                "claim_type_text": _text(
                    qualification.get("claim_type_candidate")
                    or qualification.get("claim_type")
                    if isinstance(qualification, dict) else ""
                ),
                "text": text_value,
                "normalized_text": text_value,
                "concepts": [],
                "equation": {},
                "support_status": "source_backed",
                "evidence_text": "",
                "thesis_refs": thesis_refs or None,
                "origin": CLAIM_ORIGIN_SPAN,
                "claim_tier": (
                    _text(qualification.get("tier"))
                    if isinstance(qualification, dict)
                    and _text(qualification.get("tier")) in CLAIM_TIERS else ""
                ),
                "content_hash": "",
                "review_status": DEFAULT_REVIEW_STATUS,
            },
        })

    # 同一 run 内の stable_key 衝突は agent ID 昇順で #2 … を付ける（KO2）。
    final_keys = ko_keys.dedupe_stable_keys(
        items,
        key_of=lambda item: item["stable_key"],
        agent_id_of=lambda item: item["agent_id"],
    )
    for item in items:
        item["stable_key"] = final_keys.get(item["agent_id"], item["stable_key"])
    return items


def persist_qualified_claims(
    *,
    document_id: str,
    qualified_result,
    chunk_index: list[dict],
    thesis_result: Any = None,
    claim_objects: Any = None,
    evidence_registry: Any = None,
    equations: Any = None,
    run_id: str | None = None,
) -> list[dict]:
    """claim を `theory_claims` に **同期**する（knowledge_objects_design.md §5.4 / KO3・KO4）。

    かつては「document 単位の DELETE → 再 INSERT」だったため、再解析のたびに UUID が
    変わり C層の承認・D層の台帳・R層の産出物が宙に浮いていた（S-6 / S-14）。現在は
    内容由来の ``stable_key``（:mod:`core.knowledge_objects.stable_key`）で live 行と
    突合し、一致は同じ UUID のまま内容列を更新、一致しない旧 live 行は
    ``superseded_at`` を刻んで残す。**DELETE は発行しない**。

    保存対象は span だけでなく claim object（親 / atomic 子 / 式由来合成）も含み、
    それぞれ ``origin`` を持つ（KO4: artifact にしか無い知識を残さない）。

    Args:
        chunk_index: persist_source_chunks の戻り値。block_id → chunk_id の解決に使う。
        thesis_result: ThesisReconstructionResult（省略可）。`thesis_refs` に
            `[{"thesis_ref", "kind", "text_excerpt"}]` を保存する。
        claim_objects: ClaimObjectBuildResult（省略可）。指定されると claim object も
            1行ずつ保存し、対応する span の突合キーを同じ行に合流させる。
        evidence_registry: EvidenceRegistryResult（省略可）。claim object → block_id の
            主経路（evidence_id → `source.block_id`）。stable_key の材料でもある。
        equations: EquationSemanticsResult（省略可）。`equation.equation_stable_keys` の
            材料（純計算・DB は読まない）。
        run_id: この保存を出した run（``produced_by_run_id`` / supersede の刻印）。

    Returns:
        ``[{claim_id, agent_claim_id, span_id, block_id, chunk_id, text, stable_key,
        legacy_ids}]``。呼び出し側（orchestrator の ``claim_id_map``）が **全 claim の
        agent ID** → UUID を引けるように、突合キーを ``legacy_ids`` で併せて返す。
    """
    block_to_chunk: dict[str, str] = {}
    for ch in chunk_index or []:
        for bid in ch.get("block_ids") or []:
            block_to_chunk.setdefault(bid, ch["chunk_id"])

    spans = list(getattr(qualified_result, "qualified_spans", []) or [])
    thesis_ref_index = _claim_thesis_ref_index(thesis_result)
    claim_ids_by_span_key = _claim_object_ids_by_span_key(
        qualified_result, claim_objects, evidence_registry
    )
    evidence_blocks = _evidence_block_index(evidence_registry)
    equation_keys = _equation_stable_key_map(document_id, equations)

    items = _build_claim_items(
        document_id=document_id,
        spans=spans,
        claim_objects=claim_objects,
        claim_ids_by_span_key=claim_ids_by_span_key,
        evidence_blocks=evidence_blocks,
        block_to_chunk=block_to_chunk,
        thesis_ref_index=thesis_ref_index,
        equation_keys=equation_keys,
    )

    session = _pg_session()
    try:
        # S-7 の早期 return は撤去する: incoming が空でも同期を走らせ、旧 live 行を
        # superseded にする（「今回の解析には無い」ことを行の状態として残す）。
        sync = sync_live_rows(
            session,
            table=TABLE_CLAIMS,
            document_id=document_id,
            run_id=run_id,
            incoming=items,
            content_columns=_CLAIM_CONTENT_COLUMNS,
            preserved_columns=_CLAIM_PRESERVED_COLUMNS,
            agent_id_column="agent_claim_id",
            column_casts=_CLAIM_COLUMN_CASTS,
        )

        # 親子（atomic rewrite）は2パス: 全行を同期したあとに parent_claim_id を解く。
        for item in items:
            parent_agent_id = item.get("parent_agent_id") or ""
            child_id = sync.id_map.get(item["agent_id"])
            parent_id = sync.id_map.get(parent_agent_id) if parent_agent_id else None
            if child_id and parent_id and child_id != parent_id:
                session.execute(
                    sa_text(
                        f"""
                        UPDATE {TABLE_CLAIMS}
                        SET parent_claim_id = CAST(:parent_id AS uuid), updated_at = now()
                        WHERE id = CAST(:child_id AS uuid)
                        """
                    ),
                    {"parent_id": parent_id, "child_id": child_id},
                )

        remap_summary = _apply_remaps(
            session,
            document_id=document_id,
            run_id=run_id,
            kind="claim",
            remaps=sync.remaps,
        )
        _record_knowledge_audit(
            session,
            document_id=document_id,
            run_id=run_id,
            stats={"claim": sync.stats, "remap": remap_summary},
        )

        saved: list[dict] = []
        for item in items:
            db_id = sync.id_map.get(item["agent_id"])
            if not db_id:
                continue
            saved.append({
                "claim_id": db_id,
                "agent_claim_id": item["agent_id"],
                "span_id": item.get("span_id"),
                # 呼び出し側（orchestrator の claim_id_map）が _claim_legacy_keys で
                # 文書内一意キーまで引けるように block_id も返す。
                "block_id": item.get("block_id"),
                "chunk_id": item.get("chunk_id"),
                "text": item.get("text") or "",
                "stable_key": item["stable_key"],
                "legacy_ids": sorted(item.get("legacy_ids") or set()),
            })
        session.commit()
        logger.info(
            "Synced theory_claims for document %s: %s (remap=%s)",
            document_id, sync.stats, remap_summary.get("recorded", 0),
        )
        return saved
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

# ---------------------------------------------------------------------------
# theory_components & links
# ---------------------------------------------------------------------------


_COMPONENT_CONTENT_COLUMNS = (
    "course_id", "name", "component_type", "component_type_text", "summary",
    "source_chunks", "inputs", "outputs", "preconditions", "constraints",
    "invalid_conditions", "dependencies", "blackbox_policy", "validation_warnings",
    "source_scope", "evidence_claims", "maturity_level", "maturity_source",
    "cautions", "connectors", "internal_flow", "duplicate_candidates",
    "thesis_context", "operation", "teaching_takeaway", "teaching_granularity",
    "prerequisite_concepts", "assumptions", "approximations", "linked_claim_ids",
    "linked_equation_ids", "linked_evidence_ids", "linked_derivation_ids",
    "agent_payload",
    # P2-2（learning_units_design.md §5.1）: 決定論 refinement が分割した子から
    # LLM 原案（親）をたどるための agent 側 ID。``parent_component_id``（UUID 列）は
    # v1 では常に NULL（原案は theory_components の行にしない）。
    "parent_agent_component_id",
)

#: 人間の確定列（一致時に触らない。§5.3）。
_COMPONENT_PRESERVED_COLUMNS = ("review_status", "status", "teacher_notes", "created_by")

#: 「人間が触った行」でのみ追加で保護する内容列（§5.3）。
_COMPONENT_PROTECTED_WHEN_TOUCHED = ("name", "summary", "maturity_source")

#: 列に昇格させた agent フィールド（``agent_payload`` からは除く。confidence 等の
#: 数値は列にせず payload の中にだけ残す — 原則4）。
_COMPONENT_PAYLOAD_EXCLUDED = frozenset({
    "component_id", "component_type", "label", "summary", "inputs", "outputs",
    "preconditions", "cautions", "dependencies", "internal_flow", "review_status",
    "maturity_source", "source_scope", "operation", "teaching_takeaway",
    "teaching_granularity", "prerequisite_concepts", "assumptions", "approximations",
    "linked_claim_ids", "linked_equation_ids", "linked_evidence_ids",
    "linked_derivation_ids",
})


def _component_human_touched(row: Any) -> bool:
    """live component 行を人間が触ったか（§5.3 の判定）。"""
    status = _text(row.get("status") if hasattr(row, "get") else "")
    review_status = _text(row.get("review_status") if hasattr(row, "get") else "")
    teacher_notes = _text(row.get("teacher_notes") if hasattr(row, "get") else "")
    maturity_source = _text(row.get("maturity_source") if hasattr(row, "get") else "")
    return bool(
        (status and status != "candidate")
        or (review_status and review_status != DEFAULT_REVIEW_STATUS)
        or teacher_notes
        or maturity_source == "teacher_reviewed"
    )


def _claim_block_index(claim_id_map: dict[str, str]) -> dict[str, list[str]]:
    """``agent claim ID -> [block_id]``（claim_id_map の逆引き）。

    ``claim_id_map`` は「突合キー → DB UUID」で、キーのうち ``"{block_id}:{span_id}"``
    形のものだけが block を含む（:func:`claim_span_key`）。同じ UUID を指す他の
    agent ID にその block を配ることで、component の stable_key 材料（出典 block 集合）を
    **推測なしに**引く。
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


def _component_parent_index(component_result: Any) -> dict[str, str]:
    """``子 agent component_id -> 親（LLM 原案）の agent component_id``（P2-2）。

    出所は ``ComponentAssemblyResult.refinement_report.split_actions``
    （``component_refiner.RefinementAction``: ``parent_component_id`` /
    ``parent_label`` / ``child_component_ids``）。子 ID が親 ID と同じ組
    （分割されなかった原案）は親子関係を作らない。``parent_label`` は
    ``learning_units(unit_kind='parent_component')`` が持つのでここでは使わない。
    """
    report = getattr(component_result, "refinement_report", None)
    if not isinstance(report, dict):
        return {}
    out: dict[str, str] = {}
    for action in report.get("split_actions") or []:
        if not isinstance(action, dict):
            continue
        parent_id = _text(action.get("parent_component_id"))
        if not parent_id:
            continue
        for child in action.get("child_component_ids") or []:
            child_id = _text(child)
            if child_id and child_id != parent_id:
                out.setdefault(child_id, parent_id)
    return out


def _component_block_ids(
    data: dict,
    claim_blocks: dict[str, list[str]],
    evidence_blocks: dict[str, str],
) -> list[str]:
    """component の出典 block 集合（linked claim / evidence 由来。§5.1）。"""
    blocks: set[str] = set()
    evidence_refs = data.get("evidence_refs") if isinstance(data.get("evidence_refs"), dict) else {}
    claim_ids = _id_list(
        list(data.get("linked_claim_ids") or []) + list(evidence_refs.get("claim_ids") or [])
    )
    for claim_id in claim_ids:
        blocks.update(claim_blocks.get(claim_id, ()))
    for evidence_id in _id_list(data.get("linked_evidence_ids")):
        block = evidence_blocks.get(evidence_id)
        if block:
            blocks.add(block)
    return sorted(blocks)


def persist_components(
    *,
    document_id: str,
    component_result,
    course_id: str | None = None,
    claim_id_map: dict[str, str] | None = None,
    evidence_registry: Any = None,
    run_id: str | None = None,
) -> dict[str, str]:
    """component を `theory_components` に **同期**する（KO3 / KO4 / P1-9）。

    claims と同じく DELETE を発行せず、``stable_key`` 一致で同じ UUID を保つ。人間が
    触った行（``status`` / ``review_status`` / ``teacher_notes`` / ``maturity_source``）
    では ``name`` / ``summary`` も上書きしない（§5.3）。

    ``theory_component_links`` だけは本 Phase の明示例外で、document 単位の
    DELETE → live component 間の再作成を維持する（派生構造で人間の書き込み経路が無い）。

    Args:
        evidence_registry: EvidenceRegistryResult（省略可）。stable_key の材料になる
            出典 block 集合の解決に使う。
        run_id: この保存を出した run（``produced_by_run_id`` / supersede の刻印）。

    Returns:
        agent component_id → DB UUID のマッピング（dependency 解決用）。
    """
    components = list(getattr(component_result, "components", []) or [])

    # deterministic_fallback component は通常成果物として永続化しない (#347)。
    # ExportValidationGate が persist 前にブロックするのが正規経路だが、
    # 旧 artifact からの resume 等でここまで届いた場合の最終ガード。
    # 全件 fallback の場合は hard fail にする: silently return すると
    # 同 document の live な theory_components（前回 run の通常成果物）が
    # 同期も supersede もされず「成功」扱いのまま downstream に見え続けるため、
    # 例外で run を failed にして再処理が必要なことを明示する。
    fallback_components = [
        c for c in components
        if str(getattr(c, "maturity_source", "") or "") == "deterministic_fallback"
    ]
    if fallback_components:
        fallback_reason = str(
            getattr(fallback_components[0], "fallback_reason", "") or "unknown"
        )
        components = [c for c in components if c not in fallback_components]
        if not components:
            raise DeterministicFallbackPersistError(
                f"persist_components blocked for document={document_id}: all "
                f"{len(fallback_components)} component(s) are deterministic-fallback "
                f"(fallback_reason={fallback_reason!r}); rerun component_assembly "
                "instead of persisting. Existing theory_components for this "
                "document are left untouched."
            )
        logger.warning(
            "persist_components: skipping %d deterministic-fallback component(s) "
            "for document=%s (fallback_reason=%r); they remain in the stage "
            "artifact only and are not persisted to theory_components",
            len(fallback_components),
            document_id,
            fallback_reason,
        )

    claim_id_map = claim_id_map or {}
    claim_blocks = _claim_block_index(claim_id_map)
    evidence_blocks = _evidence_block_index(evidence_registry)
    parent_index = _component_parent_index(component_result)

    items: list[dict] = []
    for comp in components:
        data = _plain(comp)
        if not isinstance(data, dict):
            continue
        agent_id = _text(data.get("component_id"))
        evidence_refs = dict(data.get("evidence_refs") or {})
        if isinstance(evidence_refs.get("claim_ids"), list):
            evidence_refs["claim_ids"] = _remap_string_list(evidence_refs["claim_ids"], claim_id_map)
        inputs = _remap_nested_claim_refs(data.get("inputs") or [], claim_id_map)
        outputs = _remap_nested_claim_refs(data.get("outputs") or [], claim_id_map)
        preconditions = _remap_nested_claim_refs(data.get("preconditions") or [], claim_id_map)
        cautions = _remap_nested_claim_refs(data.get("cautions") or [], claim_id_map)
        raw_component_type = _text(data.get("component_type"))
        # source_scope は agent 側（例: apparatus_components.py の
        # ComponentRecord.source_scope）をベースに document_id / legacy_ids を
        # 上書きマージする（figure_concept_linking_design.md F2）。
        # legacy_ids=[component_id] の既存セマンティクスは
        # _component_id_lookup_from_rows（context_lens.py）が依存するため不変。
        source_scope = dict(data.get("source_scope") or {})
        source_scope["document_id"] = document_id
        source_scope["legacy_ids"] = [agent_id]
        thesis_context = _component_thesis_context(comp)
        operation = _text(data.get("operation")) or _text(data.get("primary_operation"))
        block_ids = _component_block_ids(data, claim_blocks, evidence_blocks)
        agent_payload = {
            key: value for key, value in data.items()
            if key not in _COMPONENT_PAYLOAD_EXCLUDED
        }
        items.append({
            "agent_id": agent_id,
            "stable_key": ko_keys.component_stable_key(
                document_id, _text(data.get("label")), operation, block_ids
            ),
            "values": {
                "course_id": course_id,
                "name": _text(data.get("label")) or "Untitled",
                # migration 041 で CHECK に追加された装置系語彙のみ実値を保存し、
                # それ以外（assembly の自由語彙）は語彙表に載る値へ丸める
                # （実語彙は component_type_text が正）。
                "component_type": (
                    raw_component_type
                    if raw_component_type in ("apparatus", "instrument", "part")
                    else normalize_component_type(raw_component_type)
                ),
                "component_type_text": raw_component_type,
                "summary": str(data.get("summary") or ""),
                "status": "candidate",
                "source_chunks": list(evidence_refs.get("source_chunks") or []),
                "inputs": inputs,
                "outputs": outputs,
                "preconditions": preconditions,
                "constraints": [],
                "invalid_conditions": [],
                "dependencies": list(data.get("dependencies") or []),
                "blackbox_policy": {
                    "default_level": "summary",
                    "expand_if_unlearned": True,
                },
                "validation_warnings": [],
                "teacher_notes": "",
                "source_scope": source_scope,
                "evidence_claims": list(evidence_refs.get("claim_ids") or []),
                "maturity_level": "paper_claim",
                "maturity_source": _text(data.get("maturity_source")) or "llm_proposed",
                "review_status": _text(data.get("review_status")) or DEFAULT_REVIEW_STATUS,
                "cautions": cautions,
                "connectors": {},
                "internal_flow": list(data.get("internal_flow") or []),
                "duplicate_candidates": [],
                "thesis_context": thesis_context if thesis_context is not None else None,
                "operation": operation,
                "teaching_takeaway": str(data.get("teaching_takeaway") or ""),
                "teaching_granularity": dict(data.get("teaching_granularity") or {}),
                "prerequisite_concepts": list(data.get("prerequisite_concepts") or []),
                "assumptions": list(data.get("assumptions") or []),
                "approximations": list(data.get("approximations") or []),
                "linked_claim_ids": _id_list(data.get("linked_claim_ids")),
                "linked_equation_ids": _id_list(data.get("linked_equation_ids")),
                "linked_evidence_ids": _id_list(data.get("linked_evidence_ids")),
                "linked_derivation_ids": _id_list(data.get("linked_derivation_ids")),
                "agent_payload": agent_payload,
                # P2-2: 分割されていない component では NULL のまま（親は自分自身では
                # ないので、無い親を捏造しない）。
                "parent_agent_component_id": parent_index.get(agent_id) or None,
            },
        })

    final_keys = ko_keys.dedupe_stable_keys(
        items,
        key_of=lambda item: item["stable_key"],
        agent_id_of=lambda item: item["agent_id"],
    )
    for item in items:
        item["stable_key"] = final_keys.get(item["agent_id"], item["stable_key"])

    session = _pg_session()
    try:
        sync = sync_live_rows(
            session,
            table=TABLE_COMPONENTS,
            document_id=document_id,
            run_id=run_id,
            incoming=items,
            content_columns=_COMPONENT_CONTENT_COLUMNS,
            preserved_columns=_COMPONENT_PRESERVED_COLUMNS,
            agent_id_column="agent_component_id",
            human_touched=_component_human_touched,
            protected_when_touched=_COMPONENT_PROTECTED_WHEN_TOUCHED,
            touch_columns=("maturity_source",),
        )
        id_map = dict(sync.id_map)

        # links は派生構造（人間の書き込み経路が無い）ため、document 単位の
        # DELETE → live component 間の再作成を明示例外として維持する（§4.1）。
        session.execute(
            sa_text("DELETE FROM theory_component_links WHERE document_id = :doc_id"),
            {"doc_id": document_id},
        )
        for comp in components:
            src_db = id_map.get(_text(getattr(comp, "component_id", "")))
            if not src_db:
                continue
            for dep in getattr(comp, "dependencies", []) or []:
                if not isinstance(dep, dict):
                    continue
                refs = dep.get("component_refs") or []
                dep_type = dep.get("dependency_type") or "depends_on"
                link_type = (
                    "requires" if dep_type == "requires"
                    else "depends_on" if dep_type in ("depends_on", "qualifies", "refines", "supports")
                    else "depends_on"
                )
                for ref in refs:
                    dst_db = id_map.get(_text(ref))
                    if not dst_db or dst_db == src_db:
                        continue
                    session.execute(
                        sa_text(
                            """
                            INSERT INTO theory_component_links (
                                course_id, document_id,
                                source_component_id, target_component_id,
                                link_type, status, validation_result, produced_by_run_id
                            )
                            VALUES (
                                :course_id, :document_id,
                                CAST(:src AS uuid), CAST(:dst AS uuid),
                                :link_type, 'candidate', CAST(:validation AS jsonb),
                                CAST(:run_id AS uuid)
                            )
                            """
                        ),
                        {
                            "course_id": course_id,
                            "document_id": document_id,
                            "src": src_db,
                            "dst": dst_db,
                            "link_type": link_type,
                            "run_id": run_id,
                            "validation": _json_dumps({
                                "agent_dependency_type": dep_type,
                                "reason": dep.get("reason"),
                            }),
                        },
                    )

        remap_summary = _apply_remaps(
            session,
            document_id=document_id,
            run_id=run_id,
            kind="component",
            remaps=sync.remaps,
        )
        _record_knowledge_audit(
            session,
            document_id=document_id,
            run_id=run_id,
            stats={"component": sync.stats, "remap": remap_summary},
        )
        session.commit()
        logger.info(
            "Synced theory_components for document %s: %s (remap=%s)",
            document_id, sync.stats, remap_summary.get("recorded", 0),
        )
        return id_map
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# knowledge_equations / knowledge_evidence / knowledge_derivation_steps /
# knowledge_symbols（KO4: artifact にしか無い知識を残さない）
# ---------------------------------------------------------------------------


_EQUATION_CONTENT_COLUMNS = (
    "label", "latex", "plain_text", "raw_text", "block_id", "section_id", "page",
    "equation_type", "semantic_status", "content_hash", "defined_symbols",
    "used_symbols", "input_equation_ids", "output_equation_ids", "linked_claim_ids",
    "source_evidence_ids", "needs_math_review", "agent_payload",
)

_EVIDENCE_CONTENT_COLUMNS = (
    "block_id", "section_id", "page", "span_start", "span_end", "evidence_text",
    "evidence_role", "parent_evidence_id", "public_export_policy", "agent_payload",
)

_DERIVATION_CONTENT_COLUMNS = (
    "agent_derivation_id", "step_index", "operation", "operation_subtype", "chain_type",
    "input_equation_ids", "output_equation_ids", "input_claim_ids", "output_claim_ids",
    "required_claim_ids", "assumption_ids", "source_evidence_ids", "teaching_takeaway",
    "agent_payload",
)

_SYMBOL_CONTENT_COLUMNS = (
    "canonical_symbol", "notation_variants", "kind", "unit", "scope",
    "definition_status", "defining_equation_ids", "used_in_equation_ids",
    "source_evidence_ids", "definition_evidence_texts", "agent_payload",
)

#: 新 4 表の人間の確定列（§5.3）。
_KNOWLEDGE_PRESERVED_COLUMNS = ("review_status",)


def _equation_items(document_id: str, equations: Any, equation_keys: dict[str, str]) -> list[dict]:
    items: list[dict] = []
    for record in _equation_records(equations):
        fields = _equation_fields(record)
        agent_id = fields.get("equation_id") or ""
        if not agent_id:
            continue
        items.append({
            "agent_id": agent_id,
            "stable_key": equation_keys.get(agent_id, ""),
            "values": {
                "label": fields.get("label") or "",
                "latex": fields.get("latex") or "",
                "plain_text": fields.get("plain_text") or "",
                "raw_text": fields.get("raw_text") or "",
                "block_id": fields.get("block_id") or "",
                "section_id": fields.get("section_id") or "",
                "page": fields.get("page"),
                "equation_type": fields.get("equation_type") or "",
                "semantic_status": fields.get("semantic_status") or "",
                "content_hash": fields.get("content_hash") or "",
                "defined_symbols": fields.get("defined_symbols") or [],
                "used_symbols": fields.get("used_symbols") or [],
                "input_equation_ids": fields.get("input_equation_ids") or [],
                "output_equation_ids": fields.get("output_equation_ids") or [],
                "linked_claim_ids": fields.get("linked_claim_ids") or [],
                "source_evidence_ids": fields.get("source_evidence_ids") or [],
                "needs_math_review": bool(fields.get("needs_math_review")),
                "agent_payload": fields.get("payload") or {},
                "review_status": DEFAULT_REVIEW_STATUS,
            },
        })
    return items


def _evidence_items(document_id: str, evidence_registry: Any) -> list[dict]:
    items: list[dict] = []
    for record in (getattr(evidence_registry, "records", None) or []):
        data = _plain(record)
        if not isinstance(data, dict):
            continue
        agent_id = _text(data.get("evidence_id"))
        if not agent_id:
            continue
        source = data.get("source") or {}
        block_id = _text(source.get("block_id"))
        evidence_text = str(data.get("evidence_text") or "")
        items.append({
            "agent_id": agent_id,
            "stable_key": ko_keys.evidence_stable_key(document_id, block_id, evidence_text),
            "values": {
                "block_id": block_id,
                "section_id": _text(source.get("section_id")),
                "page": source.get("page"),
                "span_start": source.get("span_start") or 0,
                "span_end": source.get("span_end") or 0,
                "evidence_text": evidence_text,
                "evidence_role": _text(data.get("evidence_role")) or "source_quote",
                "parent_evidence_id": _text(data.get("parent_evidence_id")),
                "public_export_policy": _text(data.get("public_export_policy")) or "location_only",
                "agent_payload": data,
                "review_status": DEFAULT_REVIEW_STATUS,
            },
        })
    return items


def _derivation_items(
    document_id: str, derivations: Any, equation_keys: dict[str, str]
) -> list[dict]:
    items: list[dict] = []
    for chain in (getattr(derivations, "chains", None) or []):
        data = _plain(chain)
        if not isinstance(data, dict):
            continue
        derivation_id = _text(data.get("derivation_id"))
        chain_type = _text(data.get("chain_type")) or "equation_chain"
        steps = list(data.get("steps") or [])
        if not steps:
            # system-level derivation（steps を持たない操作）も 1 行として残す
            # （artifact にしか無い知識を作らない = KO4）。
            steps = [{
                "step_id": _text(data.get("system_id")) or derivation_id,
                "operation": _text(data.get("operation")) or _text(data.get("operation_family")),
                "operation_subtype": data.get("operation_subtype"),
                "input_equation_ids": data.get("input_equation_ids") or [],
                "output_equation_ids": data.get("output_equation_ids") or [],
                "input_claim_ids": data.get("input_claim_ids") or [],
                "output_claim_ids": data.get("output_claim_ids") or [],
                "assumption_ids": data.get("assumption_ids") or [],
                "source_evidence_ids": data.get("source_evidence_ids") or [],
            }]
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            agent_step_id = _text(step.get("step_id"))
            if not agent_step_id:
                continue
            operation = _text(step.get("operation"))
            input_keys = _resolved_equation_keys(step.get("input_equation_ids"), equation_keys)
            output_keys = _resolved_equation_keys(step.get("output_equation_ids"), equation_keys)
            items.append({
                "agent_id": agent_step_id,
                "stable_key": ko_keys.derivation_step_stable_key(
                    document_id, operation, input_keys, output_keys
                ),
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
                    "assumption_ids": _id_list(step.get("assumption_ids")),
                    "source_evidence_ids": _id_list(step.get("source_evidence_ids")),
                    "teaching_takeaway": str(data.get("teaching_takeaway") or ""),
                    "agent_payload": step,
                    "review_status": DEFAULT_REVIEW_STATUS,
                },
            })
    return items


def _symbol_items(
    document_id: str, symbol_registry: Any, equation_keys: dict[str, str]
) -> list[dict]:
    items: list[dict] = []
    for record in (getattr(symbol_registry, "records", None) or []):
        data = _plain(record)
        if not isinstance(data, dict):
            continue
        agent_id = _text(data.get("symbol_id"))
        if not agent_id:
            continue
        canonical = _text(data.get("canonical_symbol"))
        scope = _text(data.get("scope")) or "equation_local"
        defining_keys = _resolved_equation_keys(data.get("defining_equation_ids"), equation_keys)
        items.append({
            "agent_id": agent_id,
            "stable_key": ko_keys.symbol_stable_key(
                document_id, canonical, scope, defining_keys
            ),
            "values": {
                "canonical_symbol": canonical,
                "notation_variants": _id_list(data.get("notation_variants")),
                "kind": _text(data.get("kind")) or "unknown",
                # 単位は「書かれていない」を空文字で保つ（捏造しない・列は NOT NULL）。
                "unit": _text(data.get("unit")),
                "scope": scope,
                "definition_status": _text(data.get("definition_status")) or "unknown",
                "defining_equation_ids": _id_list(data.get("defining_equation_ids")),
                "used_in_equation_ids": _id_list(data.get("used_in_equation_ids")),
                "source_evidence_ids": _id_list(data.get("source_evidence_ids")),
                "definition_evidence_texts": list(data.get("definition_evidence_texts") or []),
                "agent_payload": data,
                "review_status": DEFAULT_REVIEW_STATUS,
            },
        })
    return items


def persist_knowledge_objects(
    *,
    document_id: str,
    run_id: str | None = None,
    equations: Any = None,
    evidence_registry: Any = None,
    derivations: Any = None,
    symbol_registry: Any = None,
) -> dict:
    """equation / evidence / derivation step / symbol を専用テーブルへ同期する（KO4）。

    claims / components と同じ規則（stable_key 一致 = 同 UUID 更新 / 不一致 =
    supersede 刻印 / DELETE なし）。式の stable_key を先に確定し、derivation step と
    symbol のキー材料に使う（解決できない agent ID はそのまま材料にする）。

    種別ごとに素材（``ctx.equations`` 等）が ``None`` なら **その種別だけ**スキップする
    （素材が無いことを「全件消えた」と解釈しない）。

    Returns:
        ``{種別: {"updated", "inserted", "superseded"}}`` + ``{"skipped": [...]}``。
    """
    equation_keys = _equation_stable_key_map(document_id, equations)
    plans: list[tuple[str, str, str, tuple[str, ...], list[dict]]] = []
    skipped: list[str] = []

    if equations is not None:
        plans.append((
            "equations", TABLE_EQUATIONS, "agent_equation_id",
            _EQUATION_CONTENT_COLUMNS, _equation_items(document_id, equations, equation_keys),
        ))
    else:
        skipped.append("equations")
    if evidence_registry is not None:
        plans.append((
            "evidence", TABLE_EVIDENCE, "agent_evidence_id",
            _EVIDENCE_CONTENT_COLUMNS, _evidence_items(document_id, evidence_registry),
        ))
    else:
        skipped.append("evidence")
    if derivations is not None:
        plans.append((
            "derivation_steps", TABLE_DERIVATION_STEPS, "agent_step_id",
            _DERIVATION_CONTENT_COLUMNS,
            _derivation_items(document_id, derivations, equation_keys),
        ))
    else:
        skipped.append("derivation_steps")
    if symbol_registry is not None:
        plans.append((
            "symbols", TABLE_SYMBOLS, "agent_symbol_id",
            _SYMBOL_CONTENT_COLUMNS,
            _symbol_items(document_id, symbol_registry, equation_keys),
        ))
    else:
        skipped.append("symbols")

    summary: dict[str, Any] = {"skipped": skipped}
    if not plans:
        return summary

    session = _pg_session()
    try:
        for kind, table, agent_id_column, content_columns, items in plans:
            final_keys = ko_keys.dedupe_stable_keys(
                items,
                key_of=lambda item: item["stable_key"],
                agent_id_of=lambda item: item["agent_id"],
            )
            for item in items:
                item["stable_key"] = final_keys.get(item["agent_id"], item["stable_key"])
            sync = sync_live_rows(
                session,
                table=table,
                document_id=document_id,
                run_id=run_id,
                incoming=items,
                content_columns=content_columns,
                preserved_columns=_KNOWLEDGE_PRESERVED_COLUMNS,
                agent_id_column=agent_id_column,
            )
            summary[kind] = dict(sync.stats)
            remap_kind = "derivation_step" if kind == "derivation_steps" else kind.rstrip("s")
            remap_summary = _apply_remaps(
                session,
                document_id=document_id,
                run_id=run_id,
                kind=remap_kind,
                remaps=sync.remaps,
            )
            summary[f"{kind}_remap"] = remap_summary.get("recorded", 0)
        _record_knowledge_audit(
            session, document_id=document_id, run_id=run_id, stats=summary,
        )
        session.commit()
        logger.info(
            "Synced knowledge objects for document %s: %s", document_id, summary
        )
        return summary
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# learning_units（学ぶ単位の一級化 Phase 2 / P2-1）
# ---------------------------------------------------------------------------


#: 再解析で上書きしてよい内容列（learning_units_design.md §4.1）。
_LEARNING_UNIT_CONTENT_COLUMNS = (
    "unit_kind", "label", "summary", "teaches", "order_index", "section_ids",
    "source_block_ids", "linked_claim_ids", "linked_equation_ids",
    "linked_component_ids", "linked_figure_ids", "agent_payload",
)

#: 人間の確定列（LU2。一致時は触らない・行削除もしない）。
_LEARNING_UNIT_PRESERVED_COLUMNS = ("review_status", "teacher_notes")


def persist_learning_units(
    *,
    document_id: str,
    run_id: str | None = None,
    skeleton: Any = None,
    thesis: Any = None,
    component_result: Any = None,
    dsl: Any = None,
    figures: Any = None,
    claim_id_map: dict[str, str] | None = None,
    component_id_map: dict[str, str] | None = None,
    evidence_registry: Any = None,
) -> dict:
    """学ぶ単位を ``learning_units`` へ**同期**する（LU3 / LU4・migration 081）。

    導出は :func:`core.knowledge_objects.learning_units.build_learning_unit_items`
    （決定論・非LLM・純関数）で、ここは書き込みだけを受け持つ。claims / components と
    同じ規則（``stable_key`` 一致 = 同 UUID 更新 / 不一致 = supersede 刻印 /
    **DELETE なし**）で、``review_status`` / ``teacher_notes`` は触らない。

    素材（``skeleton`` 等）が **5 種別とも ``None``** のときは SQL を一切発行しない
    （素材が無いことを「単位が全部消えた」と解釈しない）。一部だけ ``None`` のときは
    その種別を導出からスキップし、``skipped_kinds`` に正直に載せる。

    ``element_id_remap`` への再係留は行わない — unit の agent 側 ID を参照している
    テーブルが v1 には無く、``element_id_remap.object_kind`` の CHECK 語彙
    （知識オブジェクト 6 種）も増やさないため。

    Returns:
        ``{"updated", "inserted", "superseded", "skipped_kinds", "units"}``。
        素材ゼロのときは ``{"skipped_kinds": [...], "units": 0}`` のみ。
    """
    present_kinds, skipped_kinds = ko_units.available_kinds(
        skeleton=skeleton,
        thesis=thesis,
        component_result=component_result,
        dsl=dsl,
        figures=figures,
    )
    if not present_kinds:
        return {"skipped_kinds": skipped_kinds, "units": 0}

    items = ko_units.build_learning_unit_items(
        document_id,
        skeleton=skeleton,
        thesis=thesis,
        component_result=component_result,
        dsl=dsl,
        figures=figures,
        claim_id_map=claim_id_map,
        component_id_map=component_id_map,
        evidence_registry=evidence_registry,
    )

    session = _pg_session()
    try:
        sync = sync_live_rows(
            session,
            table=TABLE_LEARNING_UNITS,
            document_id=document_id,
            run_id=run_id,
            incoming=items,
            content_columns=_LEARNING_UNIT_CONTENT_COLUMNS,
            preserved_columns=_LEARNING_UNIT_PRESERVED_COLUMNS,
            agent_id_column="agent_unit_id",
        )
        summary = {
            **dict(sync.stats),
            "skipped_kinds": skipped_kinds,
            "units": len(items),
        }
        _record_knowledge_audit(
            session,
            document_id=document_id,
            run_id=run_id,
            stats={"learning_units": summary},
        )
        session.commit()
        logger.info(
            "Synced learning_units for document %s: %s", document_id, summary
        )
        return summary
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# theory_component_graphs
# ---------------------------------------------------------------------------


def _split_main_label(label: str) -> tuple[str, str]:
    """Lazy wrapper around the shared stage-label splitter (issue #319)."""
    try:
        from episteme_graph.agents.component_graph.schema import split_main_label
    except Exception:  # pragma: no cover - agents package optional in some contexts
        return str(label or "").strip(), ""
    return split_main_label(label)


def _narrative_payload(narrative_result, component_id_map: dict[str, str]) -> dict:
    """NarrativeAnnotator output keyed by stored DB component ids (issue #360)."""
    if narrative_result is None:
        return {}
    node_map: dict[str, dict] = {}
    for narrative in getattr(narrative_result, "node_narratives", []) or []:
        agent_id = str(getattr(narrative, "component_id", "") or "")
        if not agent_id:
            continue
        db_id = component_id_map.get(agent_id, agent_id)
        node_map[db_id] = {
            "narrative_role": str(getattr(narrative, "narrative_role", "") or ""),
            "reason": str(getattr(narrative, "reason", "") or ""),
            "confidence": float(getattr(narrative, "confidence", 0.0) or 0.0),
        }
    edge_map: dict[str, dict] = {}
    for narrative in getattr(narrative_result, "edge_narratives", []) or []:
        edge_id = str(getattr(narrative, "edge_id", "") or "")
        if not edge_id:
            continue
        edge_map[edge_id] = {
            "transition_text": str(getattr(narrative, "transition_text", "") or ""),
            "reason": str(getattr(narrative, "reason", "") or ""),
            "confidence": float(getattr(narrative, "confidence", 0.0) or 0.0),
        }
    graph_summary = str(getattr(narrative_result, "graph_summary", "") or "")
    if not graph_summary and not node_map and not edge_map:
        return {}
    return {
        "graph_summary": graph_summary,
        "maturity_source": str(
            getattr(narrative_result, "maturity_source", "") or "llm_proposed"
        ),
        "node_narratives": node_map,
        "edge_narratives": edge_map,
    }


def _normalize_graph_payload_for_persist(graph: dict) -> dict:
    """Guarantee the persisted graph satisfies the stored-graph invariants (issue #319).

    Before save we enforce, deterministically:
      * main ``TheoryOperationNode`` labels are short stage names only;
      * the long label remainder is preserved in ``description``;
      * every node / edge carries a ``source_backing_status`` key;
      * ``review_required`` / ``inferred`` nodes & edges have non-empty
        ``review_reasons``.
    """
    for node in graph.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        layer = str(node.get("graph_layer") or "main")
        ctype = str(node.get("component_type") or node.get("type") or "")
        if layer == "main" and ctype == "TheoryOperationNode":
            short_label, remainder = _split_main_label(str(node.get("label") or ""))
            if short_label:
                node["label"] = short_label
            if not str(node.get("description") or "") and remainder:
                node["description"] = remainder
        label = str(node.get("label") or "")
        theory_object = str(node.get("theory_object") or "")
        if not str(node.get("display_label") or ""):
            node["display_label"] = f"{label}: {theory_object}" if theory_object else label
        for key in (
            "linked_component_ids",
            "detail_node_ids",
            "supporting_derivation_ids",
        ):
            if not isinstance(node.get(key), list):
                node[key] = []
        node["representative_component_id"] = str(node.get("representative_component_id") or "")
        backing = str(node.get("source_backing_status") or "")
        node["source_backing_status"] = backing
        reasons = node.get("review_reasons") if isinstance(node.get("review_reasons"), list) else []
        review_status = str(node.get("review_status") or "")
        if not reasons and (backing in ("review_required", "inferred") or review_status == "review_required"):
            node["review_reasons"] = [
                "fallback_or_inferred_node" if backing == "inferred" else "missing_evidence_link"
            ]
        else:
            node["review_reasons"] = reasons
    for edge in graph.get("edges", []) or []:
        if not isinstance(edge, dict):
            continue
        backing = str(edge.get("source_backing_status") or "")
        edge["source_backing_status"] = backing
        reasons = edge.get("review_reasons") if isinstance(edge.get("review_reasons"), list) else []
        review_status = str(edge.get("review_status") or "")
        if not reasons and (backing in ("review_required", "inferred") or review_status == "review_required"):
            edge["review_reasons"] = ["edge_not_source_backed"]
        else:
            edge["review_reasons"] = reasons
    return graph


def persist_component_graph(
    *,
    document_id: str,
    component_id_map: dict[str, str],
    component_result,
    dsl_result,
    course_id: str | None = None,
    component_graph_result=None,
    claim_id_map: dict[str, str] | None = None,
    narrative_result=None,
    run_id: str | None = None,
) -> str | None:
    """document scope の component graph を `theory_component_graphs` に保存。

    component_graph_result が指定された場合（ComponentGraphAgent の出力）は
    そのエッジ情報を優先し、source_component_id/target_component_id/relation/
    edge_type/evidence スキーマで保存する。
    指定されない場合は component_result.components[].dependencies から
    フォールバックエッジを生成する。
    """
    claim_id_map = claim_id_map or {}
    if component_graph_result is not None:
        # ComponentGraphAgent の結果を使い、エージェントIDをDB IDに変換してエッジ化
        payload = component_graph_result.to_graph_payload()
        nodes = []
        seen_node_ids: set[str] = set()
        for node in payload.get("nodes", []):
            if not isinstance(node, dict):
                continue
            agent_id = str(node.get("component_id") or node.get("id") or "").strip()
            if not agent_id:
                continue
            db_id = component_id_map.get(agent_id, agent_id)
            if db_id in seen_node_ids:
                continue
            seen_node_ids.add(db_id)
            stored_node = dict(node)
            stored_node["id"] = db_id
            stored_node["component_id"] = db_id
            stored_node["agent_component_id"] = agent_id
            stored_node.setdefault("type", "component")
            nodes.append(stored_node)
        edges = []
        for e in payload.get("edges", []):
            src_agent_id = e.get("source_component_id", "")
            dst_agent_id = e.get("target_component_id", "")
            src_db = component_id_map.get(src_agent_id, src_agent_id)
            dst_db = component_id_map.get(dst_agent_id, dst_agent_id)
            evidence = _remap_nested_claim_refs(
                e.get("evidence", {"evidence_claims": [], "reason": ""}),
                claim_id_map,
            )
            edges.append({
                "source_component_id": src_db,
                "target_component_id": dst_db,
                "relation": e.get("relation", "RELATED_TO"),
                "edge_type": e.get("edge_type", e.get("relation", "RELATED_TO")),
                "support_status": e.get("support_status", "llm_inferred"),
                "confidence": e.get("confidence", 0.0),
                # node と同じ source-backing 語彙を edge にも保存する (issue #311/#319)。
                # 落とすと API/UI 側で source_backing_status を表示・検証できなくなる。
                "source_backing_status": e.get("source_backing_status", ""),
                "review_status": e.get("review_status", "teacher_review_required"),
                # review_required edge の理由 (issue #302/#304) を保存する。
                # 落とすと API/UI 側で review 理由を表示・検証できなくなる。
                "review_reasons": list(e.get("review_reasons") or []),
                "evidence": evidence,
                "edge_id": e.get("edge_id", ""),
                # Issue #451: relation polarity (+/-/""), carried to API/UI.
                "polarity": e.get("polarity", ""),
            })
        validation_issues = [
            {"rule_id": v.rule_id, "severity": v.severity, "message": v.message}
            for v in getattr(component_graph_result, "validation_issues", [])
        ]
    else:
        nodes = []
        for agent_id, db_id in component_id_map.items():
            nodes.append({
                "id": db_id,
                "agent_component_id": agent_id,
                "component_id": db_id,
                "type": "component",
            })
        # フォールバック: dependencies ベースの確定的エッジ生成
        edges = []
        for comp in getattr(component_result, "components", []) or []:
            src_db = component_id_map.get(getattr(comp, "component_id", ""))
            if not src_db:
                continue
            for dep in getattr(comp, "dependencies", []) or []:
                if not isinstance(dep, dict):
                    continue
                dep_type = dep.get("dependency_type") or "depends_on"
                relation = (
                    "REQUIRES" if dep_type in ("requires", "depends_on")
                    else "TRANSFORMS" if dep_type == "transforms"
                    else "ENABLES" if dep_type in ("enables", "supports")
                    else "RELATED_TO"
                )
                for ref in dep.get("component_refs") or []:
                    dst_db = component_id_map.get(ref)
                    if not dst_db:
                        continue
                    edges.append({
                        "source_component_id": src_db,
                        "target_component_id": dst_db,
                        "relation": relation,
                        "edge_type": relation,
                        "support_status": "dependency_declared",
                        "confidence": 1.0,
                        "review_status": "teacher_review_required",
                        "evidence": {
                            "evidence_claims": [],
                            "reason": dep.get("reason") or "",
                        },
                    })
        validation_issues = []

    dsl_nodes = [
        {
            "id": getattr(n, "node_id", ""),
            "node_type": getattr(n, "node_type", ""),
            "value": getattr(n, "node_value", ""),
            # Issue #442: preserve the thesis traversal-anchor flag.
            "is_thesis_anchor": bool(getattr(n, "is_thesis_anchor", False)),
        }
        for n in (getattr(dsl_result, "nodes", []) or [])
    ]
    dsl_edges = [
        {
            "from": getattr(e, "from_node_id", ""),
            "to": getattr(e, "to_node_id", ""),
            "predicate": getattr(e, "core_predicate", ""),
            # Issue #441: persist the controlled edge_type (mirrors core_predicate)
            # and the relation's evidence_refs; keep the raw verb as the subtype.
            "edge_type": getattr(e, "edge_type", "") or getattr(e, "core_predicate", ""),
            "verb": getattr(e, "domain_verb", ""),
            "domain_verb": getattr(e, "domain_verb", ""),
            "polarity": getattr(e, "polarity", ""),
            "evidence_refs": getattr(e, "evidence_refs", {}) or {},
        }
        for e in (getattr(dsl_result, "edges", []) or [])
    ]

    graph = _normalize_graph_payload_for_persist({
        "graph_id": f"graph_{document_id}",
        "document_id": document_id,
        "graph_schema_version": "0.1.0",
        "scope": {"level": "paper"},
        "nodes": nodes,
        "edges": edges,
        "dsl": {"nodes": dsl_nodes, "edges": dsl_edges},
        "validation_results": validation_issues,
    })
    # Reading layer from NarrativeAnnotator (issue #360): stored as a sibling
    # block, never merged into nodes/edges, always provisional.
    narrative_payload = _narrative_payload(narrative_result, component_id_map)
    if narrative_payload:
        graph["narrative"] = narrative_payload

    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                INSERT INTO theory_component_graphs (
                    course_id, document_id, scope, graph_json, validation_results,
                    produced_by_run_id
                )
                VALUES (
                    :course_id, :document_id, CAST(:scope AS jsonb),
                    CAST(:graph_json AS jsonb), CAST(:validation AS jsonb),
                    CAST(:run_id AS uuid)
                )
                ON CONFLICT (document_id) DO UPDATE SET
                    course_id = EXCLUDED.course_id,
                    scope = EXCLUDED.scope,
                    graph_json = EXCLUDED.graph_json,
                    validation_results = EXCLUDED.validation_results,
                    produced_by_run_id = EXCLUDED.produced_by_run_id,
                    updated_at = now()
                RETURNING id
                """
            ),
            {
                "course_id": course_id,
                "document_id": document_id,
                "run_id": run_id,
                "scope": _json_dumps({"level": "paper"}),
                "graph_json": _json_dumps(graph),
                "validation": _json_dumps([]),
            },
        ).fetchone()
        session.commit()
        return str(row[0]) if row else None
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def delete_component_graph(document_id: str) -> None:
    """当該 document の component graph 行（theory_component_graphs）を削除する。

    コンポーネントを DELETE→新UUIDで再INSERT したのにグラフを再保存しない経路
    （検証エラーで graph persist をスキップした場合など）では、
    theory_component_graphs に旧・削除済みUUIDを指す古いノード/エッジが残る。
    この stale グラフは context_lens._build_component の
    ``nodes_by_id.get(ref.element_id)`` を全ミスさせ、component の上位/下位を
    一切引けなくする（しかも古いグラフにノードが在るため「グラフ未保存」注記も
    出ない）。components を作り直したのにグラフを作り直さないケースで本関数を呼び、
    古い行を明示削除して不整合を断つ（削除後は _build_component が正直に
    「component_graph が保存されていないため…」の注記を出す）。
    """
    session = _pg_session()
    try:
        session.execute(
            sa_text("DELETE FROM theory_component_graphs WHERE document_id = :doc"),
            {"doc": document_id},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# document_embeddings
# ---------------------------------------------------------------------------


def persist_document_embedding(
    *,
    document_id: str,
    material_id: str | None,
    embedding_type: str,
    text: str,
    metadata: dict | None = None,
    source_version: str = "v1",
) -> str:
    """document-level の derived embedding を保存する（DSL graph など）。"""
    if not text or not text.strip():
        text = " "
    [embedding] = generate_embeddings([text])
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                INSERT INTO document_embeddings (
                    document_id, material_id, embedding_type, source_version,
                    text, embedding, metadata
                )
                VALUES (
                    :document_id, :material_id, :embedding_type, :source_version,
                    :text, :embedding, CAST(:metadata AS jsonb)
                )
                ON CONFLICT (document_id, embedding_type, source_version) DO UPDATE SET
                    material_id = EXCLUDED.material_id,
                    text = EXCLUDED.text,
                    embedding = EXCLUDED.embedding,
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                RETURNING id
                """
            ),
            {
                "document_id": document_id,
                "material_id": material_id,
                "embedding_type": embedding_type,
                "source_version": source_version,
                "text": _strip_nuls(text),
                "embedding": str(list(embedding)),
                "metadata": _json_dumps(metadata or {}),
            },
        ).fetchone()
        session.commit()
        return str(row[0]) if row else ""
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# document_analysis_runs
# ---------------------------------------------------------------------------


def upsert_analysis_run(
    *,
    document_id: str,
    material_id: str | None,
    cartridge_id: str | None,
    status: str,
    current_stage: str | None = None,
    error_message: str = "",
    stage_outputs: dict | None = None,
    run_id: str | None = None,
    options: dict | None = None,
) -> str:
    """`document_analysis_runs` に upsert する。

    run_id を渡すとそのレコードを更新、None なら新規作成。

    ``options``（migration 041 §3-2）はアップロード時オプション（例:
    ``analyze_images``）のスナップショット。新規作成時は ``options or {}`` を
    保存する。更新時は ``options`` が明示的に渡されたときのみ上書きし、
    ``None`` のときは既存値を保持する（呼び出し側が毎回 options を意識せず
    呼べるようにするため）。

    ``stage_outputs`` の ``_artifacts`` は **stage_outputs に書かず**、生成ログ表
    ``document_analysis_artifacts`` へ stage ごと upsert する（§6 / KO6）。読み手の
    契約（``document_run_artifacts()``）は getter 側の hydrate で不変に保つ。
    """
    payload = dict(stage_outputs or {})
    artifacts = payload.pop(ARTIFACTS_KEY, None)
    stage_outputs = payload
    session = _pg_session()
    try:
        if run_id is None:
            row = session.execute(
                sa_text(
                    """
                    INSERT INTO document_analysis_runs (
                        document_id, material_id, cartridge_id, status,
                        current_stage, error_message, stage_outputs, options, started_at
                    )
                    VALUES (
                        :document_id, :material_id, :cartridge_id, :status,
                        :current_stage, :error_message, CAST(:stage_outputs AS jsonb),
                        CAST(:options AS jsonb),
                        CASE WHEN :status = 'running' THEN now() ELSE NULL END
                    )
                    RETURNING id
                    """
                ),
                {
                    "document_id": document_id,
                    "material_id": material_id,
                    "cartridge_id": cartridge_id,
                    "status": status,
                    "current_stage": current_stage,
                    "error_message": error_message or "",
                    "stage_outputs": _json_dumps(stage_outputs or {}),
                    "options": _json_dumps(options or {}),
                },
            ).fetchone()
            new_run_id = str(row[0])
            _upsert_run_artifacts(session, new_run_id, artifacts)
            session.commit()
            return new_run_id
        else:
            session.execute(
                sa_text(
                    """
                    UPDATE document_analysis_runs SET
                        status = :status,
                        current_stage = :current_stage,
                        error_message = :error_message,
                        stage_outputs = stage_outputs || CAST(:stage_outputs AS jsonb),
                        options = CASE WHEN :options_provided THEN CAST(:options AS jsonb) ELSE options END,
                        completed_at = CASE WHEN :status IN ('completed', 'failed')
                                            THEN now() ELSE completed_at END,
                        updated_at = now()
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {
                    "id": run_id,
                    "status": status,
                    "current_stage": current_stage,
                    "error_message": error_message or "",
                    "stage_outputs": _json_dumps(stage_outputs or {}),
                    "options_provided": options is not None,
                    "options": _json_dumps(options or {}),
                },
            )
            _upsert_run_artifacts(session, run_id, artifacts)
            session.commit()
            return run_id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_latest_analysis_run(
    *,
    document_id: str,
    material_id: str | None = None,
) -> dict | None:
    """Return the latest analysis run for resume/status inspection."""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT id::text, document_id::text, material_id, cartridge_id, status,
                       current_stage, error_message, stage_outputs, started_at,
                       completed_at, created_at, updated_at, options
                FROM document_analysis_runs
                WHERE document_id = CAST(NULLIF(:document_id, '') AS uuid)
                  AND (:material_id IS NULL OR material_id = :material_id)
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"document_id": document_id, "material_id": material_id},
        ).mappings().fetchone()
        return _hydrate_run_artifacts(session, dict(row) if row else None)
    finally:
        session.close()


# ----------------------------------------------------------------------------
# Run version management (#402)
#
# 「最新Run」(get_latest_analysis_run) と「採用Run」(get_active_analysis_run)
# を明確に区別する。export/API は成果物参照には active run を、処理状況確認には
# latest run を使う。revision run は必ず base_run_id を持つ。
# ----------------------------------------------------------------------------

# Column list for analysis-run SELECTs, qualified with the ``r`` table alias so
# it is unambiguous when the run table is joined with ``documents`` (which shares
# column names like ``id`` / ``document_id``) — see get_active_analysis_run.
_RUN_COLUMNS = (
    "r.id::text, r.document_id::text, r.material_id, r.cartridge_id, r.status, "
    "r.current_stage, r.error_message, r.stage_outputs, r.started_at, r.completed_at, "
    "r.created_at, r.updated_at, r.run_type, r.base_run_id::text, "
    "r.parent_revision_id::text, r.revision_status, r.created_by::text"
)


def get_analysis_run(*, run_id: str) -> dict | None:
    """Fetch a single analysis run by id (latest *or* candidate)."""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                SELECT {_RUN_COLUMNS}
                FROM document_analysis_runs r
                WHERE r.id = CAST(:run_id AS uuid)
                """
            ),
            {"run_id": run_id},
        ).mappings().fetchone()
        return _hydrate_run_artifacts(session, dict(row) if row else None)
    finally:
        session.close()


def get_active_analysis_run(*, document_id: str) -> dict | None:
    """Return the *adopted* (active) analysis run for a document.

    成果物参照に使う採用Run。latest run とは別物であり、混同しないこと。
    active run が未設定の document（旧データ）では None を返す。呼び出し側は
    必要なら get_latest_analysis_run() への後方互換 fallback を判断する。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                SELECT {_RUN_COLUMNS}
                FROM document_analysis_runs r
                JOIN documents d ON d.active_analysis_run_id = r.id
                WHERE d.id = CAST(:document_id AS uuid)
                """
            ),
            {"document_id": document_id},
        ).mappings().fetchone()
        return _hydrate_run_artifacts(session, dict(row) if row else None)
    finally:
        session.close()


def get_active_analysis_run_id(*, document_id: str) -> str | None:
    """Return only documents.active_analysis_run_id (no run join)."""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT active_analysis_run_id::text
                FROM documents
                WHERE id = CAST(:document_id AS uuid)
                """
            ),
            {"document_id": document_id},
        ).fetchone()
        return str(row[0]) if row and row[0] else None
    finally:
        session.close()


def resolve_artifact_run(*, document_id: str, material_id: str | None = None) -> dict | None:
    """Return the run whose artifacts should be exported/used.

    採用Run優先。active が無い旧 document は最新 completed run へ後方互換 fallback。
    """
    active = get_active_analysis_run(document_id=document_id)
    if active is not None:
        return active
    latest = get_latest_analysis_run(document_id=document_id, material_id=material_id)
    if latest is not None and latest.get("status") == "completed":
        return latest
    return None


# 成果物 run の選び方（C-8 の一本化）。
#   adopted … documents.active_analysis_run_id → 無ければ最新 completed run。
#             **成果物（artifact）を読むときは常にこれ**。
#   latest  … status を問わない最新 run。resume / 進捗表示 / 前回 run の options・
#             cartridge 継承のように「いま走っている run を見たい」用途専用。
ARTIFACT_RUN_POLICIES = ("adopted", "latest")

# policy → targets CTE の run_id 選択式（SQL はこの1箇所にしか書かない）。
_ARTIFACT_RUN_SELECT_SQL = {
    "adopted": """
                       COALESCE(
                           d.active_analysis_run_id,
                           (SELECT r2.id FROM document_analysis_runs r2
                            WHERE r2.document_id = d.id AND r2.status = 'completed'
                            ORDER BY r2.completed_at DESC NULLS LAST,
                                     r2.created_at DESC, r2.id DESC
                            LIMIT 1)
                       )""",
    "latest": """
                       (SELECT r2.id FROM document_analysis_runs r2
                        WHERE r2.document_id = d.id
                        ORDER BY r2.created_at DESC, r2.id DESC
                        LIMIT 1)""",
}


def _check_artifact_run_policy(policy: str) -> str:
    if policy not in ARTIFACT_RUN_POLICIES:
        raise ValueError(
            f"unknown artifact run policy: {policy!r} "
            f"(expected one of {ARTIFACT_RUN_POLICIES})"
        )
    return policy


def resolve_artifact_runs(
    session, document_ids: list[str], *, policy: str = "adopted"
) -> dict[str, dict]:
    """Resolve the *artifact* run per document, using a caller session.

    ``policy="adopted"``（既定）は ``documents.active_analysis_run_id`` を優先し、
    無ければ最新 **completed** run へ後方互換 fallback する（走行中・失敗中の
    latest run が採用成果物を上書きしない）。``policy="latest"`` は status を問わない
    最新 run で、resume / 進捗表示 / 前回 run の options 継承の専用経路
    （成果物の参照には使わない。知識構造の見直し 2026-09-12 C-8）。

    Returns:
        ``{document_id: {"run_id", "stage_outputs", "status", "cartridge_id"}}``（#408）。
        run を解決できない document はキーごと含まれない。
    """
    _check_artifact_run_policy(policy)
    if not document_ids:
        return {}
    placeholders = ", ".join(f":doc_{i}" for i in range(len(document_ids)))
    params = {f"doc_{i}": did for i, did in enumerate(document_ids)}
    rows = session.execute(
        sa_text(
            f"""
            WITH targets AS (
                SELECT d.id::text AS document_id,
                       {_ARTIFACT_RUN_SELECT_SQL[policy]} AS run_id
                FROM documents d
                WHERE d.id::text IN ({placeholders})
            )
            SELECT t.document_id, r.id::text, r.stage_outputs, r.status, r.cartridge_id
            FROM targets t
            JOIN document_analysis_runs r ON r.id = t.run_id
            """
        ),
        params,
    ).fetchall()
    out: dict[str, dict] = {}
    for row in rows:
        doc_id = str(row[0]) if row[0] else ""
        if not doc_id:
            continue
        stage_outputs = row[2]
        if isinstance(stage_outputs, str):
            try:
                stage_outputs = json.loads(stage_outputs)
            except (ValueError, TypeError):
                stage_outputs = {}
        out[doc_id] = {
            "run_id": str(row[1]) if row[1] else None,
            "stage_outputs": stage_outputs if isinstance(stage_outputs, dict) else {},
            "status": row[3],
            # 短い行（既存テストの fake session 等）でも壊れないように防御的に読む。
            "cartridge_id": str(row[4] or "") if len(row) > 4 else "",
        }
    # artifact は生成ログ表が正本（§6 / KO6）。旧 blob が残る run では blob を
    # 下敷きにして表の値が勝つ（読み手の契約は不変）。
    stored = load_run_artifacts(
        session, [entry["run_id"] for entry in out.values() if entry.get("run_id")]
    )
    for entry in out.values():
        artifacts = stored.get(str(entry.get("run_id") or ""))
        if not artifacts:
            continue
        stage_outputs = dict(entry.get("stage_outputs") or {})
        blob = stage_outputs.get(ARTIFACTS_KEY)
        stage_outputs[ARTIFACTS_KEY] = {
            **(blob if isinstance(blob, dict) else {}), **artifacts,
        }
        entry["stage_outputs"] = stage_outputs
    return out


def document_run_artifacts(
    document_id: str, *, policy: str = "adopted", session: Any = None
) -> dict:
    """1 document の ``stage_outputs._artifacts`` を返す（成果物参照の正本・#408 / C-8）。

    run 選択は :data:`ARTIFACT_RUN_POLICIES` の1語彙で宣言する（既定 ``adopted``）。
    成果物を読む経路はすべてこれを通し、``get_latest_analysis_run`` を直接読まない
    （知識構造の見直し 2026-09-12 C-8: run 選択ポリシが4種に分裂していた是正）。

    Args:
        session: 呼び出し側のセッション（省略時は本関数が開閉する）。
    Returns:
        artifact の dict。run が無い・artifacts が無い場合は ``{}``。
    """
    _check_artifact_run_policy(policy)
    doc_id = str(document_id or "").strip()
    if not doc_id:
        return {}
    if session is not None:
        resolved = resolve_artifact_runs(session, [doc_id], policy=policy)
    else:
        own = _pg_session()
        try:
            resolved = resolve_artifact_runs(own, [doc_id], policy=policy)
        finally:
            own.close()
    stage_outputs = (resolved.get(doc_id) or {}).get("stage_outputs") or {}
    artifacts = stage_outputs.get(ARTIFACTS_KEY) if isinstance(stage_outputs, dict) else None
    return artifacts if isinstance(artifacts, dict) else {}


def document_run_cartridge_id(
    document_id: str, *, policy: str = "adopted", session: Any = None
) -> str:
    """1 document の分野（成果物 run の ``cartridge_id``）。未解析・分野中立は ``""``。

    成果物と同じ run から引く（:func:`document_run_artifacts` と同一ポリシ）ので、
    「成果物は採用 run・分野は最新 run」のような食い違いが起きない。
    """
    _check_artifact_run_policy(policy)
    doc_id = str(document_id or "").strip()
    if not doc_id:
        return ""
    if session is not None:
        resolved = resolve_artifact_runs(session, [doc_id], policy=policy)
    else:
        own = _pg_session()
        try:
            resolved = resolve_artifact_runs(own, [doc_id], policy=policy)
        finally:
            own.close()
    return str((resolved.get(doc_id) or {}).get("cartridge_id") or "").strip()


def create_revision_run(
    *,
    document_id: str,
    base_run_id: str,
    material_id: str | None = None,
    cartridge_id: str | None = None,
    parent_revision_id: str | None = None,
    created_by: str | None = None,
    revision_status: str = "preparing",
    stage_outputs: dict | None = None,
) -> str:
    """Create a revision (candidate) run.

    revision run は必ず比較元の base_run_id を持つ。base_run_id を省略すると
    ValueError を送出する（DB CHECK 制約と二重防御）。
    candidate run は projection table を更新せず、artifact のみを保持する。
    """
    if not base_run_id:
        raise ValueError("create_revision_run requires a non-empty base_run_id")
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                INSERT INTO document_analysis_runs (
                    document_id, material_id, cartridge_id, status,
                    stage_outputs, run_type, base_run_id, parent_revision_id,
                    revision_status, created_by
                )
                VALUES (
                    :document_id, :material_id, :cartridge_id, 'pending',
                    CAST(:stage_outputs AS jsonb), 'revision',
                    CAST(:base_run_id AS uuid),
                    CAST(:parent_revision_id AS uuid),
                    :revision_status,
                    CAST(:created_by AS uuid)
                )
                RETURNING id::text
                """
            ),
            {
                "document_id": document_id,
                "material_id": material_id,
                "cartridge_id": cartridge_id,
                "stage_outputs": _json_dumps(stage_outputs or {}),
                "base_run_id": base_run_id,
                "parent_revision_id": parent_revision_id,
                "revision_status": revision_status,
                "created_by": created_by,
            },
        ).fetchone()
        session.commit()
        return str(row[0])
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def update_revision_status(
    *,
    run_id: str,
    revision_status: str,
    status: str | None = None,
    current_stage: str | None = None,
    error_message: str | None = None,
    stage_outputs: dict | None = None,
) -> None:
    """Update a revision run's revision_status (and optionally run status / artifacts).

    artifact は stage_outputs の JSONB blob ではなく生成ログ表
    ``document_analysis_artifacts`` に stage ごと upsert する（§6 / KO6）。1 run ×
    1 ステージ = 1 行なので、かつての ``jsonb_set`` による deep merge
    （#410 P0: 浅マージだと兄弟 artifact が消える問題への対処）は不要になった。
    stage_outputs 側は従来どおり top-level の浅マージのみ。
    """
    payload = dict(stage_outputs or {})
    artifacts_delta = payload.pop(ARTIFACTS_KEY, None)
    session = _pg_session()
    try:
        session.execute(
            sa_text(
                """
                UPDATE document_analysis_runs SET
                    revision_status = :revision_status,
                    status = COALESCE(:status, status),
                    current_stage = COALESCE(:current_stage, current_stage),
                    error_message = COALESCE(:error_message, error_message),
                    stage_outputs = COALESCE(stage_outputs, '{}'::jsonb)
                        || CAST(:other AS jsonb),
                    started_at = CASE WHEN :status = 'running' AND started_at IS NULL
                                      THEN now() ELSE started_at END,
                    completed_at = CASE WHEN :status IN ('completed', 'failed')
                                        THEN now() ELSE completed_at END,
                    updated_at = now()
                WHERE id = CAST(:run_id AS uuid)
                  AND run_type = 'revision'
                """
            ),
            {
                "run_id": run_id,
                "revision_status": revision_status,
                "status": status,
                "current_stage": current_stage,
                "error_message": error_message,
                "other": _json_dumps(payload),
            },
        )
        _upsert_run_artifacts(session, run_id, artifacts_delta)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def claim_revision_run(*, run_id: str, stage: str = "audit") -> bool:
    """Atomically acquire the right to run a revision (#414-1).

    Flips the run to ``status='running'`` only when it is not already running, in
    a single ``UPDATE ... WHERE`` so two concurrent ``/run`` requests cannot both
    win: under READ COMMITTED the second statement re-evaluates the predicate on
    the row the first committed (now ``running``) and matches zero rows. Returns
    True for the single winner, False for everyone else (→ 409). Works across
    multiple API processes — it relies on the DB row, not an in-process lock.
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                UPDATE document_analysis_runs
                SET status = 'running',
                    revision_status = 'auditing',
                    current_stage = :stage,
                    error_message = '',
                    started_at = COALESCE(started_at, now()),
                    completed_at = NULL,
                    updated_at = now()
                WHERE id = CAST(:run_id AS uuid)
                  AND run_type = 'revision'
                  AND status IS DISTINCT FROM 'running'
                RETURNING id::text
                """
            ),
            {"run_id": run_id, "stage": stage},
        ).fetchone()
        session.commit()
        return row is not None
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def release_revision_run(*, run_id: str, error_message: str = "") -> None:
    """Release a claimed-but-not-started revision back to a re-runnable state (#414-1).

    Called when task creation / worker launch fails after ``claim_revision_run``
    succeeded, so the revision does not stay stuck in ``running``. ``failed`` is
    re-claimable by a subsequent ``/run``.
    """
    session = _pg_session()
    try:
        session.execute(
            sa_text(
                """
                UPDATE document_analysis_runs
                SET status = 'failed',
                    error_message = :err,
                    completed_at = now(),
                    updated_at = now()
                WHERE id = CAST(:run_id AS uuid)
                  AND run_type = 'revision'
                  AND status = 'running'
                """
            ),
            {"run_id": run_id, "err": error_message or "failed to start revision run"},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def set_active_analysis_run(
    *,
    document_id: str,
    run_id: str,
    expected_run_id: str | None,
) -> bool:
    """Switch documents.active_analysis_run_id with optimistic concurrency.

    accept 時に base_run_id を expected として渡す。0件更新（=採用Runが既に
    別Runへ進んでいる）の場合は競合として False を返す。`IS NOT DISTINCT FROM`
    で NULL（active 未設定）も扱える。
    """
    session = _pg_session()
    try:
        result = session.execute(
            sa_text(
                """
                UPDATE documents
                SET active_analysis_run_id = CAST(:run_id AS uuid),
                    updated_at = now()
                WHERE id = CAST(:document_id AS uuid)
                  AND active_analysis_run_id IS NOT DISTINCT FROM CAST(:expected AS uuid)
                """
            ),
            {
                "run_id": run_id,
                "document_id": document_id,
                "expected": expected_run_id,
            },
        )
        session.commit()
        return result.rowcount == 1
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_run_lineage(*, document_id: str) -> dict:
    """Return run lineage for a document: every run + the current active run id.

    Output keeps latest/active distinct so callers never conflate them.
    """
    session = _pg_session()
    try:
        active_row = session.execute(
            sa_text(
                """
                SELECT active_analysis_run_id::text
                FROM documents
                WHERE id = CAST(:document_id AS uuid)
                """
            ),
            {"document_id": document_id},
        ).fetchone()
        active_run_id = str(active_row[0]) if active_row and active_row[0] else None

        rows = session.execute(
            sa_text(
                """
                SELECT id::text, status, run_type, base_run_id::text,
                       parent_revision_id::text, revision_status,
                       current_stage, created_by::text, created_at, completed_at
                FROM document_analysis_runs
                WHERE document_id = CAST(NULLIF(:document_id, '') AS uuid)
                ORDER BY created_at ASC, id ASC
                """
            ),
            {"document_id": document_id},
        ).mappings().all()
        runs = [dict(r) for r in rows]
        latest_run_id = runs[-1]["id"] if runs else None
        for run in runs:
            run["is_active"] = run["id"] == active_run_id
            run["is_latest"] = run["id"] == latest_run_id
        return {
            "document_id": document_id,
            "active_run_id": active_run_id,
            "latest_run_id": latest_run_id,
            "runs": runs,
        }
    finally:
        session.close()


class RevisionConflictError(RuntimeError):
    """Raised when a revision accept loses the optimistic concurrency race."""


def _insert_revision_decision(
    session,
    *,
    run_id: str,
    old_status: str,
    new_status: str,
    changed_by: str | None,
    metadata: dict,
) -> None:
    session.execute(
        sa_text(
            """
            INSERT INTO theory_review_events (
                entity_type, entity_id, old_status, new_status, changed_by, metadata
            )
            VALUES (
                :entity_type, :entity_id, :old_status, :new_status,
                CAST(:changed_by AS uuid), CAST(:metadata AS jsonb)
            )
            """
        ),
        {
            "entity_type": AUDIT_ENTITY_REVISION_RUN,
            "entity_id": run_id,
            "old_status": old_status or "",
            "new_status": new_status or "",
            "changed_by": changed_by,
            "metadata": _json_dumps(metadata or {}),
        },
    )


def get_revision_decisions(*, run_id: str) -> list[dict]:
    """Return the decision audit history for a revision run."""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT old_status, new_status, changed_by::text, metadata, created_at
                FROM theory_review_events
                WHERE entity_type = 'revision_run' AND entity_id = :run_id
                ORDER BY created_at ASC
                """
            ),
            {"run_id": run_id},
        ).mappings().all()
        return [dict(r) for r in rows]
    finally:
        session.close()


def load_revision_projection_overlay(*, document_id: str) -> dict:
    """Load the current editable projections and their review history.

    Revision inventory starts from immutable run artifacts, then overlays these
    rows so post-pipeline manual edits and teacher decisions are not lost.
    """
    session = _pg_session()
    try:
        claims = session.execute(
            sa_text(
                """
                SELECT id::text, source_scope, claim_type, text, normalized_text,
                       concepts, equation, support_status, evidence_text,
                       review_status, updated_at
                FROM theory_claims
                WHERE document_id = :doc
                """
            ),
            {"doc": document_id},
        ).mappings().all()
        components = session.execute(
            sa_text(
                """
                SELECT id::text, name, component_type_text, summary, status,
                       source_scope, evidence_claims, review_status, inputs,
                       outputs, preconditions, constraints, invalid_conditions,
                       dependencies, updated_at
                FROM theory_components
                WHERE document_id = :doc
                """
            ),
            {"doc": document_id},
        ).mappings().all()
        entity_ids = [
            str(row["id"]) for row in [*claims, *components] if row.get("id")
        ]
        events: list[dict] = []
        if entity_ids:
            events = [
                dict(row)
                for row in session.execute(
                    sa_text(
                        """
                        SELECT entity_type, entity_id, old_status, new_status,
                               metadata, created_at
                        FROM theory_review_events
                        WHERE entity_id = ANY(CAST(:entity_ids AS text[]))
                          AND entity_type IN ('claim', 'component')
                        ORDER BY created_at ASC, id ASC
                        """
                    ),
                    {"entity_ids": entity_ids},
                ).mappings().all()
            ]
        return {
            "claims": [dict(row) for row in claims],
            "components": [dict(row) for row in components],
            "review_events": events,
        }
    finally:
        session.close()


# Allowed theory_claims.claim_type values（旧 CHECK。現在の語彙の正本は
# ``core/schema.py::CLAIM_TYPES`` で、丸め先は :func:`normalize_claim_type`）。
_THEORY_CLAIM_TYPES = {
    "definition", "assumption", "approximation", "equation", "relation",
    "derivation_step", "observable_definition", "correction", "uncertainty",
    "limitation", "result", "diagnostic_claim", "equation_definition",
    "equation_relation", "equation_transformation", "equation_approximation",
    "equation_constraint",
}


def _rebuild_theory_claims_in_session(
    session, document_id: str, claims: list, run_id: str | None = None
) -> dict[str, str]:
    """候補 revision の claim_object_builder claims を theory_claims へ **同期**する。

    呼び出し側のトランザクションで動く（commit しない）。パイプライン経路
    （:func:`persist_qualified_claims`）と同じ ``stable_key`` 規則で live 行と突合し、
    **DELETE は発行しない**（accept のたびに UUID が変わると C層の承認・D層の台帳が
    宙に浮くため。KO3）。

    Returns: agent claim_id → db id。
    """
    items: list[dict] = []
    for c in claims or []:
        if not isinstance(c, dict):
            continue
        agent_id = _text(c.get("claim_id"))
        raw_type = _text(c.get("claim_type"))
        equation_ids = _id_list(c.get("equation_ids"))
        source_scope = dict(c.get("source_scope") or {"section_id": c.get("section_id")})
        legacy_ids = _id_list(source_scope.get("legacy_ids"))
        if agent_id and agent_id not in legacy_ids:
            legacy_ids.append(agent_id)
        source_scope["legacy_ids"] = legacy_ids
        text_value = str(c.get("text") or "")
        normalized = str(c.get("normalized_text") or "") or text_value
        block_ids = _id_list([source_scope.get("block_id")])
        parent_agent_id = _text(c.get("parent_claim_id"))
        synthesis_method = _text(c.get("synthesis_method"))
        if parent_agent_id:
            origin = CLAIM_ORIGIN_ATOMIC_REWRITE
        elif synthesis_method or agent_id.startswith("synth_claim_"):
            origin = CLAIM_ORIGIN_EQUATION_SYNTHESIS
        else:
            origin = CLAIM_ORIGIN_CLAIM_OBJECT
        items.append({
            "agent_id": agent_id,
            "stable_key": ko_keys.claim_stable_key(document_id, normalized, block_ids),
            "parent_agent_id": parent_agent_id,
            "values": {
                "source_scope": source_scope,
                "claim_type": normalize_claim_type(raw_type),
                "claim_type_text": raw_type,
                "text": text_value,
                "normalized_text": normalized,
                "concepts": _plain(c.get("concepts") or []),
                "equation": {"equation_ids": equation_ids} if equation_ids else {},
                "support_status": _text(c.get("support_status")) or "source_backed",
                "evidence_text": "",
                "origin": origin,
                "claim_tier": (
                    _text(c.get("claim_tier"))
                    if _text(c.get("claim_tier")) in CLAIM_TIERS else ""
                ),
                "content_hash": _text(c.get("content_hash")),
                "review_status": _text(c.get("review_status")) or DEFAULT_REVIEW_STATUS,
            },
        })

    final_keys = ko_keys.dedupe_stable_keys(
        items,
        key_of=lambda item: item["stable_key"],
        agent_id_of=lambda item: item["agent_id"],
    )
    for item in items:
        item["stable_key"] = final_keys.get(item["agent_id"], item["stable_key"])

    sync = sync_live_rows(
        session,
        table=TABLE_CLAIMS,
        document_id=document_id,
        run_id=run_id,
        incoming=items,
        content_columns=_CLAIM_CONTENT_COLUMNS,
        preserved_columns=_CLAIM_PRESERVED_COLUMNS,
        agent_id_column="agent_claim_id",
        column_casts=_CLAIM_COLUMN_CASTS,
    )
    for item in items:
        parent_agent_id = item.get("parent_agent_id") or ""
        child_id = sync.id_map.get(item["agent_id"])
        parent_id = sync.id_map.get(parent_agent_id) if parent_agent_id else None
        if child_id and parent_id and child_id != parent_id:
            session.execute(
                sa_text(
                    f"""
                    UPDATE {TABLE_CLAIMS}
                    SET parent_claim_id = CAST(:parent_id AS uuid), updated_at = now()
                    WHERE id = CAST(:child_id AS uuid)
                    """
                ),
                {"parent_id": parent_id, "child_id": child_id},
            )
    _apply_remaps(
        session, document_id=document_id, run_id=run_id, kind="claim", remaps=sync.remaps,
    )
    return dict(sync.id_map)


def _rebuild_theory_components_in_session(
    session,
    document_id: str,
    components: list,
    claim_id_map: dict[str, str],
    run_id: str | None = None,
) -> dict[str, str]:
    """候補 revision の components を theory_components へ同期し、links を張り直す。

    claims と同じく DELETE を発行しない（links だけが明示例外。§4.1）。
    """
    claim_id_map = claim_id_map or {}
    claim_blocks = _claim_block_index(claim_id_map)
    items: list[dict] = []
    for comp in components or []:
        if not isinstance(comp, dict):
            continue
        agent_id = _text(comp.get("component_id"))
        evidence_refs = comp.get("evidence_refs") if isinstance(comp.get("evidence_refs"), dict) else {}
        linked_claim_ids = _id_list(comp.get("linked_claim_ids"))
        evidence_claims_agent = _id_list(
            linked_claim_ids + _id_list(evidence_refs.get("claim_ids"))
        )
        evidence_claims_db = [claim_id_map.get(a, a) for a in evidence_claims_agent]
        operation = _text(comp.get("operation")) or _text(comp.get("primary_operation"))
        block_ids = _component_block_ids(comp, claim_blocks, {})
        raw_component_type = _text(comp.get("component_type")) or _text(comp.get("responsibility_type"))
        agent_payload = {
            key: value for key, value in comp.items()
            if key not in _COMPONENT_PAYLOAD_EXCLUDED
        }
        items.append({
            "agent_id": agent_id,
            "stable_key": ko_keys.component_stable_key(
                document_id, _text(comp.get("label") or comp.get("name")), operation, block_ids
            ),
            "values": {
                "course_id": None,
                "name": _text(comp.get("label") or comp.get("name")) or "Untitled",
                "component_type": normalize_component_type(raw_component_type),
                "component_type_text": raw_component_type,
                "summary": str(comp.get("summary") or ""),
                "status": "candidate",
                "source_chunks": list(evidence_refs.get("source_chunks") or []),
                "inputs": list(comp.get("inputs") or []),
                "outputs": list(comp.get("outputs") or []),
                "preconditions": list(comp.get("preconditions") or []),
                "constraints": list(comp.get("constraints") or []),
                "invalid_conditions": list(comp.get("invalid_conditions") or []),
                "dependencies": list(comp.get("dependencies") or []),
                "blackbox_policy": {"default_level": "summary", "expand_if_unlearned": True},
                "validation_warnings": [],
                "teacher_notes": str(comp.get("teaching_takeaway") or ""),
                "source_scope": {"document_id": document_id, "legacy_ids": [agent_id]},
                "evidence_claims": evidence_claims_db,
                "maturity_level": _text(comp.get("maturity_level")) or "paper_claim",
                "maturity_source": _text(comp.get("maturity_source")) or "llm_proposed",
                "review_status": _text(comp.get("review_status")) or DEFAULT_REVIEW_STATUS,
                "cautions": list(comp.get("cautions") or []),
                "connectors": dict(comp.get("connectors") or {}),
                "internal_flow": list(comp.get("internal_flow") or []),
                "duplicate_candidates": [],
                "operation": operation,
                "teaching_takeaway": str(comp.get("teaching_takeaway") or ""),
                "teaching_granularity": dict(comp.get("teaching_granularity") or {}),
                "prerequisite_concepts": list(comp.get("prerequisite_concepts") or []),
                "assumptions": list(comp.get("assumptions") or []),
                "approximations": list(comp.get("approximations") or []),
                "linked_claim_ids": linked_claim_ids,
                "linked_equation_ids": _id_list(comp.get("linked_equation_ids")),
                "linked_evidence_ids": _id_list(comp.get("linked_evidence_ids")),
                "linked_derivation_ids": _id_list(comp.get("linked_derivation_ids")),
                "agent_payload": agent_payload,
            },
        })

    final_keys = ko_keys.dedupe_stable_keys(
        items,
        key_of=lambda item: item["stable_key"],
        agent_id_of=lambda item: item["agent_id"],
    )
    for item in items:
        item["stable_key"] = final_keys.get(item["agent_id"], item["stable_key"])

    sync = sync_live_rows(
        session,
        table=TABLE_COMPONENTS,
        document_id=document_id,
        run_id=run_id,
        incoming=items,
        content_columns=_COMPONENT_CONTENT_COLUMNS,
        preserved_columns=_COMPONENT_PRESERVED_COLUMNS,
        agent_id_column="agent_component_id",
        human_touched=_component_human_touched,
        protected_when_touched=_COMPONENT_PROTECTED_WHEN_TOUCHED,
        touch_columns=("maturity_source",),
    )
    id_map = dict(sync.id_map)

    # Dependency links（派生構造の明示例外: document 単位で張り直す）。
    session.execute(
        sa_text("DELETE FROM theory_component_links WHERE document_id = :doc"),
        {"doc": document_id},
    )
    for comp in components or []:
        if not isinstance(comp, dict):
            continue
        src_db = id_map.get(_text(comp.get("component_id")))
        if not src_db:
            continue
        for dep in comp.get("dependencies") or []:
            if not isinstance(dep, dict):
                continue
            dep_type = dep.get("dependency_type") or "depends_on"
            link_type = "requires" if dep_type == "requires" else "depends_on"
            for ref in dep.get("component_refs") or []:
                dst_db = id_map.get(_text(ref))
                if not dst_db or dst_db == src_db:
                    continue
                session.execute(
                    sa_text(
                        """
                        INSERT INTO theory_component_links (
                            course_id, document_id, source_component_id,
                            target_component_id, link_type, status, validation_result,
                            produced_by_run_id
                        )
                        VALUES (
                            NULL, :document_id, CAST(:src AS uuid), CAST(:dst AS uuid),
                            :link_type, 'candidate', CAST(:validation AS jsonb),
                            CAST(:run_id AS uuid)
                        )
                        """
                    ),
                    {
                        "document_id": document_id, "src": src_db, "dst": dst_db,
                        "link_type": link_type, "run_id": run_id,
                        "validation": _json_dumps({"agent_dependency_type": dep_type,
                                                   "reason": dep.get("reason")}),
                    },
                )
    _apply_remaps(
        session, document_id=document_id, run_id=run_id, kind="component", remaps=sync.remaps,
    )
    return id_map


def _remap_revision_graph(
    graph_payload: dict,
    component_id_map: dict[str, str],
    claim_id_map: dict[str, str],
) -> dict:
    """Map candidate agent ids to the freshly-created projection UUIDs.

    Component nodes and all graph endpoints must resolve to stored nodes. Claim
    references embedded in nodes/edges are remapped recursively. Unknown
    component ids are rejected instead of being persisted as ghost nodes.
    """
    graph = copy.deepcopy(graph_payload or {})
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    node_id_map: dict[str, str] = {}
    stored_node_ids: set[str] = set()

    for node in nodes:
        if not isinstance(node, dict):
            continue
        agent_id = str(
            node.get("component_id") or node.get("node_id") or node.get("id") or ""
        ).strip()
        if not agent_id:
            raise ValueError("component graph node is missing an id")
        graph_layer = str(node.get("graph_layer") or "").strip().lower()
        component_type = str(node.get("component_type") or "").strip()
        # TheoryOperationGraph contains graph-native aggregate/detail nodes whose
        # ``component_id`` is a graph identifier (theory_op_*/eq_op_*), not a
        # component_assembly entity.  Treating every node carrying component_id as
        # a projection-backed component made valid revision candidates impossible
        # to accept.  Explicit stored component nodes and ordinary component ids
        # still fail closed when they cannot be resolved.
        is_graph_native_node = (
            component_type in ("TheoryOperationNode", "EquationOperationNode")
            or graph_layer in ("equation_detail", "debug")
        )
        is_component_node = (
            agent_id in component_id_map
            or str(node.get("type") or "").lower() == "component"
            or (bool(node.get("component_id")) and not is_graph_native_node)
        )
        if is_component_node:
            db_id = component_id_map.get(agent_id)
            if not db_id:
                raise ValueError(f"component graph references unknown component id: {agent_id}")
        else:
            db_id = agent_id
        if db_id in stored_node_ids:
            raise ValueError(f"component graph contains duplicate node id: {db_id}")
        stored_node_ids.add(db_id)
        node_id_map[agent_id] = db_id
        if "id" in node or "node_id" not in node:
            node["id"] = db_id
        if "node_id" in node:
            node["node_id"] = db_id
        if is_component_node:
            node["component_id"] = db_id
            node["agent_component_id"] = agent_id
        remapped = _remap_nested_claim_refs(node, claim_id_map)
        node.clear()
        node.update(remapped)

    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        for key in (
            "source", "target", "from", "to",
            "source_component_id", "target_component_id",
        ):
            if key not in edge or edge[key] in (None, ""):
                continue
            agent_id = str(edge[key])
            db_id = node_id_map.get(agent_id)
            if not db_id or db_id not in stored_node_ids:
                raise ValueError(
                    f"component graph edge references unknown node id: {agent_id}"
                )
            edge[key] = db_id
        remapped = _remap_nested_claim_refs(edge, claim_id_map)
        edge.clear()
        edge.update(remapped)

    return _normalize_graph_payload_for_persist(graph)


def _rebuild_component_graph_in_session(
    session,
    document_id: str,
    graph_payload: dict,
    component_id_map: dict[str, str],
    claim_id_map: dict[str, str],
    run_id: str | None = None,
) -> None:
    remapped_graph = _remap_revision_graph(
        graph_payload, component_id_map or {}, claim_id_map or {}
    )
    session.execute(
        sa_text(
            """
            INSERT INTO theory_component_graphs (
                document_id, graph_json, scope, produced_by_run_id, updated_at
            )
            VALUES (
                :doc, CAST(:graph AS jsonb), CAST(:scope AS jsonb),
                CAST(:run_id AS uuid), now()
            )
            ON CONFLICT (document_id)
            DO UPDATE SET graph_json = EXCLUDED.graph_json,
                          produced_by_run_id = EXCLUDED.produced_by_run_id,
                          updated_at = now()
            """
        ),
        {"doc": document_id, "graph": _json_dumps(remapped_graph), "run_id": run_id,
         "scope": _json_dumps({"level": "paper"})},
    )


_REVISION_ARTIFACT_KEYS = {
    "baseline_inventory",
    "audit_checkpoints",
    "audit_results",
    "proposed_operations",
    "revision_operations",
    "candidate",
    "candidate_validation",
    "diff_report",
}


def _materializable_candidate_artifacts(candidate_artifacts: dict) -> dict:
    """Return normal pipeline artifacts safe to promote on accept."""
    return {
        str(key): value
        for key, value in (candidate_artifacts or {}).items()
        if key not in _REVISION_ARTIFACT_KEYS
    }


def accept_revision(
    *,
    document_id: str,
    run_id: str,
    expected_base_run_id: str | None,
    changed_by: str | None = None,
    comment: str = "",
    candidate_artifacts: dict | None = None,
    decision_metadata: dict | None = None,
) -> dict:
    """Accept a candidate revision in a single transaction (#407 / #410 P1-5).

    Switches the document active run base→candidate with optimistic concurrency,
    rebuilds ALL document-scoped projections (theory_claims, theory_components,
    theory_component_links, theory_component_graphs) from the candidate artifacts,
    marks the candidate accepted and the superseded revision base, and records a
    decision event — all in one transaction. Any projection failure rolls the
    whole thing back (active pointer + projections unchanged). Raises
    ``RevisionConflictError`` (→ 409) when the active run moved since the candidate
    was proposed, or the candidate is not ``proposed``.
    """
    candidate_artifacts = candidate_artifacts or {}
    promoted_artifacts = _materializable_candidate_artifacts(candidate_artifacts)
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT status, revision_status, base_run_id::text, run_type
                FROM document_analysis_runs
                WHERE id = CAST(:rid AS uuid)
                FOR UPDATE
                """
            ),
            {"rid": run_id},
        ).fetchone()
        if not row:
            raise ValueError(f"revision run {run_id} not found")
        _status, rev_status, base_id, run_type = row
        if run_type != "revision":
            raise ValueError(f"run {run_id} is not a revision run")
        if rev_status != "proposed":
            raise RevisionConflictError(
                f"revision {run_id} is not in 'proposed' state (revision_status={rev_status})"
            )
        if expected_base_run_id and base_id and expected_base_run_id != base_id:
            raise RevisionConflictError("base_run_id mismatch with candidate")

        # Materialize the adopted candidate into the normal artifact namespace.
        # Keep `candidate.candidate_artifacts` and all revision audit metadata
        # intact for lineage/diff inspection, while making the accepted run
        # readable by existing Export/Course Builder consumers. artifact の格納先は
        # 生成ログ表（§6 / KO6）なので、jsonb_set の deep merge は不要になった。
        _upsert_run_artifacts(session, run_id, promoted_artifacts)
        session.execute(
            sa_text(
                """
                UPDATE document_analysis_runs
                SET updated_at = now()
                WHERE id = CAST(:rid AS uuid)
                """
            ),
            {"rid": run_id},
        )

        switched = session.execute(
            sa_text(
                """
                UPDATE documents
                SET active_analysis_run_id = CAST(:cand AS uuid), updated_at = now()
                WHERE id = CAST(:doc AS uuid)
                  AND active_analysis_run_id IS NOT DISTINCT FROM CAST(:base AS uuid)
                """
            ),
            {"cand": run_id, "doc": document_id, "base": base_id},
        ).rowcount
        if switched != 1:
            raise RevisionConflictError(
                "active run changed since the candidate was proposed; refusing to accept"
            )

        # Projection rebuild (in-transaction): claims, components+links, graph —
        # all from the candidate artifacts. Any failure here rolls back the active
        # switch too (single transaction), so projections + active never diverge.
        claims = ((candidate_artifacts.get("claim_object_builder") or {}).get("claims")) or []
        components = ((candidate_artifacts.get("component_assembly") or {}).get("components")) or []
        graph_payload = candidate_artifacts.get("component_graph")
        claim_id_map = _rebuild_theory_claims_in_session(
            session, document_id, claims, run_id=run_id
        )
        component_id_map = _rebuild_theory_components_in_session(
            session, document_id, components, claim_id_map, run_id=run_id
        )
        if graph_payload is not None:
            _rebuild_component_graph_in_session(
                session,
                document_id,
                graph_payload,
                component_id_map,
                claim_id_map,
                run_id=run_id,
            )
        else:
            # 候補に component_graph が無い場合、components は上で同期され（一致行は
            # 同 UUID・不一致行は supersede / 新規 INSERT）ているため、古い
            # theory_component_graphs 行を残すと superseded・新規 UUID を指す stale
            # グラフになり、context_lens が component の上位/下位を引けなくなる。
            # 同一トランザクション内で明示削除し整合させる
            # （delete_component_graph と同義だが、accept_revision のトランザクションに
            # 同乗させるため inline で実行する）。
            session.execute(
                sa_text("DELETE FROM theory_component_graphs WHERE document_id = :doc"),
                {"doc": document_id},
            )

        session.execute(
            sa_text(
                """
                UPDATE document_analysis_runs
                SET revision_status = 'accepted', status = 'completed',
                    completed_at = now(), updated_at = now()
                WHERE id = CAST(:rid AS uuid)
                """
            ),
            {"rid": run_id},
        )
        # The superseded base — only mark revisions; an initial base keeps NULL.
        if base_id:
            session.execute(
                sa_text(
                    """
                    UPDATE document_analysis_runs
                    SET revision_status = 'superseded', updated_at = now()
                    WHERE id = CAST(:base AS uuid) AND run_type = 'revision'
                    """
                ),
                {"base": base_id},
            )
        _insert_revision_decision(
            session, run_id=run_id, old_status="proposed", new_status="accepted",
            changed_by=changed_by,
            metadata={"decision": "accept", "comment": comment,
                      "document_id": document_id, "base_run_id": base_id,
                      # Applied/excluded operation sets for the partial-adoption
                      # audit trail (#415).
                      **(decision_metadata or {})},
        )
        session.commit()
        return {"accepted": True, "active_run_id": run_id, "superseded_run_id": base_id}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reject_revision(
    *,
    run_id: str,
    changed_by: str | None = None,
    comment: str = "",
) -> dict:
    """Reject a candidate revision. Never touches the active run or projections."""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT revision_status, run_type
                FROM document_analysis_runs
                WHERE id = CAST(:rid AS uuid)
                FOR UPDATE
                """
            ),
            {"rid": run_id},
        ).fetchone()
        if not row:
            raise ValueError(f"revision run {run_id} not found")
        rev_status, run_type = row
        if run_type != "revision":
            raise ValueError(f"run {run_id} is not a revision run")
        if rev_status in ("accepted", "superseded"):
            raise RevisionConflictError(
                f"cannot reject revision in '{rev_status}' state"
            )
        session.execute(
            sa_text(
                """
                UPDATE document_analysis_runs
                SET revision_status = 'rejected', status = 'completed',
                    completed_at = now(), updated_at = now()
                WHERE id = CAST(:rid AS uuid)
                """
            ),
            {"rid": run_id},
        )
        _insert_revision_decision(
            session, run_id=run_id, old_status=rev_status or "proposed",
            new_status="rejected", changed_by=changed_by,
            metadata={"decision": "reject", "comment": comment},
        )
        session.commit()
        return {"rejected": True, "run_id": run_id}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def load_source_chunk_index(*, document_id: str) -> list[dict]:
    """Load persisted source chunk metadata for downstream evidence resolution."""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT id::text AS chunk_id, chunk_index, section_id, block_ids,
                       page_start, page_end, text
                FROM chunks
                WHERE document_id = CAST(:document_id AS uuid)
                ORDER BY chunk_index ASC
                """
            ),
            {"document_id": document_id},
        ).mappings().all()
        return [dict(row) for row in rows]
    finally:
        session.close()
