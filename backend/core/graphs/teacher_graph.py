"""TeacherGraph — 教師向けコース構築パイプライン。

ワークフロー:
  DocumentParse (LLM不使用)
    → StructureExtraction (Standard)
    → ConceptGraphBuilder (Deep)
    → DiffReviewFormatter (Fast)
    → 教師UIへDiffデータを返却 (査読待ち状態)

グラフDBへの直接書き込みは行わず、教師がレビューするための
Diffデータ（JSON）を返す設計。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.graph import END, StateGraph

from core.graphs.state import TeacherState
from core.llm import generate_text, get_llm_params

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ノード実装
# ---------------------------------------------------------------------------


def document_parse_node(state: TeacherState) -> dict[str, Any]:
    """PDF → テキストチャンク変換 (LLM 不使用)。

    GROBID を使用し、利用不可の場合は PyMuPDF にフォールバックする。
    """
    from core.extractor import (
        extract_tei_xml_from_pdf_bytes,
        parse_tei_to_logical_chunks,
    )

    pdf_bytes = state["pdf_bytes"]
    tei_xml = ""
    chunks: list[str] = []

    # GROBID による TEI XML 抽出を試みる
    try:
        tei_xml = extract_tei_xml_from_pdf_bytes(pdf_bytes)
        chunks = parse_tei_to_logical_chunks(tei_xml)
        logger.info("DocumentParse: GROBID succeeded, %d chunks extracted", len(chunks))
    except Exception as exc:
        logger.warning("GROBID failed (%s), falling back to PyMuPDF", exc)

    # フォールバック: PyMuPDF
    if not chunks:
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            full_text = ""
            for page in doc:
                full_text += page.get_text() + "\n"
            doc.close()

            # 簡易チャンク分割 (4000文字ごと)
            chunk_size = 4000
            for i in range(0, len(full_text), chunk_size):
                chunk = full_text[i : i + chunk_size].strip()
                if chunk:
                    chunks.append(chunk)

            logger.info("DocumentParse: PyMuPDF fallback, %d chunks", len(chunks))
        except Exception as fallback_exc:
            logger.error("PyMuPDF fallback also failed: %s", fallback_exc)
            return {"chunks": [], "tei_xml": "", "error": str(fallback_exc)}

    return {"chunks": chunks, "tei_xml": tei_xml}


def structure_extraction_node(state: TeacherState) -> dict[str, Any]:
    """チャンクから論理セクション・キーワードを抽出 (Standard モード)。"""
    params = get_llm_params("standard")
    chunks = state.get("chunks", [])

    if not chunks:
        return {"sections": [], "error": "no_chunks_to_extract"}

    # チャンクを連結して LLM に投入 (先頭5チャンクに制限)
    chunk_text = "\n\n---\n\n".join(
        f"[チャンク {i + 1}]\n{c}" for i, c in enumerate(chunks[:5])
    )

    prompt = (
        "あなたは学術論文の構造解析専門のAIです。\n"
        "以下のテキストチャンクを分析し、論理的なセクション構造を抽出してください。\n\n"
        f"{chunk_text}\n\n"
        '出力形式 (JSON配列のみ、他のテキストは不要):\n'
        "[{\n"
        '  "title": "<セクションのタイトル>",\n'
        '  "keywords": ["keyword1", "keyword2", ...],\n'
        '  "summary": "<セクションの要約 (2-3文)>"\n'
        "}]\n\n"
        "抽出のポイント:\n"
        "- 各セクションの主要テーマを明確にする\n"
        "- 物理学・数学の専門用語はそのまま保持する\n"
        "- 数式が含まれる場合はLaTeX記法で記録する"
    )

    try:
        result = generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=params["model"],
            reasoning_effort=params["reasoning_effort"],
        )
        sections = json.loads(
            result.strip().removeprefix("```json").removesuffix("```").strip()
        )
        return {"sections": sections}
    except Exception as exc:
        logger.warning("StructureExtraction parse failed: %s", exc)
        return {"sections": [], "error": f"structure_extraction_failed: {exc}"}


def concept_graph_builder_node(state: TeacherState) -> dict[str, Any]:
    """概念グラフ (REQUIRES エッジ + Misconceptions) を構築 (Deep モード)。

    最重要プロセス: 物理学の概念の依存関係と学生が陥りやすい
    誤解パターンを高度な推論で抽出する。
    """
    params = get_llm_params("deep")
    sections = state.get("sections", [])
    chunks = state.get("chunks", [])

    if not sections and not chunks:
        return {"concept_graph": {}, "error": "no_data_for_graph"}

    # セクション情報 + 元チャンク (先頭3つ) を入力に使う
    sections_text = json.dumps(sections, ensure_ascii=False, indent=2) if sections else "[]"
    chunk_sample = "\n---\n".join(chunks[:3]) if chunks else "(なし)"

    prompt = (
        "あなたは大学院レベルの物理学知識グラフを構築する専門AIです。\n"
        "以下の論文データから概念グラフを構築してください。\n\n"
        f"## 抽出済みセクション構造\n{sections_text}\n\n"
        f"## 元テキスト (サンプル)\n{chunk_sample}\n\n"
        '出力形式 (JSONのみ):\n'
        "{\n"
        '  "nodes": [\n'
        '    {"id": "<一意ID>", "label": "<概念名>", "type": "<OntologyType>", '
        '"description": "<概念の説明>"}\n'
        "  ],\n"
        '  "edges": [\n'
        '    {"source": "<nodeID>", "target": "<nodeID>", '
        '"predicate": "<REQUIRES|CAUSES|INHIBITS|CORRELATES|DEFINES|MEASURES|TRANSFORMS|CONTAINS|EQUIVALENT>", '
        '"description": "<関係の説明>"}\n'
        "  ],\n"
        '  "misconceptions": [\n'
        '    {"concept": "<関連概念>", "misconception": "<よくある誤解>", '
        '"correction": "<正しい理解>"}\n'
        "  ]\n"
        "}\n\n"
        "構築のルール:\n"
        "- REQUIRES エッジは前提知識の依存関係を表す (A を学ぶ前に B が必要)\n"
        "- CONTAINS エッジは包含関係を表す (A は B の構成要素)\n"
        "- OntologyType は以下から選択:\n"
        "  AGENT, EVENT, RESOURCE, INTENTIONAL_MOMENT,\n"
        "  MATHEMATICAL_OBJECT, PHYSICAL_PHENOMENON, THEORETICAL_FRAMEWORK,\n"
        "  THEOREM, SYMMETRY, PARTICLE\n"
        "- misconceptions は学生が陥りやすい誤解パターンを網羅する\n"
        "- 論理破綻がないか十分に検証すること"
    )

    try:
        result = generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=params["model"],
            reasoning_effort=params["reasoning_effort"],
        )
        graph = json.loads(
            result.strip().removeprefix("```json").removesuffix("```").strip()
        )
        return {"concept_graph": graph}
    except Exception as exc:
        logger.warning("ConceptGraphBuilder parse failed: %s", exc)
        return {"concept_graph": {}, "error": f"graph_build_failed: {exc}"}


def diff_review_formatter_node(state: TeacherState) -> dict[str, Any]:
    """既存グラフとの差分データを教師レビュー用に整形 (Fast モード)。

    グラフDBへの直接書き込みは行わず、査読用のDiffデータを返す。
    """
    params = get_llm_params("fast")
    concept_graph = state.get("concept_graph", {})
    paper_id = state.get("paper_id", "unknown")

    if not concept_graph:
        return {
            "diff_data": {
                "paper_id": paper_id,
                "status": "error",
                "message": "No concept graph was generated",
            }
        }

    prompt = (
        "以下の概念グラフデータを教師がレビューしやすい差分形式に整形してください。\n"
        "整形後のJSONのみ出力してください。\n\n"
        f"入力データ:\n{json.dumps(concept_graph, ensure_ascii=False, indent=2)}\n\n"
        '出力形式 (JSONのみ):\n'
        "{\n"
        f'  "paper_id": "{paper_id}",\n'
        '  "status": "pending_review",\n'
        '  "summary": "<変更の概要 (1-2文)>",\n'
        '  "additions": {\n'
        '    "nodes": [<新規追加ノード>],\n'
        '    "edges": [<新規追加エッジ>],\n'
        '    "misconceptions": [<新規追加の誤解パターン>]\n'
        "  },\n"
        '  "statistics": {\n'
        '    "total_nodes": <数値>,\n'
        '    "total_edges": <数値>,\n'
        '    "total_misconceptions": <数値>\n'
        "  }\n"
        "}"
    )

    try:
        result = generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=params["model"],
            reasoning_effort=params["reasoning_effort"],
        )
        diff = json.loads(
            result.strip().removeprefix("```json").removesuffix("```").strip()
        )
        return {"diff_data": diff}
    except Exception as exc:
        logger.warning("DiffReviewFormatter parse failed: %s", exc)
        # フォールバック: 生の concept_graph をそのまま返す
        nodes = concept_graph.get("nodes", [])
        edges = concept_graph.get("edges", [])
        misconceptions = concept_graph.get("misconceptions", [])
        return {
            "diff_data": {
                "paper_id": paper_id,
                "status": "pending_review",
                "summary": "概念グラフの自動整形に失敗したため、生データを返却します。",
                "additions": {
                    "nodes": nodes,
                    "edges": edges,
                    "misconceptions": misconceptions,
                },
                "statistics": {
                    "total_nodes": len(nodes),
                    "total_edges": len(edges),
                    "total_misconceptions": len(misconceptions),
                },
            }
        }


# ---------------------------------------------------------------------------
# グラフ構築
# ---------------------------------------------------------------------------

def build_teacher_graph() -> StateGraph:
    """TeacherGraph (教師向けコース構築パイプライン) を構築して返す。"""
    graph = StateGraph(TeacherState)

    # ノード登録
    graph.add_node("document_parse", document_parse_node)
    graph.add_node("structure_extraction", structure_extraction_node)
    graph.add_node("concept_graph_builder", concept_graph_builder_node)
    graph.add_node("diff_review_formatter", diff_review_formatter_node)

    # リニアパイプライン
    graph.set_entry_point("document_parse")
    graph.add_edge("document_parse", "structure_extraction")
    graph.add_edge("structure_extraction", "concept_graph_builder")
    graph.add_edge("concept_graph_builder", "diff_review_formatter")
    graph.add_edge("diff_review_formatter", END)

    return graph.compile()
