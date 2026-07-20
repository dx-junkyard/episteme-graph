"""apparatus_semantics ステージへの figure_context 配線の検証（装置図理解機能の拡張, Phase 1 D）。

設計: /Users/Shared/issues/apparatus_diagram_understanding_design.md §4-4
（G1: orchestrator._build_apparatus_semantics が ``nearby_text=[]`` を
ハードコードしているギャップの解消）。

観点:
  - 構造テスト: ``_build_apparatus_semantics`` のソースが ``collect_figure_context``
    を呼び、``nearby_text=[]`` の固定リテラルを持たず、新設定2フィールドを参照する。
  - 設定テスト: ``Settings`` に既定値 12 / 6000 の2フィールドが存在する
    （env 変数は読まず、フィールド定義のみを検査する）。
  - 機能テスト: 偽の依存関数群で ``_build_apparatus_semantics`` を駆動し、
    agent に渡る ``FigureImageInput`` に ``nearby_text`` / ``inner_labels`` /
    ``abbreviations`` が実際に配線されていること、cartridge 指定時にライブラリ
    retrieval クエリへ略語展開名が含まれることを確認する。
  - 例外縮退テスト: ``collect_figure_context`` が例外を投げてもステージは
    失敗せず、当該図は ``nearby_text=[]`` / ``abbreviations={}`` に縮退する。

DB / MinIO / LLM への実接続は行わない。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SRC = ROOT / "src"
for _p in (str(BACKEND), str(BACKEND / "api"), str(SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import core.config as config_module  # noqa: E402
import core.document_pipeline.figure_context as figure_context_module  # noqa: E402
import core.document_pipeline.figure_images as figure_images_module  # noqa: E402
import core.document_pipeline.orchestrator as orch  # noqa: E402
import core.library.search as library_search_module  # noqa: E402
import core.storage as storage_module  # noqa: E402
import episteme_graph.agents.apparatus_semantics.agent as apparatus_agent_module  # noqa: E402

_SOURCE = inspect.getsource(orch._build_apparatus_semantics)


# ---------------------------------------------------------------------------
# 構造テスト
# ---------------------------------------------------------------------------


class TestStructuralWiring:
    def test_calls_collect_figure_context(self):
        assert "collect_figure_context" in _SOURCE

    def test_nearby_text_is_not_hardcoded_empty(self):
        assert "nearby_text=[]" not in _SOURCE

    def test_references_context_max_items_setting(self):
        assert "apparatus_context_max_items" in _SOURCE

    def test_references_context_max_chars_setting(self):
        assert "apparatus_context_max_chars" in _SOURCE


# ---------------------------------------------------------------------------
# 設定テスト
# ---------------------------------------------------------------------------


class TestSettingsFields:
    def test_context_max_items_default(self):
        assert config_module.Settings.model_fields["apparatus_context_max_items"].default == 12

    def test_context_max_chars_default(self):
        assert config_module.Settings.model_fields["apparatus_context_max_chars"].default == 6000


# ---------------------------------------------------------------------------
# テスト用の合成 structure / figure row
# ---------------------------------------------------------------------------


def _block(text: str, *, page: int, order: int, block_id: str, block_type: str = "paragraph", section_id: str = "s1") -> dict:
    return {
        "block_id": block_id,
        "page": page,
        "order": order,
        "text": text,
        "block_type": block_type,
        "bbox": None,
        "section_id": section_id,
    }


def _structure() -> dict:
    return {
        "blocks": [
            _block("Figure 1: ECDL setup.", page=1, order=0, block_id="cap", block_type="figure_caption"),
            _block(
                "external cavity diode laser (ECDL) provides the tunable source.",
                page=1, order=1, block_id="b1",
            ),
        ]
    }


def _figure_row() -> dict:
    return {
        "id": "row1",
        "figure_key": "fig_1",
        "figure_label": "Figure 1",
        "caption_text": "Figure 1: ECDL setup.",
        "caption_block_id": "cap",
        "status": "extracted",
        "minio_key": "figures/fig1.png",
        "inner_labels": [{"text": "ECDL", "bbox": [1.0, 2.0, 3.0, 4.0]}],
    }


def _fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        apparatus_max_images_per_document=20,
        apparatus_context_max_items=12,
        apparatus_context_max_chars=6000,
        apparatus_retrieval_top_k=5,
        apparatus_max_calls_per_day=30,
    )


class _FakeStorageClient:
    def get_object(self, bucket: str, key: str) -> bytes:
        return b"\x89PNG\r\n\x1a\nrestofpngbytes"


class _FakeApparatusSemanticsAgent:
    """``ApparatusSemanticsAgent`` の偽物。``run`` の kwargs を ``captured`` に記録する。"""

    def __init__(
        self, captured: dict, cartridge_id=None, llm_client=None, cartridge_loader=None,
        iterative_config=None,
    ):
        self._captured = captured
        captured["init_cartridge_id"] = cartridge_id
        captured["init_iterative_config"] = iterative_config

    def run(self, *, document_id, figures, library_candidates=None, cartridge_id=None):
        self._captured["run_kwargs"] = {
            "document_id": document_id,
            "figures": figures,
            "library_candidates": library_candidates,
            "cartridge_id": cartridge_id,
        }
        return SimpleNamespace(apparatus_records=[])


def _make_agent_factory(captured: dict):
    def _factory(cartridge_id=None, llm_client=None, cartridge_loader=None, iterative_config=None):
        return _FakeApparatusSemanticsAgent(
            captured, cartridge_id=cartridge_id, llm_client=llm_client, cartridge_loader=cartridge_loader,
            iterative_config=iterative_config,
        )
    return _factory


# ---------------------------------------------------------------------------
# 機能テスト（monkeypatch で全依存を差し替える）
# ---------------------------------------------------------------------------


class TestFunctionalWiring:
    def _patch_common(self, monkeypatch, captured: dict, search_queries: list):
        monkeypatch.setattr(
            figure_images_module, "load_document_figures", lambda document_id: [_figure_row()],
        )
        monkeypatch.setattr(config_module, "get_settings", _fake_settings)
        monkeypatch.setattr(storage_module, "get_storage_client", lambda: _FakeStorageClient())
        monkeypatch.setattr(orch, "_apparatus_daily_remaining", lambda settings: 10)
        monkeypatch.setattr(
            apparatus_agent_module, "ApparatusSemanticsAgent", _make_agent_factory(captured),
        )

        def _fake_search_frozen_entries(*, domain_key, query_text, top_k):
            search_queries.append(query_text)
            return []

        monkeypatch.setattr(library_search_module, "search_frozen_entries", _fake_search_frozen_entries)

    def test_nearby_text_inner_labels_and_abbreviations_are_wired(self, monkeypatch):
        captured: dict = {}
        search_queries: list = []
        self._patch_common(monkeypatch, captured, search_queries)

        result, done_payload = orch._build_apparatus_semantics(
            document_id="doc1",
            cartridge_id="particle_physics",
            fig_tbl=None,
            structure=_structure(),
        )

        figures = captured["run_kwargs"]["figures"]
        assert len(figures) == 1
        fig_input = figures[0]

        assert fig_input.nearby_text == [
            "external cavity diode laser (ECDL) provides the tunable source.",
        ]
        assert fig_input.inner_labels == [{"text": "ECDL", "bbox": [1.0, 2.0, 3.0, 4.0]}]
        assert fig_input.abbreviations == {"ECDL": "external cavity diode laser"}

        assert done_payload["context_collected"] == 1
        assert getattr(result, "apparatus_records", None) == []

    def test_retrieval_query_includes_expanded_abbreviation(self, monkeypatch):
        captured: dict = {}
        search_queries: list = []
        self._patch_common(monkeypatch, captured, search_queries)

        orch._build_apparatus_semantics(
            document_id="doc1",
            cartridge_id="particle_physics",
            fig_tbl=None,
            structure=_structure(),
        )

        assert len(search_queries) == 1
        assert "external cavity diode laser" in search_queries[0]

    def test_no_cartridge_id_skips_retrieval_entirely(self, monkeypatch):
        captured: dict = {}
        search_queries: list = []
        self._patch_common(monkeypatch, captured, search_queries)

        orch._build_apparatus_semantics(
            document_id="doc1",
            cartridge_id=None,
            fig_tbl=None,
            structure=_structure(),
        )

        assert search_queries == []


# ---------------------------------------------------------------------------
# 例外縮退テスト
# ---------------------------------------------------------------------------


class TestContextCollectionFailureDegradesGracefully:
    def test_collect_figure_context_exception_degrades_to_empty_context(self, monkeypatch):
        captured: dict = {}
        search_queries: list = []

        monkeypatch.setattr(
            figure_images_module, "load_document_figures", lambda document_id: [_figure_row()],
        )
        monkeypatch.setattr(config_module, "get_settings", _fake_settings)
        monkeypatch.setattr(storage_module, "get_storage_client", lambda: _FakeStorageClient())
        monkeypatch.setattr(orch, "_apparatus_daily_remaining", lambda settings: 10)
        monkeypatch.setattr(
            apparatus_agent_module, "ApparatusSemanticsAgent", _make_agent_factory(captured),
        )

        def _fake_search_frozen_entries(*, domain_key, query_text, top_k):
            search_queries.append(query_text)
            return []

        monkeypatch.setattr(library_search_module, "search_frozen_entries", _fake_search_frozen_entries)

        def _boom(structure, figure_row, *, inner_labels=None, max_items=12, max_chars=6000):
            raise RuntimeError("boom")

        monkeypatch.setattr(figure_context_module, "collect_figure_context", _boom)

        # ステージ本体は例外を伝播させない（非致命, P4）。
        result, done_payload = orch._build_apparatus_semantics(
            document_id="doc1",
            cartridge_id="particle_physics",
            fig_tbl=None,
            structure=_structure(),
        )

        figures = captured["run_kwargs"]["figures"]
        assert len(figures) == 1
        fig_input = figures[0]
        assert fig_input.nearby_text == []
        assert fig_input.abbreviations == {}
        # inner_labels は figure_context に依存しない別経路（row 由来）なので保持される。
        assert fig_input.inner_labels == [{"text": "ECDL", "bbox": [1.0, 2.0, 3.0, 4.0]}]
        assert done_payload["context_collected"] == 0
        assert getattr(result, "apparatus_records", None) == []
