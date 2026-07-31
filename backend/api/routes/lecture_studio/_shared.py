"""Episteme Graph — Lecture Script Studio: 境界横断ヘルパー (Tier 3-17a パッケージ分割)。

scripts.py / pipeline.py / topics.py の複数モジュールから共有される、チャンク取得・
数式プレビュー・構造抽出などの非ルーティングヘルパーをここに集約する。
FastAPI の ``router`` は持たない（各サブモジュールが独自の router を持ち、
``__init__.py`` がそれらを束ねる）。
"""

from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy import text as sa_text

from dependencies import ROLE_SYSTEM_ADMIN
from services import get_editable_course_data, resolve_document_access
from core.config import get_settings
from core.course_data import course_source_material_ids
from core.document_sections import enrich_chunks_with_sections
from core.lecture import normalize_to_placeholder_format
from core.llm_worker.cost_gate import CostGate, today_str
from core.postgres import get_session as _pg_session


# ---------------------------------------------------------------------------
# Phase 2-D: 原稿スタジオ rewrite/save の権限ゲート（🔴 セキュリティ修正）
#
# chunk_id を直指定するエンドポイント（scripts.py の rewrite/save）は `_require_teacher`
# のみで、chunk が属する document への編集権限を確認していなかったため、他教員の教材の
# チャンクも書き換え可能だった（設計書 assistant_common_infra_design.md §5）。
# `_ensure_document_editable`（routes/theory_components.py）と同じ fail-closed 流儀
# （不可視・権限なしは 404「Chunk not found」— 存在の有無を権限で区別しない, I5）で揃える。
# ---------------------------------------------------------------------------


def _material_id_for_chunk(chunk_id: str) -> str | None:
    """chunk_id から material_id を1件引く。chunk が存在しなければ None。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT material_id FROM chunks WHERE id = CAST(:cid AS uuid)"),
            {"cid": chunk_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return None
    return row[0] or ""


def _ensure_chunk_editable(chunk_id: str, current_user: dict) -> str:
    """chunk が属する document への編集権限を要求し、material_id を返す。

    判定は ``services.resolve_document_access``（owner / editor グループ /
    SYSTEM_ADMIN）に委譲する。chunk が存在しない・material_id が解決できない・
    編集権限がない、のいずれも同一の 404「Chunk not found」にする
    （fail-closed。既存の教材の有無で分岐させない）。
    """
    material_id = _material_id_for_chunk(chunk_id)
    if material_id is None:
        raise HTTPException(status_code=404, detail="Chunk not found")
    if current_user.get("role") == ROLE_SYSTEM_ADMIN:
        return material_id
    if not material_id or not resolve_document_access(current_user["id"], material_id).can_edit:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return material_id


# ---------------------------------------------------------------------------
# コース単位の権限ゲート（原稿スタジオ / 教材図スタジオの共有）
#
# ``_course_data_for_studio_editable`` は topics.py の module-private helper だったが、
# 教材図スタジオ（`docs/features/teaching_figure_studio_design.md` §8）も同じ編集権限
# ゲートを使うためここへ移した（topics.py 側は委譲に置き換え、外部からの参照名
# ``routes.lecture_studio.topics._course_data_for_studio_editable`` は不変）。
# ---------------------------------------------------------------------------


def _course_data_for_studio_editable(course_id: str, current_user: dict) -> dict:
    """編集権限チェック付きでコースデータを返す（Phase 2-D 🔴）。

    ``rewrite_lecture_studio_course_topic`` は LLM を呼んでコース内容のドラフトを
    生成する書き込み系の操作だが、旧実装は ``_course_data_for_studio``（閲覧権限のみ）
    を使っていたため、viewer 権限しかない教員でも呼び出せてしまっていた
    （設計書 assistant_common_infra_design.md §5。scripts.py の設定 PUT 系
    （``get_editable_course_data`` + SYSTEM_ADMIN フォールバック）と権限水準を揃える）。
    """
    course_data = get_editable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")
    return course_data


def ensure_course_owner(course_id: str, current_user: dict) -> None:
    """コース所有者 / SYSTEM_ADMIN のみ許可する（それ以外は 403）。

    トピック本文の保存（``save_lecture_studio_course_topic``）と**同じ判定**にするための
    共通ゲート（教材図スタジオ設計書 §8「権限の非対称に注意」）。editor 権限の教員が
    図だけ作れて本文へ反映できない中途半端を作らないため、図の生成・保存・採用も
    トピック保存と同じ所有者ゲートに揃える。

    コース行が無ければ 404（存在の有無で 403/404 を分けるのは所有者判定より前の段階の
    ``_course_data_for_studio_editable`` が既に済ませている）。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT user_id FROM learning_courses WHERE id = :course_id LIMIT 1"),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        raise HTTPException(status_code=404, detail="Course not found")
    if str(row[0]) != str(current_user["id"]) and current_user.get("role") != ROLE_SYSTEM_ADMIN:
        raise HTTPException(status_code=403, detail="Forbidden")


def course_data_for_owner(course_id: str, current_user: dict) -> dict:
    """所有者 / SYSTEM_ADMIN 限定でコースデータを返す（教材図スタジオの共通入口）。

    ``_course_data_for_studio_editable``（404 fail-closed）→ ``ensure_course_owner``
    （403）の順に通す。editor 共有の教員は course_data は引けるが 403 になる。
    """
    course_data = _course_data_for_studio_editable(course_id, current_user)
    ensure_course_owner(course_id, current_user)
    return course_data


# ---------------------------------------------------------------------------
# Phase 2-D: 原稿スタジオ rewrite のコスト上限（設計書 §1）
#
# scripts.py の chunk 単位 rewrite と topics.py のトピック単位 rewrite の両経路が
# 同一の CostGate インスタンスを共有する（`lecture_rewrite_max_calls_per_day`、
# キーは (today_str(), user_id)）。バッチ生成・TTS・手動保存は対象外（rewrite の
# LLM 呼び出しのみを数える）。
# ---------------------------------------------------------------------------

_lecture_rewrite_cost_gate = CostGate()


def consume_lecture_rewrite_quota(user_id: str) -> None:
    """原稿スタジオ rewrite の日次上限を1消費する。超過時は 429 + 事実文（I2、数値非表示）。

    呼び出し順序: 権限ゲート（fail-closed 404）より**後**、LLM 呼び出しより**前**
    （拒否されたリクエストを数えない）。
    """
    settings = get_settings()
    cap = int(getattr(settings, "lecture_rewrite_max_calls_per_day", 100) or 0)
    ok = _lecture_rewrite_cost_gate.check_and_count(
        daily_limit=cap, daily_key=(today_str(), user_id)
    )
    if not ok:
        raise HTTPException(
            status_code=429,
            detail="本日の原稿書き換え回数の上限に達しました。明日以降に再度お試しください。",
        )


# ---------------------------------------------------------------------------
# Helper: コースに紐づくチャンクを取得
# ---------------------------------------------------------------------------


def _get_course_chunks(course_data: dict) -> list[dict]:
    """コースのソース教材からチャンクを取得する。"""
    material_ids = course_source_material_ids(course_data)

    if not material_ids:
        return []

    session = _pg_session()
    try:
        mid_placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
        params: dict = {}
        for i, mid in enumerate(material_ids):
            params[f"mid_{i}"] = mid

        where_clause = f"c.material_id IN ({mid_placeholders})"
        rows = session.execute(
            sa_text(f"""
                SELECT c.id, c.chunk_index, c.text, c.display_text, c.spoken_text, c.formulas,
                       c.material_id, c.document_id, c.page_start, c.page_end,
                       c.smiles_dsl, c.variables, c.ancestors,
                       d.knowledge_graph, c.spoken_language
                FROM chunks c
                LEFT JOIN documents d ON c.document_id = d.id
                WHERE ({where_clause})
                  AND c.text IS NOT NULL AND c.text != ''
                ORDER BY c.chunk_index
            """),
            params,
        ).fetchall()

        chunks = []
        graph_structure_cache: dict[str, dict] = {}
        document_ids = sorted({str(row[7]) for row in rows if row[7]})
        equation_previews = _load_equation_formula_previews(session, document_ids)
        for row in rows:
            raw_text = row[2] or ""
            display_text = row[3] or raw_text
            spoken_text = row[4] or display_text
            formulas = row[5] if row[5] else []
            # 旧フォーマット（$...$）のデータをプレースホルダー方式に正規化
            display_text, formulas = normalize_to_placeholder_format(display_text, formulas)
            material_id = row[6] or ""
            document_id = str(row[7]) if row[7] else ""
            formulas = _merge_equation_formula_previews(
                formulas,
                document_id=document_id,
                page_start=row[8],
                page_end=row[9],
                equation_previews=equation_previews,
            )
            display_text = _replace_equation_preview_text(display_text, formulas)
            knowledge_graph = _json_obj(row[13])
            graph_elements = _derive_chunk_graph_elements(
                f"{raw_text}\n{display_text}",
                knowledge_graph,
                formulas,
            )
            graph_structure = {}
            if document_id:
                if document_id not in graph_structure_cache:
                    graph_structure_cache[document_id] = _extract_component_graph_structure(document_id)
                graph_structure = graph_structure_cache.get(document_id) or {}
            variables = (
                row[11]
                if row[11] is not None
                else _extract_document_variables(knowledge_graph) or graph_structure.get("variables")
            )
            ancestors = (
                row[12]
                if isinstance(row[12], list)
                else _extract_document_edges(knowledge_graph) or graph_structure.get("ancestors") or []
            )
            smiles_dsl = row[10] or _extract_document_dsl(knowledge_graph) or graph_structure.get("smiles_dsl", "")
            chunks.append({
                "id": str(row[0]),
                "chunk_index": row[1],
                "text": display_text,
                "raw_text": raw_text,
                "display_text": display_text,
                "spoken_text": spoken_text,
                "stored_spoken_text": row[4] or "",
                "formulas": formulas,
                "material_id": material_id,
                "document_id": document_id,
                "page_start": row[8],
                "page_end": row[9],
                "pdf_url": f"/admin/materials/{material_id}/pdf" if material_id else None,
                "smiles_dsl": smiles_dsl,
                "variables": variables,
                "ancestors": ancestors,
                "graph_elements": graph_elements,
                # レクチャースライド同期 (migration 040): 原稿の生成言語。
                # len<=14 の古いモック行は未指定として扱う（後方互換）。
                "spoken_language": (row[14] if len(row) > 14 else None) or "ja",
            })
        return enrich_chunks_with_sections(chunks)
    finally:
        session.close()


def _load_equation_formula_previews(session, document_ids: list[str]) -> dict[str, list[dict]]:
    """Load EquationSemanticAgent crop previews from the latest artifacts."""
    if not document_ids:
        return {}
    placeholders = ", ".join(f":doc_{i}" for i in range(len(document_ids)))
    params = {f"doc_{i}": doc_id for i, doc_id in enumerate(document_ids)}
    rows = session.execute(
        sa_text(f"""
            SELECT DISTINCT ON (document_id)
                   document_id, stage_outputs
            FROM document_analysis_runs
            WHERE document_id IN ({placeholders})
            ORDER BY document_id, created_at DESC
        """),
        params,
    ).fetchall()
    out: dict[str, list[dict]] = {}
    for row in rows:
        doc_id = str(row[0]) if row[0] else ""
        stage_outputs = _json_obj(row[1])
        artifacts = stage_outputs.get("_artifacts") if isinstance(stage_outputs.get("_artifacts"), dict) else {}
        eq_artifact = artifacts.get("equation_semantics") if isinstance(artifacts, dict) else None
        if not isinstance(eq_artifact, dict):
            continue
        previews: list[dict] = []
        for record in eq_artifact.get("equations") or []:
            if not isinstance(record, dict):
                continue
            src = record.get("source_extraction") or {}
            if not isinstance(src, dict):
                continue
            source_image = src.get("source_image")
            if not isinstance(source_image, dict) or not source_image.get("data_base64"):
                continue
            rec = record.get("reconstruction") or {}
            if not isinstance(rec, dict):
                rec = {}
            source_location = src.get("source_location") if isinstance(src.get("source_location"), dict) else {}
            previews.append({
                "id": record.get("equation_id") or f"eq_{len(previews)}",
                "latex": rec.get("latex") or src.get("latex") or src.get("plain_text") or "",
                "spoken": rec.get("plain_text") or src.get("plain_text") or "",
                "is_display": True,
                "label": record.get("label"),
                "block_id": source_location.get("block_id"),
                "source_location": source_location,
                "source_image": source_image,
                "raw_text": src.get("raw_text") or "",
                "needs_math_review": bool(src.get("needs_math_review", False)),
                "review_reason": list(src.get("review_reason", [])) if isinstance(src.get("review_reason"), list) else [],
            })
        if previews:
            out[doc_id] = previews
    return out


def _merge_equation_formula_previews(
    formulas: list[dict],
    *,
    document_id: str,
    page_start: int | None,
    page_end: int | None,
    equation_previews: dict[str, list[dict]],
) -> list[dict]:
    if not document_id:
        return formulas
    previews = equation_previews.get(document_id) or []
    if not previews:
        return formulas
    merged = [dict(f) for f in formulas if isinstance(f, dict)]
    by_block = {str(f.get("block_id")): f for f in merged if f.get("block_id")}
    by_id = {str(f.get("id")): f for f in merged if f.get("id")}
    by_label = {str(f.get("label")): f for f in merged if f.get("label")}
    for preview in previews:
        if not _equation_preview_matches_chunk(preview, page_start, page_end, by_block):
            continue
        target = None
        block_id = str(preview.get("block_id") or "")
        label = str(preview.get("label") or "")
        eq_id = str(preview.get("id") or "")
        if block_id:
            target = by_block.get(block_id)
        if target is None and eq_id:
            target = by_id.get(eq_id)
        if target is None and label:
            target = by_label.get(label)
        if target is None:
            merged.append(dict(preview))
            continue
        target["source_image"] = preview.get("source_image")
        target["source_location"] = preview.get("source_location")
        target["needs_math_review"] = preview.get("needs_math_review", False)
        target["review_reason"] = preview.get("review_reason") or []
        if not target.get("latex") and preview.get("latex"):
            target["latex"] = preview.get("latex")
        if not target.get("spoken") and preview.get("spoken"):
            target["spoken"] = preview.get("spoken")
    return merged


def _equation_preview_matches_chunk(
    preview: dict,
    page_start: int | None,
    page_end: int | None,
    formulas_by_block: dict[str, dict],
) -> bool:
    block_id = str(preview.get("block_id") or "")
    if block_id and block_id in formulas_by_block:
        return True
    loc = preview.get("source_location") if isinstance(preview.get("source_location"), dict) else {}
    page = loc.get("page")
    try:
        page_num = int(page)
    except Exception:
        return False
    if page_start is None and page_end is None:
        return False
    start = page_start if page_start is not None else page_num
    end = page_end if page_end is not None else start
    return int(start) <= page_num <= int(end)


def _replace_equation_preview_text(display_text: str, formulas: list[dict]) -> str:
    """Replace broken PDF math snippets in display text with formula placeholders."""
    if not display_text or not formulas:
        return display_text
    updated = display_text
    for formula in formulas:
        if not isinstance(formula, dict):
            continue
        raw_text = str(formula.get("raw_text") or "").strip()
        formula_id = str(formula.get("id") or "").strip()
        if not raw_text or not formula_id:
            continue
        placeholder = formula_id if formula_id.startswith("[[") else f"[[{formula_id}]]"
        variants = _equation_text_variants(raw_text)
        # Replace longer variants first so multi-line equations collapse before
        # shorter subfragments can leave broken residue in the text.
        for variant in sorted(variants, key=len, reverse=True):
            if len(variant.strip()) < 4:
                continue
            updated = updated.replace(variant, placeholder)
    return updated


def _equation_text_variants(raw_text: str) -> list[str]:
    text = str(raw_text or "").strip()
    if not text:
        return []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    variants = {
        text,
        "\n".join(lines),
        "\n\n".join(lines),
        " ".join(lines),
    }
    # PyMuPDF block text is sometimes persisted after chunk joining with blank
    # lines around each block; keep variants intentionally small and exact.
    return [v for v in variants if v]


def _json_obj(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _extract_document_dsl(knowledge_graph: dict) -> str:
    abstract = knowledge_graph.get("abstract_structure")
    if isinstance(abstract, dict):
        dsl = str(abstract.get("smiles_dsl") or "").strip()
        if dsl:
            return dsl
    return str(knowledge_graph.get("smiles_dsl") or "").strip()


def _extract_component_graph_structure(document_id: str) -> dict:
    """Return document-level DSL saved by the agent pipeline for structure views."""
    if not document_id:
        return {}
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT graph_json
                FROM theory_component_graphs
                WHERE document_id = :document_id
                ORDER BY updated_at DESC
                LIMIT 1
            """),
            {"document_id": document_id},
        ).fetchone()
    finally:
        session.close()
    graph = _json_obj(row[0]) if row else {}
    dsl = graph.get("dsl") if isinstance(graph.get("dsl"), dict) else {}
    nodes = dsl.get("nodes") if isinstance(dsl.get("nodes"), list) else []
    edges = dsl.get("edges") if isinstance(dsl.get("edges"), list) else []
    if not nodes and not edges:
        return {}
    node_lines = []
    variables = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        value = str(node.get("value") or "").strip()
        node_type = str(node.get("node_type") or "Node").strip()
        if not node_id and not value:
            continue
        node_lines.append(f"{node_id}:{node_type}({value})")
        variables.append({
            "id": node_id or value,
            "name": value or node_id,
            "type": node_type,
            "description": "",
        })
    edge_lines = []
    ancestors = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("from") or edge.get("source") or "").strip()
        target = str(edge.get("to") or edge.get("target") or "").strip()
        predicate = str(edge.get("predicate") or edge.get("relation") or "RELATED_TO").strip()
        verb = str(edge.get("verb") or "").strip()
        polarity = str(edge.get("polarity") or "").strip()
        if not source or not target:
            continue
        label = "/".join(part for part in (predicate, verb, polarity) if part)
        edge_lines.append(f"{source} -[{label}]-> {target}")
        ancestors.append({
            "source": source,
            "target": target,
            "relation": predicate,
            "verb": verb,
            "polarity": polarity,
        })
    smiles_dsl = "\n".join([*node_lines, *edge_lines]).strip()
    return {"smiles_dsl": smiles_dsl, "variables": variables or None, "ancestors": ancestors}


def _extract_document_variables(knowledge_graph: dict) -> dict | list | None:
    abstract = knowledge_graph.get("abstract_structure")
    if isinstance(abstract, dict) and abstract.get("variables"):
        return abstract.get("variables")
    concepts = knowledge_graph.get("concepts")
    if isinstance(concepts, list) and concepts:
        return [
            {
                "id": c.get("id") or c.get("name"),
                "name": c.get("name") or c.get("id"),
                "type": c.get("type"),
                "description": c.get("description"),
            }
            for c in concepts
            if isinstance(c, dict)
        ]
    return None


def _extract_document_edges(knowledge_graph: dict) -> list:
    abstract = knowledge_graph.get("abstract_structure")
    if isinstance(abstract, dict) and isinstance(abstract.get("edges"), list):
        return abstract.get("edges") or []
    relationships = knowledge_graph.get("relationships")
    return relationships if isinstance(relationships, list) else []


def _derive_chunk_graph_elements(
    text: str,
    knowledge_graph: object,
    formulas: list[dict] | None = None,
) -> list[dict]:
    """チャンク本文に現れる knowledge_graph 要素を構造確認用に返す。"""
    graph = _json_obj(knowledge_graph)
    concepts = graph.get("concepts", []) if isinstance(graph.get("concepts"), list) else []
    relationships = graph.get("relationships", []) if isinstance(graph.get("relationships"), list) else []

    concept_by_id: dict[str, dict] = {}
    elements: list[dict] = []
    seen_concepts: set[str] = set()

    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        cid = str(concept.get("id") or concept.get("name") or "").strip()
        name = str(concept.get("name") or cid).strip()
        if not cid or not name:
            continue
        concept_by_id[cid] = concept
        if name in text or cid in text:
            seen_concepts.add(cid)
            elements.append({
                "type": "concept",
                "id": cid,
                "label": name,
                "description": concept.get("description") or "",
                "status": "registered",
            })

    for rel in relationships:
        if not isinstance(rel, dict):
            continue
        source = str(rel.get("source") or "").strip()
        target = str(rel.get("target") or "").strip()
        relation = str(rel.get("relation") or "RELATED_TO").strip()
        if not source or not target:
            continue
        source_name = str(concept_by_id.get(source, {}).get("name") or source)
        target_name = str(concept_by_id.get(target, {}).get("name") or target)
        if source in seen_concepts and target in seen_concepts:
            elements.append({
                "type": "relationship",
                "id": f"{source}:{relation}:{target}",
                "label": f"{source_name} -[{relation}]-> {target_name}",
                "description": rel.get("description") or "",
                "status": "registered",
            })

    for formula in (formulas or [])[:8]:
        if not isinstance(formula, dict):
            continue
        formula_id = str(formula.get("id") or "").strip()
        latex = str(formula.get("latex") or "").strip()
        if not formula_id and not latex:
            continue
        elements.append({
            "type": "formula",
            "id": formula_id or latex[:80],
            "label": formula_id or "formula",
            "description": latex,
            "status": "chunk_formula",
        })

    if not elements:
        for concept in concepts[:8]:
            if not isinstance(concept, dict):
                continue
            cid = str(concept.get("id") or concept.get("name") or "").strip()
            name = str(concept.get("name") or cid).strip()
            if not cid or not name:
                continue
            elements.append({
                "type": "concept",
                "id": cid,
                "label": name,
                "description": concept.get("description") or "",
                "status": "document_graph",
            })

    return elements[:12]


def _chunk_status(chunk: dict) -> str:
    """チャンクのスクリプトステータスを判定する。"""
    if not chunk.get("stored_spoken_text"):
        return "ungenerated"
    # 音声キャッシュがあれば audio_ready
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                "SELECT 1 FROM lecture_audio_cache WHERE chunk_id = CAST(:cid AS uuid) LIMIT 1"
            ),
            {"cid": chunk["id"]},
        ).fetchone()
        if row:
            return "audio_ready"
    except Exception:
        pass
    finally:
        session.close()
    return "generated"


def _get_system_admin_course_data(course_id: str) -> dict | None:
    """SYSTEM_ADMIN のシステム統計画面用に course_id だけでコースデータを取得する。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT data FROM learning_courses WHERE id = :course_id LIMIT 1"),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()
    if not row or not row[0]:
        return None
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])
