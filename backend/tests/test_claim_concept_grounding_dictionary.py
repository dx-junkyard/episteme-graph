"""主張に供給する概念辞書と接地の導出（claim_concept_grounding_design.md §4 / §5）。

DB にも LLM にも触らない（辞書の材料は monkeypatch で差し替える）。固定するもの:

- 辞書の材料 3 種（レジストリの確定ラベル / カートリッジ別名 / DSL ノード）と
  代表の優先順（registry > cartridge > dsl）・``also_from`` の保持
- 記号は辞書に入らない（CG6）
- 空 ``cartridge_id`` では cartridge を読まない（既定カートリッジへ縮退させない）
- 照合は**語境界付き**（``SM`` が ``cosmological`` に当たらない = P0-2）
- ① 本文照合 / ①' DSL の直接参照 の 2 経路と、``concept_assignment_status`` を
  書き換えないこと（CG3）
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.library import claim_concept_grounding as grounding  # noqa: E402
from core.library import concept_dictionary as cd  # noqa: E402


# ---------------------------------------------------------------------------
# fake 素材
# ---------------------------------------------------------------------------


@dataclass
class _Node:
    node_id: str
    node_value: str
    node_type: str = "Observable"
    source_refs: dict = field(default_factory=dict)


@dataclass
class _Dsl:
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)


@dataclass
class _Claim:
    claim_id: str
    text: str
    concepts: list = field(default_factory=list)
    normalized_text: str = ""
    concept_assignment_status: str = "review_required"
    support_status: str = "source_backed"
    is_atomic: bool = True


@dataclass
class _ClaimObjects:
    claims: list = field(default_factory=list)


@pytest.fixture
def registry_entries(monkeypatch):
    """``store.list_entries`` / ``registry.labels_for_entries`` を差し替える。"""
    state: dict = {"entries": [], "labels": {}}

    monkeypatch.setattr(cd.library_store, "list_entries", lambda **kw: list(state["entries"]))
    monkeypatch.setattr(
        cd.registry, "labels_for_entries",
        lambda entry_ids, include_hidden=True, session=None: {
            eid: state["labels"].get(eid, []) for eid in entry_ids
        },
    )
    # 既定ではカートリッジを読まない（各テストが必要なときだけ差し替える）。
    monkeypatch.setattr(cd, "cartridge_ontology_for", lambda cartridge_id: None)
    return state


# ---------------------------------------------------------------------------
# 1. 辞書の材料
# ---------------------------------------------------------------------------


class TestDictionaryMaterials:
    def test_registry_entries_and_labels_become_names(self, registry_entries):
        registry_entries["entries"] = [
            {"id": "e1", "name": "Form factor", "entry_type": "concept", "aliases": []}
        ]
        registry_entries["labels"]["e1"] = [
            {"label": "transition form factor", "normalized_label": "transition form factor"}
        ]
        dictionary = cd.build_concept_dictionary(cartridge_id=None)
        item = dictionary.entries["form factor"]
        assert item["source"] == cd.SOURCE_REGISTRY_LABEL
        assert item["entry_id"] == "e1"
        assert item["concept_type"] == "concept"
        assert "transition form factor" in item["names"]

    def test_entry_aliases_column_is_kept_too(self, registry_entries):
        registry_entries["entries"] = [
            {"id": "e1", "name": "Form factor", "entry_type": "concept", "aliases": ["FF factor"]}
        ]
        dictionary = cd.build_concept_dictionary(cartridge_id=None)
        assert "FF factor" in dictionary.entries["form factor"]["names"]

    def test_cartridge_aliases_are_added_when_a_cartridge_resolves(self, registry_entries, monkeypatch):
        monkeypatch.setattr(
            cd, "cartridge_ontology_for",
            lambda cartridge_id: {
                "aliases": {"Standard Model": ["SM", "standard model"]},
                "concept_types": {"Standard Model": "Theory"},
            },
        )
        dictionary = cd.build_concept_dictionary(cartridge_id="particle_physics")
        item = dictionary.entries["standard model"]
        assert item["source"] == cd.SOURCE_CARTRIDGE_ALIAS
        assert item["concept_type"] == "Theory"
        assert "standard model" in item["names"]
        # CG6: 別名も記号判定を通る。2 文字の ``SM`` は記号側なので照合表に入れない
        # （``SM`` が別の語に当たる F-7 の再発をそもそも作らない）。
        assert "SM" not in item["names"]

    def test_dsl_nodes_are_added_only_when_dsl_is_given(self, registry_entries):
        dsl = _Dsl(nodes=[_Node("n1", "zero recoil limit", source_refs={"claim_ids": ["c1"]})])
        without = cd.build_concept_dictionary(cartridge_id=None)
        with_dsl = cd.build_concept_dictionary(cartridge_id=None, dsl=dsl)
        assert "zero recoil limit" not in without.entries
        item = with_dsl.entries["zero recoil limit"]
        assert item["source"] == cd.SOURCE_DSL_NODE
        assert item["claim_ids"] == ["c1"]

    def test_registry_wins_over_cartridge_and_dsl_but_keeps_also_from(
        self, registry_entries, monkeypatch
    ):
        registry_entries["entries"] = [
            {"id": "e1", "name": "Form factor", "entry_type": "concept", "aliases": []}
        ]
        monkeypatch.setattr(
            cd, "cartridge_ontology_for",
            lambda cartridge_id: {"aliases": {"form factor": []}, "concept_types": {}},
        )
        dsl = _Dsl(nodes=[_Node("n1", "Form Factor")])
        dictionary = cd.build_concept_dictionary(cartridge_id="x", dsl=dsl)
        item = dictionary.entries["form factor"]
        assert item["source"] == cd.SOURCE_REGISTRY_LABEL
        assert item["entry_id"] == "e1"
        assert set(item["also_from"]) == {cd.SOURCE_CARTRIDGE_ALIAS, cd.SOURCE_DSL_NODE}

    def test_symbols_never_enter_the_dictionary(self, registry_entries):
        registry_entries["entries"] = [
            {"id": "e1", "name": "R_D", "entry_type": "concept", "aliases": ["\\lambda"]},
            {"id": "e2", "name": "λ", "entry_type": "concept", "aliases": []},
        ]
        dsl = _Dsl(nodes=[_Node("n1", "b_1")])
        dictionary = cd.build_concept_dictionary(cartridge_id=None, dsl=dsl)
        assert dictionary.entries == {}

    def test_db_failure_degrades_to_an_empty_dictionary(self, registry_entries, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(cd.library_store, "list_entries", _boom)
        dictionary = cd.build_concept_dictionary(cartridge_id=None)
        assert not dictionary and len(dictionary) == 0


class TestCartridgeIsNotReadWithoutAnId:
    @pytest.mark.parametrize("cartridge_id", [None, "", "   "])
    def test_empty_cartridge_id_does_not_load_a_cartridge(self, cartridge_id, monkeypatch):
        calls: list = []
        monkeypatch.setattr(
            cd, "load_cartridge_or_none",
            lambda loader, cid, **kw: calls.append(cid),
        )
        assert cd.cartridge_ontology_for(cartridge_id) is None
        assert calls == []

    def test_a_real_cartridge_id_is_loaded(self, monkeypatch):
        calls: list = []

        class _Ctx:
            aliases = {"Standard Model": ["SM"]}
            ontology = {"concept_types": [{"id": "Theory", "examples": ["Standard Model"]}]}

        def _load(loader, cid, **kw):
            calls.append(cid)
            return _Ctx()

        monkeypatch.setattr(cd, "load_cartridge_or_none", _load)
        ontology = cd.cartridge_ontology_for("particle_physics")
        assert calls == ["particle_physics"]
        assert ontology == {
            "aliases": {"Standard Model": ["SM"]},
            "concept_types": {"Standard Model": "Theory"},
        }


# ---------------------------------------------------------------------------
# 2. 照合（語境界・resolver）
# ---------------------------------------------------------------------------


class TestMatching:
    def _dictionary(self, *names: str) -> cd.ConceptDictionary:
        dictionary = cd.ConceptDictionary()
        for name in names:
            dictionary.add(name, source=cd.SOURCE_REGISTRY_LABEL, entry_id="e1")
        return dictionary

    def test_word_boundary_only(self):
        dictionary = cd.ConceptDictionary()
        dictionary.add("Standard Model", source=cd.SOURCE_CARTRIDGE_ALIAS, aliases=["SM"])
        assert dictionary.match("the cosmological constant") == []
        assert dictionary.match("we use the standard model here")[0][0] == "standard model"

    def test_resolver_returns_claim_concepts(self):
        resolver = cd.make_concept_resolver(self._dictionary("Form factor"))
        concepts = resolver("the form factor is measured", [], {})
        assert len(concepts) == 1
        assert concepts[0].name == "Form factor"
        assert concepts[0].normalized == "form factor"

    def test_resolver_is_capped(self):
        dictionary = cd.ConceptDictionary()
        words = [f"concept number {i}" for i in range(cd.MAX_CONCEPTS_PER_CLAIM + 3)]
        for word in words:
            dictionary.add(word, source=cd.SOURCE_REGISTRY_LABEL)
        resolver = cd.make_concept_resolver(dictionary)
        text = " ".join(words)
        assert len(resolver(text, [], {})) == cd.MAX_CONCEPTS_PER_CLAIM


# ---------------------------------------------------------------------------
# 3. 接地（ground_claims）
# ---------------------------------------------------------------------------


class TestGroundClaims:
    def _dictionary(self) -> cd.ConceptDictionary:
        dictionary = cd.ConceptDictionary()
        dictionary.add("Form factor", source=cd.SOURCE_REGISTRY_LABEL, entry_id="e1")
        dictionary.add("zero recoil limit", source=cd.SOURCE_DSL_NODE)
        return dictionary

    def test_lexical_match_adds_a_concept_with_provenance(self):
        claims = _ClaimObjects(claims=[_Claim("c1", "The form factor is normalised.")])
        result = grounding.ground_claims(claims, None, self._dictionary())
        concept = claims.claims[0].concepts[0]
        assert concept.normalized == "form factor"
        assert concept.name == "Form factor"
        item = result.claims["c1"][0]
        assert item["source"] == cd.SOURCE_REGISTRY_LABEL
        assert item["entry_id"] == "e1"
        assert item["mapping_justification"] == "lexical_match"
        assert item["canonical"] == "Form factor"

    def test_dsl_reference_adds_a_concept_even_without_the_words(self):
        dsl = _Dsl(nodes=[_Node("n1", "heavy quark expansion", source_refs={"claim_ids": ["c1"]})])
        claims = _ClaimObjects(claims=[_Claim("c1", "This quantity is bounded.")])
        result = grounding.ground_claims(claims, dsl, cd.ConceptDictionary())
        item = result.claims["c1"][0]
        assert item["source"] == cd.SOURCE_DSL_REFERENCE
        assert item["mapping_justification"] == "llm_candidate"
        assert claims.claims[0].concepts[0].name == "heavy quark expansion"

    def test_symbol_nodes_are_not_referenced(self):
        dsl = _Dsl(nodes=[_Node("n1", "R_D", source_refs={"claim_ids": ["c1"]})])
        claims = _ClaimObjects(claims=[_Claim("c1", "R_D is measured.")])
        result = grounding.ground_claims(claims, dsl, cd.ConceptDictionary())
        assert result.claims == {}
        assert claims.claims[0].concepts == []

    def test_existing_concepts_are_kept_and_not_duplicated(self):
        from episteme_graph.agents.claim_object_builder.schema import ClaimConcept

        claim = _Claim(
            "c1",
            "The form factor of R_D.",
            concepts=[ClaimConcept(name="R_D", normalized="R_D", concept_type="symbol")],
        )
        claims = _ClaimObjects(claims=[claim])
        grounding.ground_claims(claims, None, self._dictionary())
        names = [c.name for c in claim.concepts]
        assert names == ["R_D", "Form factor"]

        # 2 度目は増えない（``normalized`` で畳む）。
        grounding.ground_claims(claims, None, self._dictionary())
        assert [c.name for c in claim.concepts] == names

    def test_assignment_status_is_never_rewritten(self):
        claim = _Claim("c1", "The form factor is normalised.")
        claims = _ClaimObjects(claims=[claim])
        grounding.ground_claims(claims, None, self._dictionary())
        assert claim.concept_assignment_status == "review_required"
        assert claim.support_status == "source_backed"
        assert claim.is_atomic is True

    def test_coverage_counts_concept_layer_mentions(self):
        claims = _ClaimObjects(
            claims=[
                _Claim("c1", "The form factor is normalised."),
                _Claim("c2", "Nothing matches here."),
            ]
        )
        result = grounding.ground_claims(claims, None, self._dictionary())
        payload = result.to_dict()
        assert payload["population"] == 2
        assert payload["processed"] == 1
        assert payload["reasons"] == [grounding.REASON_NO_MATCH]

    def test_empty_dictionary_changes_nothing(self):
        claim = _Claim("c1", "The form factor is normalised.")
        claims = _ClaimObjects(claims=[claim])
        result = grounding.ground_claims(claims, None, cd.ConceptDictionary())
        assert claim.concepts == []
        assert result.claims == {} and result.claims_changed == 0
        assert result.to_dict()["processed"] == 0


class TestMergeIntoConcepts:
    def test_merges_provenance_by_normalized_key(self):
        merged = grounding.merge_grounding_into_concepts(
            [
                {"name": "Form factor", "normalized": "form factor", "concept_type": "concept"},
                {"name": "R_D", "normalized": "R_D", "concept_type": "symbol"},
            ],
            [
                {
                    "normalized": "form factor",
                    "name": "Form factor",
                    "canonical": "Form factor",
                    "source": "registry_label",
                    "entry_id": "e1",
                    "mapping_justification": "lexical_match",
                }
            ],
        )
        assert merged[0]["entry_id"] == "e1"
        assert merged[0]["source"] == "registry_label"
        assert merged[0]["concept_type"] == "concept"  # 既存キーは不変
        assert set(merged[1]) == {"name", "normalized", "concept_type"}  # 当たらない要素

    def test_no_grounding_returns_the_input(self):
        concepts = [{"name": "R_D", "normalized": "R_D"}]
        assert grounding.merge_grounding_into_concepts(concepts, None) == concepts
