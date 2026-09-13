"""export bundle の JSON-LD 化と同一性キー付与（knowledge_transfer_design.md §4.1 / P4-1）。

固定するのは:

- 束のルートに ``ro-crate-metadata.json`` があり、RO-Crate 1.1 + ``episteme`` / ``prov``
  の ``@context`` を持つ（X-13 / X-16）。
- ``@graph`` に ①メタデータ記述子 ②ルート Dataset ③束の各ファイル（File）
  ④解析 run（``prov:Activity``）と教材（``prov:Entity``）が載る。
- **人名を書かない**（帰属は監査台帳）・**数値を 1 つも載せない**（原則4）。
- claims / components / equations / evidence / derivation step の各項目に
  ``stable_key`` / ``knowledge_object_id`` が live 行との join で付き、行が無ければ**付かない**。
- manifest の ``export_schema_version`` が ``0.3.0`` で ``jsonld`` / ``import_support`` を持つ。
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import routes.export as export  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _manifest(**overrides) -> dict:
    base = dict(
        export_id="export_test_jsonld",
        scope_type="document",
        scope_id="doc-1",
        material_ids=[],
        document_ids=["doc-1"],
        claims=[],
        dsl_graph={"nodes": [], "edges": []},
        components=[],
        component_graph={"nodes": [], "edges": []},
        evidence_snippets=[],
        options={},
    )
    base.update(overrides)
    return export._build_manifest(**base)


def _walk(value):
    """JSON 構造の全スカラーを (path, value) で列挙する。"""
    stack = [("$", value)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, dict):
            for key, child in node.items():
                stack.append((f"{path}.{key}", child))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                stack.append((f"{path}[{index}]", child))
        else:
            yield path, node


class _FakeSession:
    """種別ごとに 1 クエリ = 1 結果セットを返す最小セッション。"""

    def __init__(self, rows_by_call, *, failing: bool = False):
        self._rows = list(rows_by_call)
        self._failing = failing
        self.statements: list[str] = []
        self._current: list = []

    def execute(self, stmt, params=None):
        self.statements.append(str(stmt))
        if self._failing:
            raise RuntimeError("db unavailable")
        self._current = self._rows.pop(0) if self._rows else []
        return self

    def fetchall(self):
        return self._current


# ---------------------------------------------------------------------------
# ro-crate-metadata.json
# ---------------------------------------------------------------------------


class TestRoCrateMetadata:
    def test_zip_contains_ro_crate_metadata(self):
        manifest = _manifest()
        zip_bytes = export._build_zip(
            manifest=manifest,
            claims=[],
            dsl_graph={"nodes": [], "edges": []},
            components=[],
            component_graph={"nodes": [], "edges": []},
            evidence_snippets=[],
            ro_crate=export._build_ro_crate(manifest, run_ids={"doc-1": "run-1"}),
        )
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert "ro-crate-metadata.json" in zf.namelist()
            crate = json.loads(zf.read("ro-crate-metadata.json").decode("utf-8"))
        assert crate["@context"][0] == "https://w3id.org/ro/crate/1.1/context"
        assert crate["@context"][1]["episteme"] == export.EPISTEME_VOCAB
        assert crate["@context"][1]["prov"] == export.PROV_VOCAB

    def test_zip_builds_the_crate_from_manifest_when_not_passed(self):
        """呼び出し側が渡さなくても束は記述子を欠かない。"""
        zip_bytes = export._build_zip(
            manifest=_manifest(),
            claims=[],
            dsl_graph={"nodes": [], "edges": []},
            components=[],
            component_graph={"nodes": [], "edges": []},
            evidence_snippets=[],
        )
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            crate = json.loads(zf.read("ro-crate-metadata.json").decode("utf-8"))
        assert crate["@graph"][0]["@id"] == "ro-crate-metadata.json"

    def test_graph_has_descriptor_root_files_and_activity(self):
        crate = export._build_ro_crate(_manifest(), run_ids={"doc-1": "run-1"})
        nodes = {node["@id"]: node for node in crate["@graph"]}

        descriptor = nodes["ro-crate-metadata.json"]
        assert descriptor["@type"] == "CreativeWork"
        assert descriptor["conformsTo"] == {"@id": export.RO_CRATE_PROFILE}
        assert descriptor["about"] == {"@id": "./"}

        root = nodes["./"]
        assert root["@type"] == "Dataset"
        assert root["episteme:exportId"] == "export_test_jsonld"
        assert root["episteme:schemaVersion"] == "0.3.0"
        # 束の各ファイルは File として hasPart から辿れる。
        part_ids = {part["@id"] for part in root["hasPart"]}
        assert "claims/claims.json" in part_ids
        assert "manifest.json" in part_ids
        # メタデータ記述子自身は hasPart に入れない（RO-Crate 1.1 の形）。
        assert "ro-crate-metadata.json" not in part_ids
        for part_id in part_ids:
            assert nodes[part_id]["@type"] == "File"
            assert nodes[part_id]["encodingFormat"] in ("application/json", "text/markdown")

        assert nodes["#document-doc-1"]["@type"] == ["prov:Entity", "CreativeWork"]
        activity = nodes["#analysis-run-run-1"]
        assert activity["@type"] == "prov:Activity"
        assert activity["prov:used"] == {"@id": "#document-doc-1"}
        assert activity["prov:generated"] == {"@id": "./"}

    def test_activity_is_omitted_when_the_run_is_unknown(self):
        """解析 run が引けない教材に活動を捏造しない。"""
        crate = export._build_ro_crate(_manifest(), run_ids={})
        ids = {node["@id"] for node in crate["@graph"]}
        assert "#document-doc-1" in ids
        assert not any(i.startswith("#analysis-run-") for i in ids)

    def test_no_person_is_named(self):
        """KT8 / 出口の弁: 束の中に人は書かない（帰属は監査台帳）。"""
        crate = export._build_ro_crate(_manifest(), run_ids={"doc-1": "run-1"})
        blob = json.dumps(crate, ensure_ascii=False)
        for term in ("Person", "author", "creator", "publisher", "exported_by", "changed_by"):
            assert term not in blob, f"JSON-LD に人の記述が入っている: {term}"

    def test_no_numeric_value_is_written(self):
        """原則4: confidence / weight / 件数などの数値を 1 つも載せない。"""
        crate = export._build_ro_crate(_manifest(), run_ids={"doc-1": "run-1"})
        numeric = [
            (path, value)
            for path, value in _walk(crate)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        assert numeric == [], f"JSON-LD に数値が載っている: {numeric}"


# ---------------------------------------------------------------------------
# manifest 0.3.0
# ---------------------------------------------------------------------------


class TestManifestSchemaVersion:
    def test_schema_version_is_0_3_0(self):
        assert _manifest()["export_schema_version"] == "0.3.0"
        assert export.EXPORT_SCHEMA_VERSION == "0.3.0"

    def test_manifest_declares_jsonld_and_import_support(self):
        manifest = _manifest()
        assert manifest["jsonld"]["metadata_file"] == "ro-crate-metadata.json"
        assert manifest["jsonld"]["context"][0].startswith("https://w3id.org/ro/crate/1.1")
        assert manifest["import_support"]["min_schema_version"] == "0.3.0"
        # KT4: 束の stable_key をそのまま同一性に使わないことを束の中で宣言する。
        assert manifest["import_support"]["stable_key_recomputed_on_import"] is True
        assert manifest["files"]["ro_crate"] == "ro-crate-metadata.json"

    def test_readme_documents_the_import_endpoint_and_the_two_rules(self):
        assert "/api/documents/{document_id}/import-bundle" in export._README_TEMPLATE
        assert "ro-crate-metadata.json" in export._README_TEMPLATE
        # T-1（承認は継承しない）と KT4（キーは再計算）の事実が README にある。
        assert "未確認の候補" in export._README_TEMPLATE
        assert "計算し直します" in export._README_TEMPLATE


# ---------------------------------------------------------------------------
# stable_key / knowledge_object_id の付与
# ---------------------------------------------------------------------------


class TestKnowledgeObjectKeys:
    def _sessions_rows(self):
        # (id, agent_id, stable_key[, source_scope]) の順（_load_knowledge_object_keys）。
        return [
            [("uuid-claim", "claim_1", "k1:claimkey", {"legacy_ids": ["blk_1:span_1"]})],
            [("uuid-comp", "comp_1", "k1:compkey", {"legacy_ids": ["comp_1"]})],
            [("uuid-eq", "eq_1", "k1:eqkey")],
            [("uuid-ev", "ev_1", "k1:evkey")],
            [("uuid-step", "step_1", "k1:stepkey")],
        ]

    def test_items_get_stable_key_and_object_id(self):
        claims = [{"claim_id": "claim_1"}, {"claim_id": "claim_missing"}]
        components = [{"component_id": "comp_1"}]
        equations = [{"equation_id": "eq_1"}]
        evidence = [{"evidence_id": "ev_1"}]
        chains = [{"derivation_id": "d1", "steps": [{"step_id": "step_1"}]}]

        summary = export._attach_knowledge_object_keys(
            _FakeSession(self._sessions_rows()),
            ["doc-1"],
            claims=claims,
            components=components,
            equations=equations,
            evidence_snippets=evidence,
            derivation_chains=chains,
        )

        assert claims[0]["stable_key"] == "k1:claimkey"
        assert claims[0]["knowledge_object_id"] == "uuid-claim"
        assert components[0]["stable_key"] == "k1:compkey"
        assert equations[0]["knowledge_object_id"] == "uuid-eq"
        assert evidence[0]["stable_key"] == "k1:evkey"
        assert chains[0]["steps"][0]["knowledge_object_id"] == "uuid-step"
        assert summary["kinds"] == [
            "claims", "components", "derivation_steps", "equations", "evidence",
        ]

    def test_items_without_a_live_row_get_no_key(self):
        """行が無ければキーを**付けない**（「対応がある」だけを事実として載せる）。"""
        claims = [{"claim_id": "claim_missing"}]
        export._attach_knowledge_object_keys(
            _FakeSession(self._sessions_rows()),
            ["doc-1"],
            claims=claims,
            components=[],
            equations=[],
            evidence_snippets=[],
            derivation_chains=[],
        )
        assert "stable_key" not in claims[0]
        assert "knowledge_object_id" not in claims[0]

    def test_legacy_ids_resolve_the_row(self):
        """agent ID が変わっていても ``source_scope.legacy_ids`` で引ける。"""
        claims = [{"claim_id": "blk_1:span_1"}]
        export._attach_knowledge_object_keys(
            _FakeSession(self._sessions_rows()),
            ["doc-1"],
            claims=claims,
            components=[],
            equations=[],
            evidence_snippets=[],
            derivation_chains=[],
        )
        assert claims[0]["stable_key"] == "k1:claimkey"

    def test_db_failure_does_not_break_the_export(self):
        """DB を読めない環境でも書き出しは落ちない（同一性キーは additive な事実）。"""
        claims = [{"claim_id": "claim_1"}]
        summary = export._attach_knowledge_object_keys(
            _FakeSession([], failing=True),
            ["doc-1"],
            claims=claims,
            components=[],
            equations=[],
            evidence_snippets=[],
            derivation_chains=[],
        )
        assert summary == {"kinds": []}
        assert "stable_key" not in claims[0]

    def test_no_session_and_no_documents_are_noops(self):
        assert export._load_knowledge_object_keys(None, ["doc-1"]) == {}
        assert export._load_knowledge_object_keys(_FakeSession([]), []) == {}

    def test_live_views_are_read_for_claims_and_components(self):
        """KO5: claim / component は基表ではなく live ビューから読む。"""
        session = _FakeSession(self._sessions_rows())
        export._load_knowledge_object_keys(session, ["doc-1"])
        joined = "\n".join(session.statements)
        assert "theory_claims_live" in joined
        assert "theory_components_live" in joined
        assert "FROM theory_claims\n" not in joined
        assert "FROM theory_components\n" not in joined
        # 新 3 表は基表 + superseded_at IS NULL。
        assert "knowledge_equations" in joined
        assert "superseded_at IS NULL" in joined
