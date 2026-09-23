"""理論モジュール層 — ガードレール（構造的に守る不変条項）。

正本: ``docs/features/theory_module_layer_design.md`` §2（TM1〜TM10）/ §8.1（ガードレール候補）。

- core は FastAPI / sqlalchemy / LLM / embedding を import しない（TM2）。
- DTO に数値・指紋・閾値に当たるキーが再帰的に無い（TM6）。
- 入力の artifact と graph_json を書き換えない（TM1）。
- 閾値 k・共有の基礎の閾値は環境変数・settings から読まない（§5.4 / SA7）。
- ラベル組み立てのコードに特定分野の語が無い（TM5）。
"""

from __future__ import annotations

import ast
import copy
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from tests.guardrail_helpers import assert_module_tree_does_not_import, assert_module_tree_forbids  # noqa: E402

from core.theory_modules import build_theory_modules  # noqa: E402
from core.theory_modules import schema as tm_schema  # noqa: E402

CORE_DIR = BACKEND / "core" / "theory_modules"
FIXTURES = BACKEND / "tests" / "fixtures" / "theory_modules"
FIXTURE_NAMES = (
    "arxiv_2407_01221v2_tex.json",
    "arxiv_2609_15375v1_tex.json",
    "arxiv_2609_15375v1_pdf.json",
)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _build(fixture: dict) -> dict:
    return build_theory_modules(
        document_id=fixture["document_id"],
        artifacts=fixture["artifacts"],
        graph_json=fixture["graph_json"],
    )


def _walk_keys(value, path="$"):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, f"{path}.{key}"
            yield from _walk_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_keys(child, f"{path}[{index}]")


def _walk_values(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)
    else:
        yield value


class TestImportBoundary:
    def test_core_does_not_import_web_db_llm(self):
        assert_module_tree_does_not_import(
            CORE_DIR,
            ["fastapi", "sqlalchemy", "core.llm", "core.postgres", "openai", "core.llm_worker"],
        )

    def test_core_does_not_touch_embeddings(self):
        assert_module_tree_forbids(
            CORE_DIR,
            ["generate_embeddings", "embed_with_context", "core.embedder", "llm_worker.embedding", "pgvector"],
        )

    def test_core_import_does_not_load_forbidden_modules(self):
        """別プロセスで core.theory_modules だけを import し、推移的に重い層を読まないこと。"""
        import subprocess

        code = (
            "import sys; sys.path.insert(0, %r); import core.theory_modules; "
            "bad = [m for m in ('fastapi', 'sqlalchemy', 'core.llm', 'openai') if m in sys.modules]; "
            "print(','.join(bad))"
        ) % str(BACKEND)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        assert out.stdout.strip() == ""


class TestNoNumbersInDto:
    @pytest.mark.parametrize("name", FIXTURE_NAMES)
    def test_forbidden_keys_absent_recursively(self, name):
        result = _build(_load(name))
        offending = [path for key, path in _walk_keys(result) if key in tm_schema.FORBIDDEN_KEYS]
        assert offending == []

    @pytest.mark.parametrize("name", FIXTURE_NAMES)
    def test_no_numeric_values_in_dto(self, name):
        """件数・本数・閾値を値として持たない（TM6。真偽値は available / isolated の有無のみ）。"""
        result = _build(_load(name))
        numbers = [v for v in _walk_values(result) if isinstance(v, (int, float)) and not isinstance(v, bool)]
        assert numbers == []


class TestInputsUntouched:
    @pytest.mark.parametrize("name", FIXTURE_NAMES)
    def test_graph_json_and_artifacts_not_modified(self, name):
        fixture = _load(name)
        graph_before = copy.deepcopy(fixture["graph_json"])
        artifacts_before = copy.deepcopy(fixture["artifacts"])
        _build(fixture)
        assert fixture["graph_json"] == graph_before
        assert fixture["artifacts"] == artifacts_before

    def test_core_does_not_mutate_input_containers(self):
        """builder が入力 dict / list を書き換える API（update / pop / del / 添字代入）を使わないこと。"""
        src = (CORE_DIR / "builder.py").read_text(encoding="utf-8")
        for term in ("graph_json[", "artifacts[", "node[", ".update(", "graph_json.pop(",
                     "artifacts.pop(", "node.pop(", "del ", "setdefault(\"nodes\""):
            assert term not in src, term


class TestConstantsAreCode:
    def test_thresholds_are_literal_constants(self):
        assert tm_schema.OUTER_INTERFACE_LIMIT == 3
        assert tm_schema.INNER_INTERFACE_LIMIT == 2
        assert tm_schema.SHARED_FOUNDATION_MIN_CONSUMERS == 4
        assert tm_schema.MODULE_IDENTITY_MIN_MEMBERS == 3
        assert tm_schema.MODULE_IDENTITY_MIN_PROCESS_KINDS == 2
        # Phase 1（§13.3）: module_key の材料を equation stable_key に差し替えたので m2。
        assert tm_schema.RULE_VERSION == "m2"

    def test_thresholds_not_read_from_environment(self):
        assert_module_tree_forbids(
            CORE_DIR,
            ["os.environ", "getenv", "get_settings", "settings.", "import os", "from os", "from core.config"],
        )

    def test_threshold_assignments_are_int_literals(self):
        tree = ast.parse((CORE_DIR / "schema.py").read_text(encoding="utf-8"))
        wanted = {
            "OUTER_INTERFACE_LIMIT", "INNER_INTERFACE_LIMIT", "SHARED_FOUNDATION_MIN_CONSUMERS",
            "MODULE_IDENTITY_MIN_MEMBERS", "MODULE_IDENTITY_MIN_PROCESS_KINDS",
        }
        found = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in wanted:
                    found[node.targets[0].id] = node.value
        assert set(found) == wanted
        assert all(isinstance(value, ast.Constant) and isinstance(value.value, int) for value in found.values())


class TestDomainIndependence:
    #: 実測（設計書 §3）に出てきた分野語と、他の cartridge の分野語。ラベル組み立てのコードに書かない。
    DOMAIN_TERMS = (
        "bias", "galaxy", "galaxies", "Horndeski", "horndeski", "flavor", "flavour", "neutrino",
        "skewness", "cosmolog", "lensing", "halo", "quark", "power spectrum", "density",
    )

    def test_core_sources_have_no_domain_terms(self):
        assert_module_tree_forbids(CORE_DIR, list(self.DOMAIN_TERMS))

    def test_no_new_translation_table_in_core(self):
        """訳語は element_vocab の既存表から引く（TM5）。core に日本語の dict リテラルを作らない。"""
        for path in sorted(CORE_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    values = [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
                    japanese = [v for v in values if any("぀" <= ch <= "鿿" for ch in v)]
                    assert not japanese, f"{path.name}: 訳語表らしき dict がある: {japanese[:3]}"

    def test_builder_uses_element_vocab(self):
        src = (CORE_DIR / "builder.py").read_text(encoding="utf-8")
        assert "from core.element_vocab import" in src


class TestNoWriteSurface:
    def test_core_has_no_sql(self):
        assert_module_tree_forbids(CORE_DIR, ["INSERT INTO", "UPDATE ", "DELETE FROM", "sa_text", "execute("])

    def test_route_is_get_only(self):
        src = (BACKEND / "api" / "routes" / "theory_components.py").read_text(encoding="utf-8")
        assert '@router.get("/documents/{document_id}/theory-modules"' in src
        for method in ("post", "put", "patch", "delete"):
            assert f'@router.{method}("/documents/{{document_id}}/theory-modules' not in src


class TestPersistenceBoundary:
    """Phase 1（§13.4 / §13.11）: 保存用出力はパイプラインステージだけが呼ぶ。"""

    def test_api_does_not_import_the_records_builder(self):
        """import 文（関数内の遅延 import を含む）と属性参照の両方で見る。docstring の言及は数えない。"""
        offending = []
        for path in sorted((BACKEND / "api").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.ImportFrom):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.Attribute):
                    names = [node.attr]
                elif isinstance(node, ast.Name):
                    names = [node.id]
                if "build_theory_module_records" in names:
                    offending.append(path.relative_to(BACKEND).as_posix())
        assert offending == []

    def test_records_builder_is_called_by_the_orchestrator(self):
        src = (BACKEND / "core" / "document_pipeline" / "orchestrator.py").read_text(encoding="utf-8")
        assert "build_theory_module_records(" in src

    @pytest.mark.parametrize("name", FIXTURE_NAMES)
    def test_dto_keys_do_not_include_the_fingerprint(self, name):
        result = _build(_load(name))
        keys = {key for key, _path in _walk_keys(result)}
        assert not keys & {"structure_fingerprint", "fingerprint", "identity_eligible",
                           "produced_equation_keys", "stable_key", "edge_type"}

    def test_core_does_not_import_persistence(self):
        assert_module_tree_does_not_import(
            CORE_DIR, ["core.document_pipeline", "core.knowledge_objects"],
        )
