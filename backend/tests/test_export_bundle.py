"""Tests for Issue #208: DSL / Claim / Component / Graph ZIP export bundle."""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

EXPORT_ROUTE = ROOT / "backend" / "api" / "routes" / "export.py"
MAIN_PY = ROOT / "backend" / "api" / "main.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers — import export utilities without running FastAPI
# ---------------------------------------------------------------------------

def _import_export_module():
    """Export モジュールをロードして純粋関数ヘルパーを返す。

    CI では全依存関係がインストール済みのため直接インポートを試みる。
    未インストール環境ではスタブを使い、sys.modules を汚染しないよう必ずリストアする。
    """
    import importlib
    import importlib.util
    import types

    spec = importlib.util.spec_from_file_location("export_route_for_test", str(EXPORT_ROUTE))
    mod = importlib.util.module_from_spec(spec)

    # sys.modules のスナップショットを保存 — ロード後に復元してグローバル汚染を防ぐ
    snapshot: dict[str, object] = {}

    def _ensure_stub(name: str) -> types.ModuleType:
        """name が sys.modules に無ければスタブを作成し、元の状態を記録する。"""
        if name not in sys.modules:
            snapshot.setdefault(name, None)  # None = 元々存在しなかった
            m = types.ModuleType(name)
            sys.modules[name] = m
        else:
            snapshot.setdefault(name, sys.modules[name])  # 元のモジュールを保存
        return sys.modules[name]

    # --- 依存モジュールを準備（インストール済みなら実物、未インストールならスタブ）---
    for dep in ("fastapi", "fastapi.responses", "sqlalchemy", "sqlalchemy.dialects",
                "dependencies", "core", "core.postgres"):
        parts = dep.split(".")
        for i in range(len(parts)):
            full = ".".join(parts[:i + 1])
            m = _ensure_stub(full)
            if i > 0:
                parent = sys.modules[".".join(parts[:i])]
                if not hasattr(parent, parts[i]):
                    setattr(parent, parts[i], m)

    fa = sys.modules["fastapi"]
    if not callable(getattr(fa, "APIRouter", None)):
        fa.APIRouter = lambda **kw: type("Router", (), {
            "post": lambda s, *a, **kw: (lambda f: f),
            "get": lambda s, *a, **kw: (lambda f: f),
        })()
        fa.Depends = lambda f: None
        fa.HTTPException = Exception

    fr = sys.modules["fastapi.responses"]
    if not hasattr(fr, "StreamingResponse"):
        fr.StreamingResponse = lambda *a, **kw: None

    sa = sys.modules["sqlalchemy"]
    if not hasattr(sa, "text"):
        sa.text = lambda s: s

    try:
        import pydantic as _pyd  # noqa: F401
    except ImportError:
        _pyd_mod = _ensure_stub("pydantic")
        class _BM:
            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)
        _pyd_mod.BaseModel = _BM
        _pyd_mod.Field = lambda *a, **kw: None

    deps = sys.modules["dependencies"]
    if not hasattr(deps, "_require_teacher"):
        deps._require_teacher = lambda: None

    pg = sys.modules["core.postgres"]
    if not hasattr(pg, "get_session"):
        pg.get_session = lambda: None

    try:
        spec.loader.exec_module(mod)
    finally:
        # sys.modules を元の状態に復元（スタブが後続テストを壊さないように）
        for name, original in snapshot.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    return mod


def _get_export_helpers():
    """Return the pure-function helpers from the export module."""
    mod = _import_export_module()
    return mod


# ---------------------------------------------------------------------------
# Unit: manifest generation
# ---------------------------------------------------------------------------

class TestExportManifest:
    def test_export_bundle_generates_manifest(self):
        mod = _get_export_helpers()
        claims = [{"claim_id": "c1", "text": "t1", "evidence_text": "e1"}]
        dsl = {"nodes": [{"node_id": "n1"}], "edges": [{"edge_id": "e1"}]}
        comps = [{"component_id": "comp1"}]
        graph = {"nodes": [{"node_id": "comp1"}], "edges": [{"edge_id": "eg1"}]}
        snippets = [{"evidence_id": "ev1"}]

        manifest = mod._build_manifest(
            export_id="export_test_001",
            scope_type="course",
            scope_id="course_abc",
            material_ids=[],
            document_ids=["doc_xyz"],
            claims=claims,
            dsl_graph=dsl,
            components=comps,
            component_graph=graph,
            evidence_snippets=snippets,
            options={"include_source_snippets": True, "include_debug_data": False},
        )

        assert "export_schema_version" in manifest
        assert manifest["export_schema_version"] == "0.2.0"
        assert "exported_at" in manifest
        assert "scope" in manifest
        assert manifest["scope"]["type"] == "course"
        assert "files" in manifest
        assert "counts" in manifest
        assert manifest["counts"]["claims"] == 1
        assert manifest["counts"]["dsl_nodes"] == 1
        assert manifest["counts"]["dsl_edges"] == 1
        assert manifest["counts"]["components"] == 1
        assert manifest["counts"]["component_edges"] == 1


# ---------------------------------------------------------------------------
# Unit: ZIP contains required files
# ---------------------------------------------------------------------------

class TestExportZipContents:
    def _make_zip(self, include_ndjson=False, include_debug=False):
        mod = _get_export_helpers()
        claims = [{"claim_id": "c1", "text": "test", "evidence_text": "src", "source_scope": {}}]
        dsl = {"dsl_schema_version": "0.1.0", "nodes": [], "edges": []}
        comps = [{"component_id": "comp1", "name": "Comp"}]
        graph = {"graph_schema_version": "0.1.0", "nodes": [], "edges": []}
        snippets = [{"evidence_id": "ev1", "claim_id": "c1", "text": "src"}]
        manifest = mod._build_manifest(
            export_id="x",
            scope_type="course",
            scope_id="c",
            material_ids=[],
            document_ids=[],
            claims=claims,
            dsl_graph=dsl,
            components=comps,
            component_graph=graph,
            evidence_snippets=snippets,
            options={},
        )
        debug_data = {"validation_errors": [], "pipeline_logs": [], "llm_prompts": [], "llm_raw_outputs": []}
        return mod._build_zip(
            manifest=manifest,
            claims=claims,
            dsl_graph=dsl,
            components=comps,
            component_graph=graph,
            evidence_snippets=snippets,
            include_ndjson=include_ndjson,
            include_debug=include_debug,
            debug_data=debug_data if include_debug else None,
        )

    def test_export_bundle_zip_contains_required_files(self):
        zip_bytes = self._make_zip()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
        assert "README.md" in names
        assert "manifest.json" in names
        assert "export_validation.json" in names
        assert "claims/claims.json" in names
        assert "dsl/dsl_graph.json" in names
        assert "components/components.json" in names
        assert "graph/component_graph.json" in names
        assert "evidence/evidence_snippets.json" in names

    def test_export_bundle_json_files_are_valid_json(self):
        zip_bytes = self._make_zip()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if name.endswith(".json"):
                    data = zf.read(name).decode("utf-8")
                    parsed = json.loads(data)
                    assert parsed is not None, f"{name} failed to parse"

    def test_export_bundle_readme_explains_file_formats(self):
        zip_bytes = self._make_zip()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            readme = zf.read("README.md").decode("utf-8")
        assert "Claim" in readme
        assert "DSL" in readme
        assert "Component" in readme
        assert "Component Graph" in readme
        assert "Evidence" in readme

    def test_export_bundle_optionally_includes_ndjson(self):
        zip_bytes = self._make_zip(include_ndjson=True)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            assert "claims/claims.ndjson" in names
            assert "components/components.ndjson" in names
            # verify each line is valid JSON
            for fname in ("claims/claims.ndjson", "components/components.ndjson"):
                content = zf.read(fname).decode("utf-8").strip()
                for line in content.splitlines():
                    json.loads(line)

    def test_export_bundle_excludes_debug_data_by_default(self):
        zip_bytes = self._make_zip(include_debug=False)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
        assert "debug/llm_prompts.json" not in names
        assert "debug/llm_raw_outputs.json" not in names
        assert "debug/pipeline_logs.json" not in names

    def test_export_bundle_can_include_debug_data(self):
        zip_bytes = self._make_zip(include_debug=True)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
        assert "debug/validation_errors.json" in names
        assert "debug/pipeline_logs.json" in names


# ---------------------------------------------------------------------------
# Unit: claim / component field validation
# ---------------------------------------------------------------------------

class TestExportClaimsAndComponents:
    def test_exported_claims_include_source_scope_and_evidence(self):
        mod = _get_export_helpers()
        # Simulate _rows_to_claims output
        raw_rows = [
            (
                "uuid-claim-001", "doc_001",
                json.dumps({"level": "chunk", "section_id": "sec_1", "chunk_id": "chunk_01"}),
                "relation",
                "The law relates X to Y.",
                "X relates to Y via law.",
                json.dumps([{"name": "X", "concept_type": "Observable"}]),
                json.dumps({}),
                "source_backed",
                "short quote from text",
                "teacher_review_required",
                None,
            )
        ]
        claims = mod._rows_to_claims(raw_rows)
        assert len(claims) == 1
        c = claims[0]
        assert "claim_id" in c
        assert "claim_type" in c
        assert "text" in c
        assert "source_scope" in c
        assert "evidence_text" in c
        assert "support_status" in c
        assert "review_status" in c

    def test_exported_components_include_evidence_claims(self):
        mod = _get_export_helpers()
        raw_rows = [
            (
                "uuid-comp-001", "course_001",
                "Total Decay Rate Sum Rule Component",
                "theory", "PaperRelationComponent",
                "Summary text",
                "candidate",
                json.dumps({"level": "section", "document_id": "doc_001", "section_id": "sec_3_1"}),
                json.dumps(["claim_001", "claim_002"]),
                "paper_claim",
                "llm_proposed",
                "teacher_review_required",
                json.dumps([{"label": "input A", "evidence_claims": ["claim_001"]}]),
                json.dumps([{"label": "output B", "evidence_claims": ["claim_002"]}]),
                json.dumps([{"label": "heavy quark limit", "evidence_claims": ["claim_003"]}]),
                json.dumps([]),
                json.dumps([]),
                json.dumps([]),
                json.dumps([]),
                json.dumps({"requires_before_use": ["domain_hqet"]}),
                json.dumps([{"from": "input A", "relation": "TRANSFORMS_TO", "to": "output B"}]),
                "",
                None,
            )
        ]
        components = mod._rows_to_components(raw_rows)
        assert len(components) == 1
        c = components[0]
        assert "component_id" in c
        assert "name" in c
        assert "component_type" in c
        assert "source_scope" in c
        assert "evidence_claims" in c
        assert isinstance(c["evidence_claims"], list)
        assert "review_status" in c
        assert len(c["inputs"]) == 1
        assert "evidence_claims" in c["inputs"][0]

    def test_exported_component_graph_edges_include_source_target_and_evidence(self):
        mod = _get_export_helpers()
        raw_row = (
            "uuid-graph-001",
            "doc_001",
            json.dumps({"level": "paper"}),
            json.dumps({
                "nodes": [
                    {"component_id": "comp_hqet", "label": "HQET", "component_type": "PaperRelationComponent", "review_status": "teacher_review_required"},
                    {"component_id": "comp_sum_rule", "label": "Sum Rule", "component_type": "PaperRelationComponent", "review_status": "teacher_review_required"},
                ],
                "edges": [
                    {
                        "edge_id": "component_edge_001",
                        "source_component_id": "comp_hqet",
                        "target_component_id": "comp_sum_rule",
                        "relation": "REQUIRES",
                        "support_status": "source_inferred",
                        "evidence": {"evidence_claims": ["claim_003"]},
                        "review_status": "teacher_review_required",
                    }
                ],
            }),
        )
        graph = mod._row_to_component_graph(raw_row)
        assert "nodes" in graph
        assert "edges" in graph
        assert len(graph["edges"]) == 1
        e = graph["edges"][0]
        assert "edge_id" in e
        assert "source" in e
        assert "target" in e
        assert "edge_type" in e
        assert "evidence_claims" in e
        assert "review_status" in e

    def test_row_to_component_graph_backward_compat_from_to_keys(self):
        """Issue #266: _row_to_component_graph must read edges saved with from/to/type keys."""
        mod = _get_export_helpers()
        raw_row = (
            "uuid-graph-002",
            "doc_002",
            json.dumps({"level": "paper"}),
            json.dumps({
                "nodes": [
                    {"id": "db-uuid-001", "agent_component_id": "comp_A", "type": "component"},
                    {"id": "db-uuid-002", "agent_component_id": "comp_B", "type": "component"},
                ],
                "edges": [
                    {
                        "from": "db-uuid-001",
                        "to": "db-uuid-002",
                        "type": "depends_on",
                        "reason": "B depends on A",
                    }
                ],
            }),
        )
        graph = mod._row_to_component_graph(raw_row)
        assert len(graph["edges"]) == 1
        e = graph["edges"][0]
        assert e["source"] == "db-uuid-001", f"source should be 'db-uuid-001', got '{e['source']}'"
        assert e["target"] == "db-uuid-002", f"target should be 'db-uuid-002', got '{e['target']}'"
        assert e["edge_type"] == "depends_on"

    def test_build_evidence_snippets_from_claims(self):
        mod = _get_export_helpers()
        claims = [
            {
                "claim_id": "c1",
                "document_id": "doc_001",
                "source_scope": {"section_id": "sec_1", "chunk_id": "chunk_01", "page": 3},
                "evidence_text": "short quote from text",
            },
            {
                "claim_id": "c2",
                "document_id": "doc_001",
                "source_scope": {},
                "evidence_text": "",
            },
        ]
        snippets = mod._build_evidence_snippets(claims)
        assert len(snippets) == 1
        s = snippets[0]
        assert s["claim_id"] == "c1"
        assert "evidence_id" in s
        assert s["text"] == "short quote from text"
        assert s["page"] == 3


# ---------------------------------------------------------------------------
# Unit: reference integrity (integration-style without DB)
# ---------------------------------------------------------------------------

class TestExportReferenceIntegrity:
    def test_export_reference_normalization_rewrites_legacy_ids_before_validation(self):
        mod = _get_export_helpers()

        claims = [
            {
                "claim_id": "11111111-1111-1111-1111-111111111111",
                "source_scope": {"block_id": "b1", "span_id": "span_001"},
            }
        ]
        equations = [{"equation_id": "eq1", "linked_claim_ids": ["claim_span_001"]}]
        components = [
            {
                "component_id": "22222222-2222-2222-2222-222222222222",
                "source_scope": {"legacy_ids": ["comp_001"]},
                "evidence_claims": ["claim_span_001"],
                "dependencies": [{"component_refs": ["comp_001"]}],
            }
        ]
        component_graph = {
            "nodes": [
                {
                    "node_id": "22222222-2222-2222-2222-222222222222",
                    "legacy_ids": ["comp_001"],
                }
            ],
            "edges": [
                {
                    "source": "comp_001",
                    "target": "22222222-2222-2222-2222-222222222222",
                    "evidence_claims": ["claim_span_001"],
                }
            ],
        }
        course_info = {
            "topics": [
                {
                    "linked_component_ids": ["comp_001"],
                    "visualization_plan": [{"linked_component_ids": ["comp_001"]}],
                }
            ]
        }

        mod._normalize_export_references(
            claims=claims,
            equations=equations,
            components=components,
            component_graph=component_graph,
            course_info=course_info,
        )
        validation = mod._validate_export_references(
            claims=claims,
            equations=equations,
            components=components,
            component_graph=component_graph,
            course_info=course_info,
            evidence_snippets=[],
        )

        claim_id = claims[0]["claim_id"]
        component_id = components[0]["component_id"]
        assert equations[0]["linked_claim_ids"] == [claim_id]
        assert components[0]["evidence_claims"] == [claim_id]
        assert components[0]["dependencies"][0]["component_refs"] == [component_id]
        assert component_graph["edges"][0]["source"] == component_id
        assert component_graph["edges"][0]["evidence_claims"] == [claim_id]
        assert course_info["topics"][0]["linked_component_ids"] == [component_id]
        assert course_info["topics"][0]["visualization_plan"][0]["linked_component_ids"] == [component_id]
        assert validation["publish_ready"] is True
        assert validation["summary"]["unresolved_reference_count"] == 0

    def test_export_reference_validation_blocks_unresolved_or_legacy_refs(self):
        mod = _get_export_helpers()
        validation = mod._validate_export_references(
            claims=[{"claim_id": "claim-real"}],
            equations=[{"equation_id": "eq1", "linked_claim_ids": ["claim_span_001"]}],
            components=[{"component_id": "component-real", "evidence_claims": ["claim-missing"], "dependencies": [{"component_refs": ["comp_001"]}]}],
            component_graph={"nodes": [{"node_id": "component-real"}], "edges": [{"source": "comp_001", "target": "component-real", "evidence_claims": ["claim-missing"]}]},
            course_info={"topics": [{"linked_component_ids": ["comp_001"], "visualization_plan": [{"linked_component_ids": ["component-missing"]}]}]},
            evidence_snippets=[],
        )
        codes = {e["code"] for e in validation["errors"]}
        assert validation["publish_ready"] is False
        assert "LEGACY_EXPORT_REF" in codes
        assert "UNRESOLVED_EXPORT_REF" in codes

    def test_export_normalization_drops_claim_refs_when_claims_are_absent(self):
        mod = _get_export_helpers()
        claims = []
        equations = [
            {
                "equation_id": "eq_tex_b62",
                "linked_claim_ids": ["claim_span_001_5_sub07", "claim_span_001_5_sub08"],
            }
        ]
        components = []
        component_graph = {"nodes": [], "edges": []}
        derivation_chains = [
            {
                "derivation_id": "derivation_001",
                "steps": [
                    {
                        "step_id": "step_001",
                        "input_equation_ids": ["eq_tex_b62"],
                        "output_equation_ids": ["eq_tex_b62"],
                        "claim_ids": ["claim_span_001_5_sub07", "claim_span_001_5_sub08"],
                        "required_claim_ids": ["claim_span_001_5_sub07"],
                        "input_claim_ids": [],
                        "output_claim_ids": ["claim_span_001_5_sub08"],
                    }
                ],
            }
        ]

        mod._normalize_export_references(
            claims=claims,
            equations=equations,
            components=components,
            component_graph=component_graph,
            course_info={},
        )
        mod._normalize_derivation_references(
            claims=claims,
            equations=equations,
            derivation_chains=derivation_chains,
        )
        validation = mod._validate_export_references(
            claims=claims,
            equations=equations,
            components=components,
            component_graph=component_graph,
            course_info={},
            evidence_snippets=[],
            derivation_chains=derivation_chains,
        )

        assert equations[0]["linked_claim_ids"] == []
        step = derivation_chains[0]["steps"][0]
        assert step["claim_ids"] == []
        assert step["required_claim_ids"] == []
        assert step["input_claim_ids"] == []
        assert step["output_claim_ids"] == []
        codes = {e["code"] for e in validation["errors"]}
        assert "LEGACY_EXPORT_REF" not in codes
        assert "UNRESOLVED_EXPORT_REF" not in codes

    def test_derivation_validation_requires_chains_for_derivation_topics(self):
        mod = _get_export_helpers()
        validation = mod._validate_export_references(
            claims=[],
            equations=[{"equation_id": "eq_001"}],
            components=[],
            component_graph={"nodes": [], "edges": []},
            course_info={"topics": [{"title": "Derivation of the result"}]},
            evidence_snippets=[],
            derivation_chains=[],
        )
        assert validation["publish_ready"] is False
        assert any(e["code"] == "EMPTY_DERIVATION_CHAINS" for e in validation["errors"])

    def test_derivation_validation_checks_step_equation_and_evidence_refs(self):
        mod = _get_export_helpers()
        validation = mod._validate_export_references(
            claims=[{"claim_id": "claim_001"}],
            equations=[{"equation_id": "eq_001"}, {"equation_id": "eq_002"}],
            components=[],
            component_graph={"nodes": [], "edges": []},
            course_info={"topics": [{"title": "Derivation", "linked_derivation_ids": ["deriv_001"]}]},
            evidence_snippets=[{"evidence_id": "ev_001"}],
            derivation_chains=[{
                "derivation_id": "deriv_001",
                "steps": [{
                    "operation": "derive_from",
                    "input_equation_ids": ["eq_001"],
                    "output_equation_ids": ["eq_002"],
                    "claim_ids": ["claim_001"],
                    "source_evidence_ids": ["ev_001"],
                    "assumption_refs": [],
                }],
            }],
        )
        assert validation["publish_ready"] is True
        assert validation["summary"]["unresolved_reference_count"] == 0

    def test_derivation_topics_receive_derivation_ids(self):
        mod = _get_export_helpers()
        components = [{"component_id": "comp_001", "evidence_claims": ["claim_001"]}]
        course_info = {"topics": [{"title": "Derivation of Eq. 2", "linked_component_ids": ["comp_001"]}]}
        derivations = [{
            "derivation_id": "deriv_001",
            "steps": [{"claim_ids": ["claim_001"], "source_evidence_ids": ["ev_001"]}],
        }]
        mod._link_derivations_to_export_context(
            components=components,
            course_info=course_info,
            derivation_chains=derivations,
        )
        assert components[0]["linked_derivation_ids"] == ["deriv_001"]
        assert course_info["topics"][0]["linked_derivation_ids"] == ["deriv_001"]

    def test_export_bundle_preserves_references_between_claims_components_and_graph(self):
        mod = _get_export_helpers()

        claims = [
            {"claim_id": "claim_001", "text": "c1", "evidence_text": "e1", "source_scope": {}},
            {"claim_id": "claim_002", "text": "c2", "evidence_text": "e2", "source_scope": {}},
        ]
        components = [
            {
                "component_id": "comp_hqet",
                "name": "HQET",
                "evidence_claims": ["claim_001"],
                "inputs": [{"label": "input", "evidence_claims": ["claim_001"]}],
                "outputs": [],
            },
            {
                "component_id": "comp_sum_rule",
                "name": "Sum Rule",
                "evidence_claims": ["claim_002"],
                "inputs": [],
                "outputs": [],
            },
        ]
        component_graph = {
            "graph_schema_version": "0.1.0",
            "nodes": [
                {"node_id": "comp_hqet", "label": "HQET"},
                {"node_id": "comp_sum_rule", "label": "Sum Rule"},
            ],
            "edges": [
                {
                    "edge_id": "eg001",
                    "source": "comp_hqet",
                    "target": "comp_sum_rule",
                    "edge_type": "REQUIRES",
                    "evidence_claims": ["claim_001"],
                    "review_status": "teacher_review_required",
                }
            ],
        }
        evidence_snippets = mod._build_evidence_snippets(claims)

        # All component evidence_claims reference existing claim IDs
        claim_ids = {c["claim_id"] for c in claims}
        comp_node_ids = {c["component_id"] for c in components}
        graph_node_ids = {n["node_id"] for n in component_graph["nodes"]}

        for comp in components:
            for ec in comp.get("evidence_claims", []):
                assert ec in claim_ids, f"Component {comp['component_id']} references unknown claim {ec}"

        for edge in component_graph["edges"]:
            assert edge["source"] in comp_node_ids or edge["source"] in graph_node_ids
            assert edge["target"] in comp_node_ids or edge["target"] in graph_node_ids
            for ec in edge.get("evidence_claims", []):
                assert ec in claim_ids, f"Edge {edge['edge_id']} references unknown claim {ec}"

        for snippet in evidence_snippets:
            assert snippet["claim_id"] in claim_ids


# ---------------------------------------------------------------------------
# Integration: API endpoint and route registration
# ---------------------------------------------------------------------------

class TestExportApiRoute:
    def test_export_route_file_exists(self):
        assert EXPORT_ROUTE.exists(), "backend/api/routes/export.py must exist"

    def test_export_bundle_api_returns_zip_attachment(self):
        source = _read(EXPORT_ROUTE)
        assert 'media_type="application/zip"' in source
        assert 'attachment; filename=' in source
        assert ".zip" in source

    def test_export_route_registered_in_main(self):
        source = _read(MAIN_PY)
        assert "export_routes" in source or "export.router" in source or "routes.export" in source

    def test_export_endpoints_defined(self):
        source = _read(EXPORT_ROUTE)
        assert '"/api/courses/{course_id}/export-bundle"' in source
        assert '"/api/documents/{document_id}/export-bundle"' in source

    def test_export_requires_teacher_auth(self):
        source = _read(EXPORT_ROUTE)
        assert "_require_teacher" in source

    def test_export_scope_options_in_request(self):
        source = _read(EXPORT_ROUTE)
        assert "include_source_snippets" in source
        assert "include_debug_data" in source
        assert "include_ndjson" in source
        assert "include_review_fields" in source

    def test_export_utf8_encoding(self):
        source = _read(EXPORT_ROUTE)
        assert 'ensure_ascii=False' in source

    def test_export_zip_compression_deflated(self):
        source = _read(EXPORT_ROUTE)
        assert "ZIP_DEFLATED" in source
