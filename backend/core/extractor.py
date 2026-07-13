"""PDF テキスト抽出（GROBID）ユーティリティ。

現行のドキュメント解析パイプラインは
``core/document_pipeline/orchestrator.py`` + ``src/episteme_graph/agents/``
であり、本モジュールが提供するのはその下請けとなる GROBID 変換
（PDF → TEI XML）のみ。

かつて存在した仮説駆動型の逐次 LLM 構造抽出パイプライン
（``extract_paper_structure`` とその内部ステップ）と、未使用の
テキストフォールバック群（``parse_tei_to_logical_chunks`` /
``extract_text_from_pdf_bytes`` / ``chunk_text``）は本番呼び出し元が
存在しなかったため削除済み（2026-07 整理）。同じく本番未使用だった
PaperStructure の diff/merge ユーティリティ
（``compute_structure_diff`` / ``evaluate_and_merge_proposals``）も削除済み
（2026-07 整理 続き）。
"""

from __future__ import annotations

import requests

from core.config import get_settings as get_app_settings


# ---------------------------------------------------------------------------
# Public: GROBID API を使った PDF → TEI XML 変換
# ---------------------------------------------------------------------------

def extract_tei_xml_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """PDF バイナリを GROBID の processFulltextDocument API に送信し TEI XML を返す。"""
    grobid_url = get_app_settings().grobid_url
    url = f"{grobid_url}/api/processFulltextDocument"
    resp = requests.post(
        url,
        files={"input": ("paper.pdf", pdf_bytes, "application/pdf")},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.text
