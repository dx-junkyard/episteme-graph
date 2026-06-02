"""Issue #302: TheoryOperationGraph の source-backed / review_required 対応の回帰チェック。

backend route / Pydantic schema / frontend が、node・edge の source-backing リンクと
review 理由を伝搬・表示することを構造的に検証する。重い依存 (fastapi 等) を import せず、
ソーステキストに対するアサーションで確認する (既存 graph テストの方針に合わせる)。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ROOT / "backend" / "api" / "routes" / "theory_components.py"
SCHEMAS = ROOT / "backend" / "api" / "schemas.py"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
STYLES = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestGraphSchemaExposesSourceBacking:
    def test_node_schema_has_source_links(self):
        source = _read(SCHEMAS)
        for field in (
            "linked_equation_ids",
            "linked_derivation_ids",
            "linked_claim_ids",
            "linked_evidence_ids",
            "source_backing_status",
            "review_reasons",
        ):
            assert field in source, f"ComponentGraphNode schema missing {field}"

    def test_edge_schema_has_review_reasons(self):
        source = _read(SCHEMAS)
        assert "review_reasons: list[str]" in source


class TestRoutePropagatesSourceBacking:
    def test_normalize_passes_through_node_source_links(self):
        source = _read(ROUTES)
        for field in (
            '"linked_equation_ids"',
            '"linked_derivation_ids"',
            '"linked_claim_ids"',
            '"linked_evidence_ids"',
            '"source_backing_status"',
            '"review_reasons"',
        ):
            assert field in source, f"route does not propagate node field {field}"

    def test_graph_relations_include_operation_edge_types(self):
        source = _read(ROUTES)
        for relation in ("solves", "substitutes", "eliminates", "compares", "constructs"):
            assert f'"{relation}"' in source, f"_GRAPH_RELATIONS missing {relation}"


class TestFrontendDistinguishesSourceBacking:
    def test_admin_js_renders_source_backing(self):
        source = _read(ADMIN_JS)
        assert "source_backing_status" in source
        assert "lsGraphSourceBackingLabel" in source
        assert "lsGraphReviewReasonLabel" in source
        # review_required nodes are drawn with a dashed border.
        assert "review_required" in source

    def test_styles_define_backing_states(self):
        source = _read(STYLES)
        for cls in (
            ".ls-graph-backing-source_backed",
            ".ls-graph-backing-partially_source_backed",
            ".ls-graph-backing-inferred",
            ".ls-graph-backing-review_required",
        ):
            assert cls in source, f"styles.css missing {cls}"
