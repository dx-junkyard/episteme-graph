"""レジストリ ↔ 分野の地図の候補導出（概念レジストリ P3-4・§6.1）。

DB にも LLM にも触らない: 骨格 / アンカーベクトル / エントリ / ラベル / 既存リンクを
すべて fake に差し替え、``core/library/atlas_links.py`` の**導出規則**だけを見る。

固定するもの:

- 語彙一致 → ``exact_match`` / ``lexical_match``（教員確定別名も照合に使う）
- ベクトル近傍 → ``close_match`` / ``vector_similarity``（最上位帯のみ）
- 同じ (entry, node) に両方立てば**語彙一致を採る**
- ドメイン跨ぎの双子 → candidate entry 1 行 + node リンク 2 本
- ``dismissed`` の候補（``candidate_key`` / ``link_key``）は再提案しない（LS3）
- 戻り値に cosine の生値が現れない（KR6）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.atlas import AtlasSkeleton, SkeletonConcept, SkeletonRegion  # noqa: E402
from core.library import atlas_links  # noqa: E402
from core.library import schema as library_schema  # noqa: E402

_SESSION = object()


def _skeleton(cartridge: str, version: str, concepts: list[tuple[str, str]]) -> AtlasSkeleton:
    return AtlasSkeleton(
        cartridge=cartridge,
        status="frozen",
        version=version,
        regions=(
            SkeletonRegion(
                id="r1",
                label="宇宙論",
                concepts=tuple(SkeletonConcept(id=cid, label=label) for cid, label in concepts),
            ),
        ),
    )


class _Anchor:
    def __init__(self, node_id: str, vector: list[float] | None, node_kind: str = "concept"):
        self.node_id = node_id
        self.node_kind = node_kind
        self.label = node_id
        self.vector = vector


@pytest.fixture
def env(monkeypatch):
    """導出の入力をすべて差し替える fake 環境。"""
    state: dict = {
        "skeletons": {},
        "domains": [],
        "aliases": {},
        "anchors": {},
        "entries": [],
        "entry_vectors": {},
        "labels": {},
        "links": [],
        "created_links": [],
        "created_entries": [],
        "entries_by_candidate_key": {},
    }

    monkeypatch.setattr(
        atlas_links.atlas_store,
        "load_frozen_skeleton",
        lambda session, domain: state["skeletons"].get(domain),
    )
    monkeypatch.setattr(
        atlas_links.atlas_store, "list_domains", lambda session: list(state["domains"])
    )
    monkeypatch.setattr(
        atlas_links, "confirmed_aliases_by_node",
        lambda session, domain: dict(state["aliases"].get(domain, {})),
    )
    monkeypatch.setattr(
        atlas_links, "load_anchor_vectors",
        lambda session, domain, version: list(state["anchors"].get(domain, [])),
    )
    monkeypatch.setattr(
        atlas_links, "load_confirmed_entries", lambda session: list(state["entries"])
    )
    monkeypatch.setattr(
        atlas_links, "load_entry_vectors", lambda session: dict(state["entry_vectors"])
    )
    monkeypatch.setattr(
        atlas_links.registry, "labels_for_entries",
        lambda entry_ids, include_hidden=True, session=None: {
            eid: state["labels"].get(eid, []) for eid in entry_ids
        },
    )
    monkeypatch.setattr(
        atlas_links.registry, "list_node_links",
        lambda include_dismissed=False, session=None, **kwargs: list(state["links"]),
    )

    def _create_node_link(**kwargs):
        link = {
            "id": f"link-{len(state['created_links'])}",
            "link_key": library_schema.build_node_link_key(
                kwargs["entry_id"], kwargs["domain_key"], kwargs["node_id"]
            ),
            "entry_id": kwargs["entry_id"],
            "domain_key": kwargs["domain_key"],
            "node_id": kwargs["node_id"],
            "node_kind": kwargs.get("node_kind", "concept"),
            "kind": kwargs["kind"],
            "status": "candidate",
            "mapping_justification": kwargs["mapping_justification"],
            "reason": kwargs.get("reason", ""),
            "evidence": [],
            "review_note": "",
            "created_by": None,
            "decided_by": None,
            "decided_at": None,
            "created_at": "",
            "updated_at": "",
        }
        state["created_links"].append(kwargs)
        return link

    monkeypatch.setattr(atlas_links.registry, "create_node_link", _create_node_link)

    from core.library import store as library_store

    def _get_entry_by_candidate_key(key):
        return state["entries_by_candidate_key"].get(key)

    def _create_entry(**kwargs):
        entry = {
            "id": f"entry-{len(state['created_entries'])}",
            "name": kwargs["name"],
            "domain_key": kwargs["domain_key"],
            "entry_type": kwargs["entry_type"],
            "review_status": kwargs["review_status"],
            "candidate_key": kwargs.get("candidate_key"),
            "mapping_justification": kwargs.get("mapping_justification"),
        }
        state["created_entries"].append(kwargs)
        state["entries_by_candidate_key"][kwargs.get("candidate_key")] = entry
        return entry

    monkeypatch.setattr(library_store, "get_entry_by_candidate_key", _get_entry_by_candidate_key)
    monkeypatch.setattr(library_store, "create_entry", _create_entry)
    return state


def _entry(entry_id: str, name: str, domain: str = "astro") -> dict:
    return {"id": entry_id, "domain_key": domain, "entry_type": "concept", "name": name}


# ---------------------------------------------------------------------------
# 1. 骨格が無い / 概念が無い
# ---------------------------------------------------------------------------


class TestSkeletonPreconditions:
    def test_missing_frozen_skeleton_raises(self, env):
        with pytest.raises(atlas_links.SkeletonUnavailableError):
            atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")

    def test_empty_domain_key_raises(self, env):
        with pytest.raises(atlas_links.SkeletonUnavailableError):
            atlas_links.derive_node_link_candidates(_SESSION, domain_key="  ")

    def test_skeleton_without_concepts_returns_a_fact(self, env):
        env["skeletons"]["astro"] = AtlasSkeleton(cartridge="astro", status="frozen", version="v1")
        result = atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        assert result["candidates"] == []
        assert result["skeleton_version"] == "v1"
        assert any("照合できる概念がありません" in fact for fact in result["facts"])


# ---------------------------------------------------------------------------
# 2. 語彙一致 / ベクトル近傍
# ---------------------------------------------------------------------------


class TestLexicalAndVectorMatching:
    def test_label_match_creates_an_exact_match_candidate(self, env):
        env["skeletons"]["astro"] = _skeleton("astro", "v1", [("c1", "Cosmic Web")])
        env["entries"] = [_entry("e1", "cosmic  web")]
        result = atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        assert len(result["candidates"]) == 1
        candidate = result["candidates"][0]
        assert candidate["kind"] == library_schema.RELATION_KIND_EXACT_MATCH
        assert candidate["mapping_justification"] == library_schema.JUSTIFICATION_LEXICAL
        assert candidate["node_label"] == "Cosmic Web"

    def test_confirmed_alias_is_used_for_matching(self, env):
        env["skeletons"]["astro"] = _skeleton("astro", "v1", [("c1", "大規模構造")])
        env["aliases"]["astro"] = {"c1": ["Large scale structure"]}
        env["entries"] = [_entry("e1", "large scale structure")]
        result = atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        assert [c["kind"] for c in result["candidates"]] == [
            library_schema.RELATION_KIND_EXACT_MATCH
        ]

    def test_hidden_label_is_used_for_matching(self, env):
        """SKOS hiddenLabel は検索（照合）に使うが表示しない（KR7）。"""
        env["skeletons"]["astro"] = _skeleton("astro", "v1", [("c1", "Cosmic Web")])
        env["entries"] = [_entry("e1", "宇宙の大規模構造")]
        env["labels"]["e1"] = [
            {"kind": "hidden", "label": "Cosmic Web", "normalized_label": "cosmic web"}
        ]
        result = atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        assert len(result["candidates"]) == 1

    def test_vector_neighbour_creates_a_close_match_candidate(self, env):
        env["skeletons"]["astro"] = _skeleton("astro", "v1", [("c1", "Cosmic Web")])
        env["anchors"]["astro"] = [_Anchor("c1", [1.0, 0.0])]
        env["entries"] = [_entry("e1", "まったく別の名前")]
        env["entry_vectors"] = {"e1": [1.0, 0.0]}
        result = atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        candidate = result["candidates"][0]
        assert candidate["kind"] == library_schema.RELATION_KIND_CLOSE_MATCH
        assert candidate["mapping_justification"] == library_schema.JUSTIFICATION_VECTOR
        assert candidate["nearness_label"] == "かなり近い"

    def test_distant_vectors_do_not_create_candidates(self, env):
        env["skeletons"]["astro"] = _skeleton("astro", "v1", [("c1", "Cosmic Web")])
        env["anchors"]["astro"] = [_Anchor("c1", [1.0, 0.0])]
        env["entries"] = [_entry("e1", "まったく別の名前")]
        env["entry_vectors"] = {"e1": [0.0, 1.0]}
        result = atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        assert result["candidates"] == []
        assert any("新しい対応の候補はありません" in f for f in result["facts"])

    def test_lexical_wins_over_vector_for_the_same_pair(self, env):
        env["skeletons"]["astro"] = _skeleton("astro", "v1", [("c1", "Cosmic Web")])
        env["anchors"]["astro"] = [_Anchor("c1", [1.0, 0.0])]
        env["entries"] = [_entry("e1", "Cosmic Web")]
        env["entry_vectors"] = {"e1": [1.0, 0.0]}
        result = atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["kind"] == library_schema.RELATION_KIND_EXACT_MATCH

    def test_missing_anchor_index_is_stated_as_a_fact(self, env):
        env["skeletons"]["astro"] = _skeleton("astro", "v1", [("c1", "Cosmic Web")])
        env["entries"] = [_entry("e1", "別の名前")]
        result = atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        assert any("ベクトル索引がまだ作られていない" in f for f in result["facts"])


# ---------------------------------------------------------------------------
# 3. 却下済みの候補は再提案しない（LS3）
# ---------------------------------------------------------------------------


class TestDismissedIsNotReproposed:
    def test_dismissed_link_is_skipped(self, env):
        env["skeletons"]["astro"] = _skeleton("astro", "v1", [("c1", "Cosmic Web")])
        env["entries"] = [_entry("e1", "Cosmic Web")]
        env["links"] = [
            {
                "link_key": library_schema.build_node_link_key("e1", "astro", "c1"),
                "domain_key": "astro",
                "node_id": "c1",
                "status": "dismissed",
            }
        ]
        result = atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        assert result["candidates"] == []
        assert env["created_links"] == []

    def test_dismissed_candidate_entry_blocks_the_twin(self, env):
        env["skeletons"]["astro"] = _skeleton("astro", "v1", [("c1", "Cosmic Web")])
        env["skeletons"]["cosmo"] = _skeleton("cosmo", "v3", [("k1", "Cosmic Web")])
        env["domains"] = [
            {"domain_key": "cosmo", "frozen_version": "v3", "lifecycle": "active"},
        ]
        env["entries_by_candidate_key"][
            library_schema.build_candidate_key("astro", "Cosmic Web")
        ] = {"id": "e-old", "review_status": "dismissed"}
        result = atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        assert result["candidates"] == []
        assert env["created_entries"] == []


# ---------------------------------------------------------------------------
# 4. ドメイン跨ぎの双子（§6.1 の 3）
# ---------------------------------------------------------------------------


class TestCrossDomainTwins:
    def test_twin_creates_one_entry_and_two_links(self, env):
        env["skeletons"]["astro"] = _skeleton("astro", "v1", [("c1", "Cosmic Web")])
        env["skeletons"]["cosmo"] = _skeleton("cosmo", "v3", [("k1", "cosmic web")])
        env["domains"] = [
            {"domain_key": "astro", "frozen_version": "v1", "lifecycle": "active"},
            {"domain_key": "cosmo", "frozen_version": "v3", "lifecycle": "active"},
        ]
        result = atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        assert len(env["created_entries"]) == 1
        created = env["created_entries"][0]
        assert created["review_status"] == library_schema.REVIEW_STATUS_CANDIDATE
        assert created["entry_type"] == library_schema.ENTRY_TYPE_CONCEPT
        assert len(env["created_links"]) == 2
        assert {link["domain_key"] for link in env["created_links"]} == {"astro", "cosmo"}
        assert len(result["candidates"]) == 2
        assert any("別の分野の地図と共通しそうな概念" in f for f in result["facts"])

    def test_retired_domains_are_excluded(self, env):
        env["skeletons"]["astro"] = _skeleton("astro", "v1", [("c1", "Cosmic Web")])
        env["skeletons"]["cosmo"] = _skeleton("cosmo", "v3", [("k1", "cosmic web")])
        env["domains"] = [
            {"domain_key": "cosmo", "frozen_version": "v3", "lifecycle": "retired"},
        ]
        atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        assert env["created_entries"] == []

    def test_already_linked_nodes_are_not_twinned(self, env):
        env["skeletons"]["astro"] = _skeleton("astro", "v1", [("c1", "Cosmic Web")])
        env["skeletons"]["cosmo"] = _skeleton("cosmo", "v3", [("k1", "cosmic web")])
        env["domains"] = [
            {"domain_key": "cosmo", "frozen_version": "v3", "lifecycle": "active"},
        ]
        env["links"] = [
            {
                "link_key": library_schema.build_node_link_key("e9", "astro", "c1"),
                "domain_key": "astro",
                "node_id": "c1",
                "status": "candidate",
            }
        ]
        atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        assert env["created_entries"] == []


# ---------------------------------------------------------------------------
# 5. 数値を見せない（KR6）
# ---------------------------------------------------------------------------


class TestNoNumbersLeak:
    def test_candidates_carry_a_label_not_a_score(self, env):
        env["skeletons"]["astro"] = _skeleton("astro", "v1", [("c1", "Cosmic Web")])
        env["anchors"]["astro"] = [_Anchor("c1", [1.0, 0.0])]
        env["entries"] = [_entry("e1", "別の名前")]
        env["entry_vectors"] = {"e1": [0.9, 0.1]}
        result = atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        candidate = result["candidates"][0]
        assert "confidence" not in candidate
        assert "similarity" not in candidate
        assert candidate["nearness_label"] in ("かなり近い", "近い可能性", "遠い")

    def test_facts_carry_no_digits(self, env):
        """事実文に件数を書かない（骨格の版だけは事実として書いてよい = VA8）。"""
        env["skeletons"]["astro"] = _skeleton("astro", "vX", [("c1", "Cosmic Web")])
        env["entries"] = [_entry("e1", "Cosmic Web")]
        result = atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        for fact in result["facts"]:
            assert not any(ch.isdigit() for ch in fact), fact

    def test_confidence_is_passed_to_the_db_layer_only(self, env):
        """cosine は DB 列（confidence）には渡る — 段階ラベルの再導出に要るため。"""
        env["skeletons"]["astro"] = _skeleton("astro", "v1", [("c1", "Cosmic Web")])
        env["anchors"]["astro"] = [_Anchor("c1", [1.0, 0.0])]
        env["entries"] = [_entry("e1", "別の名前")]
        env["entry_vectors"] = {"e1": [1.0, 0.0]}
        atlas_links.derive_node_link_candidates(_SESSION, domain_key="astro")
        assert env["created_links"][0]["confidence"] == pytest.approx(1.0)
