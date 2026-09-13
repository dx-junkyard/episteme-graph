"""主張の概念接地の不変条項ガードレール（claim_concept_grounding_design.md §2 CG1〜CG7・§9）。

構造的に守るのは:

- **CG1 A層非改変** — ``src/episteme_graph/agents/`` を書き換えない（本層は既存の
  ``concept_resolver`` / ``cartridge_ontology`` 注入口と between-stage フックだけを使う）
- **CG2 決定論・非LLM** — ``core/library/{concept_dictionary,claim_concept_grounding}.py``
  が fastapi / ``core.llm`` / 埋め込み生成を import・呼び出ししない。部分文字列一致
  （``in text``）を書かず ``text_mentions_alias`` だけを使う
- **CG3 概念は候補** — 後段が ``concept_assignment_status`` / ``support_status`` /
  ``is_atomic`` を書き換えない
- **CG6 記号は概念にしない** — 辞書の登録が ``is_symbol_like_concept_name`` を通る
- **分野は入口で選ぶ** — 空 ``cartridge_id`` で cartridge を読まない
- フックが ``dsl_linking`` の直後にあること / ``persist_qualified_claims`` の既存キー不変

DB にも LLM にも接続しない静的検査。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    extract_function_source,
)

LIBRARY_DIR = BACKEND / "core" / "library"
DICTIONARY_PATH = LIBRARY_DIR / "concept_dictionary.py"
GROUNDING_PATH = LIBRARY_DIR / "claim_concept_grounding.py"
ORCHESTRATOR_PATH = BACKEND / "core" / "document_pipeline" / "orchestrator.py"
PERSISTENCE_PATH = BACKEND / "core" / "document_pipeline" / "persistence.py"

DICTIONARY_SRC = DICTIONARY_PATH.read_text(encoding="utf-8")
GROUNDING_SRC = GROUNDING_PATH.read_text(encoding="utf-8")
ORCHESTRATOR_SRC = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
PERSISTENCE_SRC = PERSISTENCE_PATH.read_text(encoding="utf-8")

_MODULES = [("concept_dictionary.py", DICTIONARY_SRC), ("claim_concept_grounding.py", GROUNDING_SRC)]
_EMBEDDING_CALLS = ("generate_embeddings(", "embed_with_context(", "generate_text(")


# ---------------------------------------------------------------------------
# 1. core の純粋性・決定論（CG2）
# ---------------------------------------------------------------------------


class TestCorePurity:
    @pytest.mark.parametrize("name,src", _MODULES)
    def test_does_not_import_fastapi_or_the_llm_layer(self, name, src):
        assert_source_does_not_import(
            src, ["fastapi", "core.llm", "openai", "services", "starlette"], context=name
        )

    @pytest.mark.parametrize("name,src", _MODULES)
    def test_never_calls_a_model(self, name, src):
        offending = [call for call in _EMBEDDING_CALLS if call in src]
        assert offending == [], f"{name}: モデル呼び出し {offending}"

    @pytest.mark.parametrize("name,src", _MODULES)
    def test_has_no_row_deletion(self, name, src):
        assert not re.search(r"\bDELETE\s+FROM\s+\w", src, re.IGNORECASE), name

    def test_the_modules_are_importable_without_the_api_layer(self):
        from core.library import claim_concept_grounding, concept_dictionary  # noqa: F401


# ---------------------------------------------------------------------------
# 2. 照合は語境界付きだけ（CG2 / P0-2）
# ---------------------------------------------------------------------------


class TestWordBoundaryMatchingOnly:
    @pytest.mark.parametrize("name,src", _MODULES)
    def test_no_substring_containment_on_text(self, name, src):
        """``alias in text`` 型の部分文字列一致を書かない（F-7 / K-3 の再発防止）。"""
        tree = ast.parse(src)
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare) or len(node.ops) != 1:
                continue
            if not isinstance(node.ops[0], ast.In):
                continue
            target = node.comparators[0]
            target_name = ""
            if isinstance(target, ast.Name):
                target_name = target.id
            elif isinstance(target, ast.Call) and isinstance(target.func, ast.Name):
                target_name = target.func.id
            if target_name in {"text", "body", "haystack", "match_text", "claim_text"}:
                offenders.append(f"line {node.lineno}")
        assert offenders == [], f"{name}: 本文への部分文字列一致 {offenders}"

    def test_dictionary_uses_the_shared_alias_matcher(self):
        assert "from episteme_graph.agents.alias_matching import text_mentions_alias" in DICTIONARY_SRC
        assert "text_mentions_alias(" in DICTIONARY_SRC

    def test_matching_is_word_bounded_in_practice(self):
        from core.library import concept_dictionary as cd

        dictionary = cd.ConceptDictionary()
        dictionary.add("Standard Model", source=cd.SOURCE_CARTRIDGE_ALIAS)
        assert dictionary.match("cosmological standard modeling") == []


# ---------------------------------------------------------------------------
# 3. 記号は概念にしない（CG6）
# ---------------------------------------------------------------------------


class TestSymbolsAreNotConcepts:
    def test_dictionary_filters_symbol_like_names(self):
        assert "is_symbol_like_concept_name" in DICTIONARY_SRC
        body = extract_function_source(DICTIONARY_SRC, "add")
        assert "is_symbol_like_concept_name(canonical)" in body

    def test_grounding_filters_symbol_like_dsl_nodes(self):
        body = extract_function_source(GROUNDING_SRC, "_dsl_reference_index")
        assert "is_symbol_like_concept_name(value)" in body

    def test_symbol_names_are_rejected_in_practice(self):
        from core.library import concept_dictionary as cd

        dictionary = cd.ConceptDictionary()
        for name in ("R_D", "λ", "\\lambda", "b_1", ""):
            assert dictionary.add(name, source=cd.SOURCE_REGISTRY_LABEL) is None
        assert dictionary.entries == {}


# ---------------------------------------------------------------------------
# 4. 概念は候補・確定は人間（CG3）
# ---------------------------------------------------------------------------


class TestStatusIsNeverRewritten:
    @pytest.mark.parametrize("name,src", _MODULES)
    def test_no_assignment_to_the_derived_statuses(self, name, src):
        for field_name in ("concept_assignment_status", "support_status", "is_atomic"):
            assert f'"{field_name}"' not in src or f'setattr(claim, "{field_name}"' not in src
            assert f"{field_name} =" not in src, f"{name}: {field_name} を書き換えている"

    def test_the_hook_only_writes_concepts(self):
        body = extract_function_source(GROUNDING_SRC, "_set_concepts")
        assert 'setattr(claim, "concepts", concepts)' in body
        tree = ast.parse(GROUNDING_SRC)
        setattr_targets = {
            node.args[1].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
        }
        assert setattr_targets == {"concepts"}


# ---------------------------------------------------------------------------
# 5. 分野は入口で選ぶ（空 cartridge では読まない）
# ---------------------------------------------------------------------------


class TestCartridgeIsGated:
    def test_cartridge_loading_is_guarded_by_a_non_empty_id(self):
        body = extract_function_source(DICTIONARY_SRC, "cartridge_ontology_for")
        guard = body.index('if not str(cartridge_id or "").strip():')
        call = body.index("load_cartridge_or_none(")
        assert guard < call, "空 cartridge_id のガードが読み込みより後ろにある"

    def test_the_default_cartridge_fallback_is_not_used(self):
        """``core.cartridges.load_cartridge(None)`` は既定カートリッジへ縮退する。"""
        for src in (DICTIONARY_SRC, GROUNDING_SRC):
            assert "from core.cartridges import" not in src
            assert "load_cartridge(" not in src


# ---------------------------------------------------------------------------
# 6. 配線（フック位置・永続化の後方互換）
# ---------------------------------------------------------------------------


class TestWiring:
    def test_hook_is_registered_right_after_dsl_linking(self):
        from core.document_pipeline import orchestrator as orch

        names = [
            step.name or getattr(step.execute, "__name__", "?")
            for step in orch._PIPELINE_STEPS
        ]
        assert names.index("_hook_claim_concept_grounding") == names.index("dsl_linking") + 1

    def test_the_hook_is_non_fatal(self):
        body = extract_function_source(ORCHESTRATOR_SRC, "_hook_claim_concept_grounding")
        assert "except Exception:" in body
        assert "raise" not in body
        assert body.rstrip().endswith("return False")

    def test_coverage_goes_through_the_shared_helper(self):
        body = extract_function_source(ORCHESTRATOR_SRC, "_hook_claim_concept_grounding")
        assert "_attach_coverage(" in body
        assert '"coverage"' not in body, "coverage キーを手で組み立てない（P0-10）"

    def test_persist_keeps_its_existing_keys(self):
        """``concepts`` 以外の values キーと列は増やさない（§6）。"""
        body = extract_function_source(PERSISTENCE_SRC, "_build_claim_items")
        assert "merge_concept_grounding(" in body
        for forbidden in ('"concept_source"', '"concept_entry_id"', "ALTER TABLE"):
            assert forbidden not in body, forbidden

    def test_merge_is_additive_only(self):
        from core.library.claim_concept_grounding import merge_grounding_into_concepts

        original = {"name": "Form factor", "normalized": "form factor", "source": "manual"}
        merged = merge_grounding_into_concepts(
            [dict(original)],
            [{"normalized": "form factor", "source": "registry_label", "entry_id": "e1"}],
        )
        # 既にある値は上書きしない（情報を落とさない = CG5）。
        assert merged[0]["source"] == "manual"
        assert merged[0]["entry_id"] == "e1"


# ---------------------------------------------------------------------------
# 7. A層非改変（CG1）
# ---------------------------------------------------------------------------


class TestAgentLayerUntouched:
    def test_the_builder_injection_points_still_exist(self):
        """A層に手を入れず**既存の**注入口を使っていること。"""
        builder_src = (
            ROOT / "src" / "episteme_graph" / "agents" / "claim_object_builder" / "builder.py"
        ).read_text(encoding="utf-8")
        assert "concept_resolver: Optional[Callable] = None," in builder_src
        assert "cartridge_ontology: dict | None = None," in builder_src

    def test_backend_never_imports_the_builder_internals(self):
        for name, src in _MODULES:
            assert "claim_object_builder.builder" not in src, name
