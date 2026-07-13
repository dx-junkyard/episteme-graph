"""Issue #319: stored TheoryOperationGraph の正規化と source-backing 伝搬の回帰チェック。

- stored graph API 経路 (`_normalize_stored_component_graph`) が、main TheoryOperationNode の
  長い label を短い stage 名へ正規化し、長文を description に退避すること。
- stored graph edge に source_backing_status が必ず付与され、欠落時は後方互換で推定されること。
- review_required / inferred の node / edge に review_reasons が非空であること。

重い依存 (fastapi / sqlalchemy) を import せずに対象関数だけを exec で隔離して検証する
(既存 route テストの方針に合わせる)。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ROOT / "backend" / "api" / "routes" / "theory_components.py"
# 原稿スタジオ (Lecture Script Studio) は Tier 3-17b で admin.js から分離済み。
# lsGraphMainStageLabel / lsGraphSemanticLabel / lsGraphStripInternalIds はこちらに存在する。
ADMIN_LS_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"
SCHEMAS = ROOT / "backend" / "api" / "schemas.py"
PERSISTENCE = ROOT / "backend" / "core" / "document_pipeline" / "persistence.py"

_SRC = str(ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from episteme_graph.agents.component_graph.schema import split_main_label  # noqa: E402


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_normalize_stored_component_graph():
    """Exec-isolate `_normalize_stored_component_graph` with the real shared helper."""
    source = _read(ROUTES)
    match = re.search(
        r"(^def _normalize_stored_component_graph\(.*?)(?=^def )",
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert match, "_normalize_stored_component_graph not found"
    code = "from __future__ import annotations\n" + match.group(1)
    ns = {
        "__builtins__": __builtins__,
        "split_main_label": split_main_label,
        # Minimal relation vocabulary sufficient for the tested edges.
        "_GRAPH_RELATIONS": {
            "RELATED_TO", "REQUIRES", "ENABLES", "defines", "derives",
            "constrains", "solves", "eliminates",
        },
    }
    exec(code, ns)  # noqa: S102
    return ns["_normalize_stored_component_graph"]


class TestSplitMainLabel:
    """Shared deterministic helper (issue #319 Implementation Notes)."""

    def test_splits_stage_prefixed_long_label(self):
        label, desc = split_main_label(
            "Theory basis: Eq. (2.7) is most likely an explicit definition/identity."
        )
        assert label == "Theory basis"
        assert desc == "Eq. (2.7) is most likely an explicit definition/identity."

    def test_bare_stage_label_keeps_empty_description(self):
        assert split_main_label("Equation system") == ("Equation system", "")

    def test_keyword_inference_keeps_full_text_as_description(self):
        label, desc = split_main_label("Define eq_2_7 normalization identity")
        assert label == "Theory basis"
        assert desc == "Define eq_2_7 normalization identity"

    def test_unmappable_label_unchanged(self):
        assert split_main_label("Completely unrelated label") == (
            "Completely unrelated label",
            "",
        )


class TestStoredGraphMainLabelNormalization:
    """Acceptance criteria 1–3, 10."""

    def test_long_main_label_shortened_and_preserved_in_description(self):
        normalize = _load_normalize_stored_component_graph()
        graph = {
            "graph_id": "g1",
            "document_id": "doc1",
            "scope": {"level": "paper"},
            "nodes": [
                {
                    "component_id": "c1",
                    "label": "Theory basis: Eq. (2.7) is most likely an explicit definition.",
                    "component_type": "TheoryOperationNode",
                    "graph_layer": "main",
                    "source_backing_status": "partially_source_backed",
                }
            ],
            "edges": [],
        }
        out = normalize("doc1", graph, [])
        node = out["nodes"][0]
        assert node["label"] == "Theory basis"
        assert node["description"] == "Eq. (2.7) is most likely an explicit definition."
        # criterion 2: never leak equation ids / Eq. / sentences into the label.
        assert "Eq." not in node["label"]
        assert node["label"] in {
            "Theory basis", "Observation model", "Observable construction",
            "Equation system", "Elimination", "Consistency relation",
            "Diagnostic / application",
        }

    def test_equation_detail_label_is_not_rewritten(self):
        normalize = _load_normalize_stored_component_graph()
        graph = {
            "document_id": "doc1",
            "nodes": [
                {
                    "component_id": "c2",
                    "label": "Define eq_2_7",
                    "component_type": "EquationOperationNode",
                    "graph_layer": "equation_detail",
                }
            ],
            "edges": [],
        }
        out = normalize("doc1", graph, [])
        # Detail-layer step labels keep their operation-specific text.
        assert out["nodes"][0]["label"] == "Define eq_2_7"

    def test_existing_description_is_preferred_over_label_remainder(self):
        normalize = _load_normalize_stored_component_graph()
        graph = {
            "document_id": "doc1",
            "nodes": [
                {
                    "component_id": "c1",
                    "label": "Elimination: reconstructed formula",
                    "description": "Existing description text",
                    "component_type": "TheoryOperationNode",
                    "graph_layer": "main",
                }
            ],
            "edges": [],
        }
        out = normalize("doc1", graph, [])
        assert out["nodes"][0]["label"] == "Elimination"
        assert out["nodes"][0]["description"] == "Existing description text"


class TestStoredGraphEdgeSourceBacking:
    """Acceptance criteria 7–9, 11."""

    def test_existing_edge_source_backing_is_preserved(self):
        normalize = _load_normalize_stored_component_graph()
        graph = {
            "document_id": "doc1",
            "nodes": [],
            "edges": [
                {
                    "source_component_id": "c1",
                    "target_component_id": "c2",
                    "relation": "defines",
                    "review_status": "source_backed",
                    "source_backing_status": "source_backed",
                }
            ],
        }
        out = normalize("doc1", graph, [])
        assert out["edges"][0]["source_backing_status"] == "source_backed"

    def test_missing_edge_source_backing_inferred_from_review_status(self):
        normalize = _load_normalize_stored_component_graph()
        graph = {
            "document_id": "doc1",
            "nodes": [],
            "edges": [
                {
                    "source_component_id": "c1",
                    "target_component_id": "c2",
                    "relation": "derives",
                    "review_status": "review_required",
                }
            ],
        }
        out = normalize("doc1", graph, [])
        edge = out["edges"][0]
        assert edge["source_backing_status"] == "review_required"
        # criterion 9: review_required edges always explain why.
        assert edge["review_reasons"] == ["edge_not_source_backed"]

    def test_missing_edge_source_backing_inferred_from_support_status(self):
        normalize = _load_normalize_stored_component_graph()
        graph = {
            "document_id": "doc1",
            "nodes": [],
            "edges": [
                {
                    "source_component_id": "c1",
                    "target_component_id": "c2",
                    "relation": "RELATED_TO",
                    "review_status": "",
                    "support_status": "design_inferred",
                }
            ],
        }
        out = normalize("doc1", graph, [])
        assert out["edges"][0]["source_backing_status"] == "inferred"

    def test_review_required_node_gets_reason(self):
        normalize = _load_normalize_stored_component_graph()
        graph = {
            "document_id": "doc1",
            "nodes": [
                {
                    "component_id": "c1",
                    "label": "Equation system",
                    "component_type": "TheoryOperationNode",
                    "graph_layer": "main",
                    "source_backing_status": "review_required",
                }
            ],
            "edges": [],
        }
        out = normalize("doc1", graph, [])
        assert out["nodes"][0]["review_reasons"], "review_required node must have reasons"


class TestSchemaAndPersistencePlumbing:
    def test_component_graph_node_schema_has_description(self):
        source = _read(SCHEMAS)
        start = source.index("class ComponentGraphNode")
        end = source.index("class ComponentGraphEdge", start)
        body = source[start:end]
        assert "description: str" in body, "ComponentGraphNode must expose description (issue #319)"

    def test_persistence_edge_includes_source_backing_status(self):
        source = _read(PERSISTENCE)
        start = source.index("def persist_component_graph")
        end = source.index("\ndef ", start + 1)
        body = source[start:end]
        assert '"source_backing_status"' in body, (
            "persist_component_graph must store edge source_backing_status (issue #319)"
        )

    def test_persistence_normalizes_payload_before_save(self):
        source = _read(PERSISTENCE)
        assert "_normalize_graph_payload_for_persist" in source


class TestAdminJsMainLabelGuard:
    def test_admin_js_has_main_stage_label_guard(self):
        source = _read(ADMIN_LS_JS)
        assert "lsGraphMainStageLabel" in source
        # Issue #447: the visual node label, detail heading and text fallback must
        # route through lsGraphSemanticLabel, which strips internal identifiers and
        # falls back to a stage/operation label rather than leaking a raw ID/name.
        assert "lsGraphSemanticLabel" in source
        assert "lsGraphStripInternalIds" in source
        # The no-vis text fallback must not fall back to the raw node.label.
        assert "lsGraphMainStageLabel(node) || node.label" not in source
