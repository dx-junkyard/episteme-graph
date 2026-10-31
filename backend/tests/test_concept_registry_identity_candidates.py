"""同一性候補の自動生成（概念レジストリ P3-6・§6.2）。

fake session（SQL 文字列でディスパッチする最小実装）で、``core/library/identity_candidates.py``
の**導出規則**だけを見る。DB にも LLM にも embedding API にも触らない。

固定するもの:

- ① 既存 confirmed entry への語彙一致 → 同一性リンク候補 1 本（``lexical_match``）
- ② 他 document の live 親 component との語彙一致 → candidate entry 1 行 + リンク 2 本
- ③ chunk-proxy ベクトル近傍 → candidate entry + リンク 2 本（``vector_similarity``）
- ④ 接地済み主張（``theory_claims_live.concepts`` に ``entry_id``）→ 主張 → エントリの
  リンク候補（``claim_concept_grounding_design.md`` §7。entry は作らない・上限は合算）
- 上限（``IDENTITY_CANDIDATES_MAX_PER_DOCUMENT``）と ``coverage`` の正直な報告
- 冪等（``dismissed`` の候補は再提案しない）
- **基表 ``theory_components`` を FROM しない**（KO5。読むのは live ビュー）
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

from core.library import identity_candidates as ic  # noqa: E402
from core.library import schema as library_schema  # noqa: E402

DOC = "11111111-1111-1111-1111-111111111111"
OTHER_DOC = "22222222-2222-2222-2222-222222222222"


def _component(cid: str, name: str, document_id: str, *, chunk: str = "", chunks=None) -> tuple:
    return (cid, document_id, name, "concept", chunk, list(chunks or []))


class FakeSession:
    """SQL のキーワードで返す行を切り替える最小セッション。"""

    def __init__(
        self, *, parents=(), others=(), entries=(), linked=(), neighbours=(),
        cartridge="", claims=(), linked_claims=(),
        documents=(), material_claims=(), equations=(),
        proxy_only=(), symbols=(),
    ):
        self.parents = list(parents)
        self.others = list(others)
        self.entries = list(entries)
        self.linked = list(linked)
        self.neighbours = list(neighbours)
        self.cartridge = cartridge
        self.claims = list(claims)
        self.linked_claims = list(linked_claims)
        # IK-0919: 文書の素性・根拠の主張・式（名前によらない材料）。
        self.documents = list(documents)
        self.material_claims = list(material_claims)
        self.equations = list(equations)
        # 第 5 波（IK-0980 / IK-0983）: 未決の chunk-proxy 候補だけの部品・記号の行。
        self.proxy_only = list(proxy_only)
        self.symbols = list(symbols)
        self.statements: list[str] = []
        self.params: list = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        self.params.append(params)
        if "theory_claims_live" in sql and "agent_claim_id = ANY" in sql:
            refs = set((params or {}).get("refs") or [])
            rows = [r for r in self.material_claims if r[0] in refs or r[2] in refs]
        elif "FROM documents" in sql:
            rows = self.documents
        elif "knowledge_equations_live" in sql:
            rows = self.equations
        elif "theory_components_live" in sql and "document_id <> " in sql:
            rows = self.others
        elif "theory_components_live" in sql:
            rows = self.parents
        elif "FROM library_entries" in sql:
            rows = self.entries
        elif "bool_and" in sql:
            rows = [(cid,) for cid in self.proxy_only]
        elif "knowledge_symbols_live" in sql:
            rows = self.symbols
        elif "FROM element_identity_links" in sql and "shared_part_id" in sql:
            rows = list(self.linked_claims)
        elif "FROM element_identity_links" in sql:
            rows = [(cid,) for cid in self.linked]
        elif "theory_claims_live" in sql:
            rows = self.claims
        elif "FROM chunks c" in sql:
            rows = self.neighbours
        elif "document_analysis_runs" in sql:
            rows = [(self.cartridge,)] if self.cartridge else []
        else:
            rows = []
        return _FakeResult(rows)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


@pytest.fixture
def env(monkeypatch):
    state: dict = {
        "created_entries": [],
        "created_links": [],
        "entries_by_candidate_key": {},
        "duplicate_writes": [],
        "audits": [],
        "labels": {},
        "domain_keys": [],
    }

    monkeypatch.setattr(
        ic.registry, "labels_for_entries",
        lambda entry_ids, include_hidden=True, session=None: {
            eid: state["labels"].get(eid, []) for eid in entry_ids
        },
    )
    monkeypatch.setattr(
        ic._corpus, "document_domain_keys", lambda session, ref: list(state["domain_keys"])
    )

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
        }
        state["created_entries"].append(kwargs)
        state["entries_by_candidate_key"][kwargs.get("candidate_key")] = entry
        return entry

    monkeypatch.setattr(ic.library_store, "get_entry_by_candidate_key", _get_entry_by_candidate_key)
    monkeypatch.setattr(ic.library_store, "create_entry", _create_entry)

    def _create_candidate(ref, shared_part_id, **kwargs):
        record = {
            "ref": ref,
            "shared_part_id": shared_part_id,
            **kwargs,
        }
        state["created_links"].append(record)
        return {"id": f"link-{len(state['created_links'])}", "status": "candidate"}

    monkeypatch.setattr(ic._identity_links, "create_candidate", _create_candidate)

    from core.document_pipeline import persistence

    monkeypatch.setattr(
        persistence, "set_duplicate_candidates",
        lambda session, component_id, items: state["duplicate_writes"].append((component_id, items)),
    )
    monkeypatch.setattr(
        persistence, "record_knowledge_audit",
        lambda session, **kwargs: state["audits"].append(kwargs),
    )
    return state


# ---------------------------------------------------------------------------
# 0. 前提（対象ゼロ・上限ゼロ）
# ---------------------------------------------------------------------------


class TestPreconditions:
    def test_no_parent_components_is_a_normal_state(self, env):
        session = FakeSession()
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["entries_created"] == 0
        assert result["links_created"] == 0
        assert result["coverage"]["population"] == 0
        assert env["created_links"] == []

    def test_limit_zero_stops_generation_and_says_so(self, env, monkeypatch):
        from core import config

        monkeypatch.setattr(
            config, "get_settings",
            lambda: type("S", (), {"identity_candidates_max_per_document": 0})(),
        )
        monkeypatch.setattr(ic, "get_settings", config.get_settings)
        session = FakeSession(parents=[_component("c1", "Form factor", DOC)])
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 0
        assert "candidate_limit_is_zero" in result["coverage"]["reasons"]


# ---------------------------------------------------------------------------
# 1. 既存 entry への語彙一致
# ---------------------------------------------------------------------------


class TestLexicalMatchToExistingEntry:
    def test_creates_one_identity_link(self, env):
        session = FakeSession(
            parents=[_component("c1", "Form  Factor", DOC)],
            entries=[("e1", "flavour", "concept", "form factor")],
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 1
        assert env["created_entries"] == []
        link = env["created_links"][0]
        assert link["shared_part_id"] == "e1"
        assert link["mapping_justification"] == library_schema.JUSTIFICATION_LEXICAL
        assert link["ref"].element_type == "theory_component"
        assert link["ref"].document_id == DOC

    def test_alternate_label_matches_too(self, env):
        session = FakeSession(
            parents=[_component("c1", "FF", DOC)],
            entries=[("e1", "flavour", "concept", "Form factor")],
        )
        env["labels"]["e1"] = [
            {"kind": "alternate", "label": "FF", "normalized_label": "ff"}
        ]
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 1

    def test_writes_duplicate_candidates_without_numbers(self, env):
        session = FakeSession(
            parents=[_component("c1", "Form factor", DOC)],
            entries=[("e1", "flavour", "concept", "Form factor")],
        )
        ic.run_identity_candidates(document_id=DOC, session=session)
        assert env["duplicate_writes"]
        _component_id, items = env["duplicate_writes"][0]
        assert set(items[0]) == {
            "component_id", "document_id", "entry_id", "mapping_justification",
        }


# ---------------------------------------------------------------------------
# 2. 他 document の component との語彙一致
# ---------------------------------------------------------------------------


class TestLexicalMatchAcrossDocuments:
    def test_creates_a_candidate_entry_and_two_links(self, env):
        session = FakeSession(
            parents=[_component("c1", "Zero recoil limit", DOC)],
            others=[_component("c2", "zero recoil limit", OTHER_DOC)],
        )
        env["domain_keys"] = ["flavour"]
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["entries_created"] == 1
        assert result["links_created"] == 2
        created = env["created_entries"][0]
        assert created["review_status"] == library_schema.REVIEW_STATUS_CANDIDATE
        assert created["domain_key"] == "flavour"
        assert created["source_document_ids"] == [DOC, OTHER_DOC]
        assert {link["shared_part_id"] for link in env["created_links"]} == {"entry-0"}

    def test_already_linked_components_are_skipped(self, env):
        session = FakeSession(
            parents=[_component("c1", "Zero recoil limit", DOC)],
            others=[_component("c2", "zero recoil limit", OTHER_DOC)],
            linked=["c1"],
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["entries_created"] == 0
        assert result["links_created"] == 0

    def test_dismissed_candidate_entry_is_not_reproposed(self, env):
        session = FakeSession(
            parents=[_component("c1", "Zero recoil limit", DOC)],
            others=[_component("c2", "zero recoil limit", OTHER_DOC)],
        )
        env["domain_keys"] = ["flavour"]
        env["entries_by_candidate_key"][
            library_schema.build_candidate_key("flavour", "Zero recoil limit")
        ] = {"id": "old", "review_status": library_schema.REVIEW_STATUS_DISMISSED}
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["entries_created"] == 0
        assert result["links_created"] == 0

    def test_existing_candidate_entry_is_reused(self, env):
        session = FakeSession(
            parents=[_component("c1", "Zero recoil limit", DOC)],
            others=[_component("c2", "zero recoil limit", OTHER_DOC)],
        )
        env["domain_keys"] = ["flavour"]
        env["entries_by_candidate_key"][
            library_schema.build_candidate_key("flavour", "Zero recoil limit")
        ] = {"id": "old", "review_status": library_schema.REVIEW_STATUS_CANDIDATE}
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert env["created_entries"] == []
        assert result["links_created"] == 2
        assert {link["shared_part_id"] for link in env["created_links"]} == {"old"}


# ---------------------------------------------------------------------------
# 3. chunk-proxy ベクトル近傍
# ---------------------------------------------------------------------------


class TestChunkProxy:
    def test_near_chunk_creates_a_vector_similarity_candidate(self, env):
        session = FakeSession(
            parents=[_component("c1", "Slope parameter", DOC, chunk="chunk-a")],
            others=[_component("c2", "Curvature parameter", OTHER_DOC, chunk="chunk-b")],
            neighbours=[("chunk-b", 0.82)],
        )
        env["domain_keys"] = ["flavour"]
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["entries_created"] == 1
        assert result["links_created"] == 2
        assert env["created_entries"][0]["mapping_justification"] == (
            library_schema.JUSTIFICATION_VECTOR
        )

    def test_below_threshold_is_not_a_candidate(self, env):
        session = FakeSession(
            parents=[_component("c1", "Slope parameter", DOC, chunk="chunk-a")],
            others=[_component("c2", "Curvature parameter", OTHER_DOC, chunk="chunk-b")],
            neighbours=[("chunk-b", 0.10)],
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["entries_created"] == 0
        assert result["links_created"] == 0

    def test_source_chunks_also_resolve_the_twin(self, env):
        session = FakeSession(
            parents=[_component("c1", "Slope parameter", DOC, chunk="chunk-a")],
            others=[
                _component("c2", "Curvature", OTHER_DOC, chunk="", chunks=["chunk-b"])
            ],
            neighbours=[("chunk-b", 0.95)],
        )
        env["domain_keys"] = ["flavour"]
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 2

    def test_component_without_a_primary_chunk_is_skipped(self, env):
        session = FakeSession(
            parents=[_component("c1", "Slope parameter", DOC)],
            others=[_component("c2", "Curvature", OTHER_DOC, chunk="chunk-b")],
            neighbours=[("chunk-b", 0.99)],
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 0


# ---------------------------------------------------------------------------
# 4. 分野の解決 / 上限 / coverage
# ---------------------------------------------------------------------------


class TestDomainResolutionAndCoverage:
    def test_falls_back_to_the_run_cartridge(self, env):
        session = FakeSession(
            parents=[_component("c1", "Zero recoil", DOC)],
            others=[_component("c2", "zero recoil", OTHER_DOC)],
            cartridge="particle_physics",
        )
        ic.run_identity_candidates(document_id=DOC, run_id="run-1", session=session)
        assert env["created_entries"][0]["domain_key"] == "particle_physics"

    def test_falls_back_to_unassigned(self, env):
        session = FakeSession(
            parents=[_component("c1", "Zero recoil", DOC)],
            others=[_component("c2", "zero recoil", OTHER_DOC)],
        )
        ic.run_identity_candidates(document_id=DOC, session=session)
        assert env["created_entries"][0]["domain_key"] == (
            library_schema.DOMAIN_KEY_UNASSIGNED
        )

    def test_limit_is_reported_in_coverage(self, env, monkeypatch):
        monkeypatch.setattr(
            ic, "get_settings",
            lambda: type("S", (), {"identity_candidates_max_per_document": 1})(),
        )
        parents = [
            _component("c1", "Alpha", DOC),
            _component("c2", "Beta", DOC),
            _component("c3", "Gamma", DOC),
        ]
        session = FakeSession(
            parents=parents,
            entries=[
                ("e1", "flavour", "concept", "Alpha"),
                ("e2", "flavour", "concept", "Beta"),
            ],
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["coverage"]["population"] == 3
        assert result["coverage"]["truncated"] > 0
        assert "candidate_limit" in result["coverage"]["reasons"]

    def test_audit_is_recorded_once_per_run(self, env):
        session = FakeSession(
            parents=[_component("c1", "Form factor", DOC)],
            entries=[("e1", "flavour", "concept", "Form factor")],
        )
        ic.run_identity_candidates(document_id=DOC, run_id="run-9", session=session)
        assert len(env["audits"]) == 1
        stats = env["audits"][0]["stats"]["identity_candidates"]
        # 規則 ⑤（theory_module_layer_design.md §13.8）の件数は同じ 1 行の modules に入る。
        assert set(stats) == {"entries", "links", "components", "modules"}
        assert set(stats["modules"]) == {"entries", "links"}


# ---------------------------------------------------------------------------
# 5. KO5: 基表を読まない
# ---------------------------------------------------------------------------


class TestReadsLiveViewsOnly:
    def test_queries_never_select_the_base_table(self, env):
        session = FakeSession(
            parents=[_component("c1", "Form factor", DOC)],
            entries=[("e1", "flavour", "concept", "Form factor")],
        )
        ic.run_identity_candidates(document_id=DOC, session=session)
        for sql in session.statements:
            assert "FROM theory_components\n" not in sql
            assert "JOIN theory_components " not in sql
        assert any("theory_components_live" in sql for sql in session.statements)

    def test_module_does_not_import_the_llm_layer(self):
        source = (BACKEND / "core" / "library" / "identity_candidates.py").read_text(
            encoding="utf-8"
        )
        assert "import core.llm" not in source
        assert "from core.llm" not in source
        assert "generate_embeddings(" not in source
        assert "embed_with_context(" not in source


# ---------------------------------------------------------------------------
# 6. 規則 ④: 接地済み主張 → 確定済みエントリ（claim_concept_grounding_design.md §7）
# ---------------------------------------------------------------------------


CLAIM = "33333333-3333-3333-3333-333333333333"


def _claim(cid: str, text: str, concepts: list) -> tuple:
    return (cid, text, concepts)


class TestGroundedClaimLinks:
    def test_entry_id_on_a_concept_creates_a_claim_link(self, env):
        session = FakeSession(
            entries=[("e1", "flavour", "concept", "Form factor")],
            claims=[
                _claim(
                    CLAIM,
                    "The form factor is normalised at zero recoil.",
                    [
                        {
                            "name": "Form factor",
                            "normalized": "form factor",
                            "entry_id": "e1",
                            "mapping_justification": "lexical_match",
                        }
                    ],
                )
            ],
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 1
        assert env["created_entries"] == [], "規則 ④ は entry を作らない"
        link = env["created_links"][0]
        assert link["ref"].element_type == "theory_claim"
        assert link["ref"].element_id == CLAIM
        assert link["ref"].document_id == DOC
        assert link["shared_part_id"] == "e1"
        assert link["local_expression"] == {"name": "Form factor"}
        assert link["mapping_justification"] == library_schema.JUSTIFICATION_LEXICAL
        assert link["evidence"] == [
            {"claim_text": "The form factor is normalised at zero recoil."}
        ]

    def test_evidence_is_truncated(self, env):
        session = FakeSession(
            entries=[("e1", "flavour", "concept", "Form factor")],
            claims=[
                _claim(
                    CLAIM, "x" * 500,
                    [{"name": "Form factor", "normalized": "form factor", "entry_id": "e1"}],
                )
            ],
        )
        ic.run_identity_candidates(document_id=DOC, session=session)
        excerpt = env["created_links"][0]["evidence"][0]["claim_text"]
        assert len(excerpt) == ic.CLAIM_EVIDENCE_MAX_CHARS

    def test_llm_candidate_justification_is_kept(self, env):
        session = FakeSession(
            entries=[("e1", "flavour", "concept", "Form factor")],
            claims=[
                _claim(
                    CLAIM, "A bound is derived.",
                    [
                        {
                            "name": "Form factor",
                            "normalized": "form factor",
                            "entry_id": "e1",
                            "mapping_justification": "llm_candidate",
                        }
                    ],
                )
            ],
        )
        ic.run_identity_candidates(document_id=DOC, session=session)
        assert env["created_links"][0]["mapping_justification"] == (
            library_schema.JUSTIFICATION_LLM
        )

    def test_concepts_without_entry_id_are_ignored(self, env):
        session = FakeSession(
            claims=[
                _claim(CLAIM, "R_D is measured.", [{"name": "R_D", "normalized": "R_D"}])
            ],
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 0
        assert env["created_links"] == []

    def test_unknown_or_retired_entries_are_not_linked(self, env):
        """確定・公開中でないエントリ（``entries`` に出てこない）は指さない。"""
        session = FakeSession(
            claims=[
                _claim(
                    CLAIM, "The form factor.",
                    [{"name": "Form factor", "normalized": "form factor", "entry_id": "gone"}],
                )
            ],
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 0

    def test_existing_pairs_are_not_reproposed(self, env):
        session = FakeSession(
            entries=[("e1", "flavour", "concept", "Form factor")],
            claims=[
                _claim(
                    CLAIM, "The form factor.",
                    [{"name": "Form factor", "normalized": "form factor", "entry_id": "e1"}],
                )
            ],
            linked_claims=[(CLAIM, "e1")],
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 0
        assert env["created_links"] == []

    def test_the_limit_is_shared_with_the_component_rules(self, env, monkeypatch):
        monkeypatch.setattr(
            ic, "get_settings",
            lambda: type("S", (), {"identity_candidates_max_per_document": 1})(),
        )
        session = FakeSession(
            parents=[_component("c1", "Form factor", DOC)],
            entries=[("e1", "flavour", "concept", "Form factor")],
            claims=[
                _claim(
                    CLAIM, "The form factor.",
                    [{"name": "Form factor", "normalized": "form factor", "entry_id": "e1"}],
                )
            ],
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        # component 側（規則 ①）で 1 本使い切るので、主張側は 1 本も作らない。
        assert result["links_created"] == 1
        assert [link["ref"].element_type for link in env["created_links"]] == [
            "theory_component"
        ]

    def test_claims_are_read_from_the_live_view(self, env):
        session = FakeSession(
            entries=[("e1", "flavour", "concept", "Form factor")],
            claims=[
                _claim(
                    CLAIM, "The form factor.",
                    [{"name": "Form factor", "normalized": "form factor", "entry_id": "e1"}],
                )
            ],
        )
        ic.run_identity_candidates(document_id=DOC, session=session)
        assert any("theory_claims_live" in sql for sql in session.statements)
        for sql in session.statements:
            assert "FROM theory_claims\n" not in sql
            assert "JOIN theory_claims " not in sql


# ---------------------------------------------------------------------------
# 7. IK-0919: 名前によらない材料（同じ論文の別の版・根拠の主張のチャンク）
# ---------------------------------------------------------------------------

PDF_DOC = "44444444-4444-4444-4444-444444444444"
TITLE = "Physics-informed neural networks for neutron star structure"


def _rich_component(
    cid: str, name: str, document_id: str, *, evidence_claims=(), linked_claims=(),
    inputs=(), equations=(), chunk: str = "",
) -> tuple:
    return (
        cid, document_id, name, "concept", chunk, [],
        list(evidence_claims), list(linked_claims), list(inputs), [], list(equations),
    )


def _doc(did: str, title: str, *, filename="", url="", path="") -> tuple:
    return (did, title, filename, url, path)


def _mclaim(cid: str, doc: str, agent: str, *, chunk: str = "", text: str = "") -> tuple:
    return (cid, doc, agent, chunk, text, text)


LONG_CLAIM = "The network maps the equation of state to the stellar structure"
LONG_EQ = r"\frac{dP}{dr} = -\frac{G m \rho}{r^2}"


class TestSamePaperVersions:
    def _base(self, *, docs, eq_pdf=LONG_EQ, claim_pdf=LONG_CLAIM):
        return FakeSession(
            parents=[
                _rich_component(
                    "tex1", "TOV network map", DOC,
                    evidence_claims=["claim-tex"], equations=["eq_tex_3"],
                )
            ],
            others=[
                _rich_component(
                    "pdf1", "Structure network map", PDF_DOC,
                    linked_claims=["c_12"], equations=["eq_blk_7"],
                )
            ],
            documents=docs,
            material_claims=[
                _mclaim("claim-tex", DOC, "c_1", text=LONG_CLAIM),
                _mclaim("claim-pdf", PDF_DOC, "c_12", text=claim_pdf),
            ],
            equations=[
                (DOC, "eq_tex_3", "$" + LONG_EQ + "$", ""),
                (PDF_DOC, "eq_blk_7", eq_pdf.replace(" ", "  "), ""),
            ],
        )

    def test_title_match_pairs_components_with_different_names(self, env):
        session = self._base(docs=[_doc(DOC, TITLE), _doc(PDF_DOC, TITLE.upper() + ".")])
        env["domain_keys"] = ["astrophysics"]
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["entries_created"] == 1
        assert result["links_created"] == 2
        assert {link["ref"].element_id for link in env["created_links"]} == {"tex1", "pdf1"}
        for link in env["created_links"]:
            assert link["mapping_justification"] == library_schema.JUSTIFICATION_LEXICAL
            # IK-0981: 種類の文に続けて「何が一致したか」（式の表示ラベル・主張の冒頭）を書く。
            assert link["reason"].startswith(ic.SAME_PAPER_REASON_BOTH + ic.SAME_PAPER_MATCHED_PREFIX)
            assert "主張「" in link["reason"]
            # 相手の名前・数値を事実文に焼き込まない（KR6 / KR10）— 種類の文には数字が無い。
            assert "Structure" not in link["reason"] and "TOV" not in link["reason"]
            assert not any(ch.isdigit() for ch in ic.SAME_PAPER_REASON_BOTH)
            # IK-0981: 版の規則の対の印（一覧が「表記の一致」と呼ばないため）。
            assert link["evidence"][0]["basis"] == ic.SAME_PAPER_BASIS
        assert env["created_entries"][0]["mapping_justification"] == (
            library_schema.JUSTIFICATION_LEXICAL
        )

    def test_arxiv_id_match_pairs_components(self, env):
        session = self._base(
            docs=[
                _doc(DOC, "TeX upload", url="https://arxiv.org/src/2407.01221v2"),
                _doc(PDF_DOC, "Some PDF", filename="2407.01221v1.pdf"),
            ]
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 2

    def test_equation_alone_is_enough(self, env):
        session = self._base(
            docs=[_doc(DOC, TITLE), _doc(PDF_DOC, TITLE)], claim_pdf="unrelated short"
        )
        ic.run_identity_candidates(document_id=DOC, session=session)
        assert env["created_links"]
        assert env["created_links"][0]["reason"].startswith(ic.SAME_PAPER_REASON_EQUATION)

    def test_different_papers_are_not_paired(self, env):
        session = self._base(
            docs=[
                _doc(DOC, TITLE, url="https://arxiv.org/abs/2407.01221"),
                _doc(PDF_DOC, TITLE, url="https://arxiv.org/abs/2501.00001"),
            ]
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 0

    def test_unrelated_titles_are_not_paired(self, env):
        session = self._base(docs=[_doc(DOC, TITLE), _doc(PDF_DOC, "A different paper title")])
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 0

    def test_no_shared_material_is_not_paired(self, env):
        session = self._base(
            docs=[_doc(DOC, TITLE), _doc(PDF_DOC, TITLE)],
            eq_pdf=r"E = m c^2 + \\Lambda",
            claim_pdf="A completely different statement about something else",
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 0

    def test_already_linked_twin_is_skipped(self, env):
        session = self._base(docs=[_doc(DOC, TITLE), _doc(PDF_DOC, TITLE)])
        session.linked = ["pdf1"]
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 0

    def test_no_embedding_or_llm_is_called(self, env, monkeypatch):
        import core.llm as llm

        def _boom(*_a, **_k):
            raise AssertionError("LLM / embedding must not be called")

        for name in ("generate_embeddings", "generate_text", "generate_text_with_structured_output"):
            if hasattr(llm, name):
                monkeypatch.setattr(llm, name, _boom)
        session = self._base(docs=[_doc(DOC, TITLE), _doc(PDF_DOC, TITLE)])
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 2
        # 保存済みベクトルの近傍も引かない（規則 ②b で決まった）
        assert not any("FROM chunks c" in sql for sql in session.statements)

    def test_reads_live_views_only(self, env):
        session = self._base(docs=[_doc(DOC, TITLE), _doc(PDF_DOC, TITLE)])
        ic.run_identity_candidates(document_id=DOC, session=session)
        joined = "\n".join(session.statements)
        assert "knowledge_equations_live" in joined
        assert "FROM theory_claims\n" not in joined
        assert "FROM knowledge_equations\n" not in joined


class TestClaimChunkOrigins:
    def test_claim_chunk_starts_the_chunk_proxy_rule(self, env):
        session = FakeSession(
            parents=[
                _rich_component("c1", "Slope parameter", DOC, evidence_claims=["cl-a"])
            ],
            others=[
                _rich_component("c2", "Curvature", OTHER_DOC, linked_claims=["c_9"])
            ],
            material_claims=[
                _mclaim("cl-a", DOC, "c_1", chunk="chunk-a"),
                _mclaim("cl-b", OTHER_DOC, "c_9", chunk="chunk-b"),
            ],
            neighbours=[("chunk-b", 0.9)],
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 2
        assert env["created_links"][0]["mapping_justification"] == (
            library_schema.JUSTIFICATION_VECTOR
        )
        neighbour_params = [
            p for sql, p in zip(session.statements, session.params) if "FROM chunks c" in sql
        ]
        assert neighbour_params[0]["chunk_id"] == "chunk-a"

    def test_claim_in_another_document_is_not_borrowed(self, env):
        # 参照 ID が同じでも、他 document の主張のチャンクを自分の起点にしない。
        session = FakeSession(
            parents=[_rich_component("c1", "Slope", DOC, linked_claims=["c_9"])],
            others=[_rich_component("c2", "Curvature", OTHER_DOC, linked_claims=["c_9"])],
            material_claims=[_mclaim("cl-b", OTHER_DOC, "c_9", chunk="chunk-b")],
            neighbours=[("chunk-b", 0.99)],
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 0

    def test_nested_input_claim_ids_are_followed(self):
        component = {
            "id": "c", "document_id": DOC, "evidence_claims": [], "linked_claim_ids": [],
            "inputs": [{"name": "x", "claim_ids": ["u1"]}],
            "outputs": [{"name": "y", "evidence": {"claim_id": "u2"}}],
        }
        assert ic.component_claim_refs(component) == ["u1", "u2"]


class TestSamePaperPureFunctions:
    def test_same_paper_by_title(self):
        assert ic.same_paper({"title": TITLE}, {"title": " " + TITLE.lower() + "\n"})

    def test_short_titles_do_not_match(self):
        assert not ic.same_paper({"title": "Intro"}, {"title": "intro"})

    def test_conflicting_arxiv_ids_win_over_title(self):
        assert not ic.same_paper(
            {"title": TITLE, "source_url": "https://arxiv.org/abs/2407.01221"},
            {"title": TITLE, "filename": "2501.00001v1.pdf"},
        )

    def test_note_mentions_versions_without_numbers(self):
        assert "同じ論文の別の版" in ic.SAME_PAPER_VERSIONS_NOTE
        assert not any(ch.isdigit() for ch in ic.SAME_PAPER_VERSIONS_NOTE)

    def test_empty_queue_facts_carry_the_versions_note(self):
        source = (BACKEND / "api" / "routes" / "library.py").read_text(encoding="utf-8")
        seg = source[source.index("def list_identity_candidates"):]
        seg = seg[: seg.index("return {\"candidates\"")]
        assert "SAME_PAPER_VERSIONS_NOTE" in seg


# ---------------------------------------------------------------------------
# 第 25 周 面 A 第 5 波（2026-10-27）: IK-0980 / IK-0981 / IK-0983
# ---------------------------------------------------------------------------


class TestWave5SamePaperProxy:
    """IK-0980（オーナー判断）: 同じ論文の別の版どうしでは文の近さを候補の材料にしない。"""

    def _session(self, **kw):
        return FakeSession(
            parents=[_rich_component("tex1", "Training loss system", DOC, chunk="chunk-a")],
            others=[_rich_component("pdf1", "TOV network map", PDF_DOC, chunk="chunk-b")],
            documents=[_doc(DOC, TITLE), _doc(PDF_DOC, TITLE)],
            neighbours=[("chunk-b", 0.99)],
            **kw,
        )

    def test_same_paper_versions_are_not_paired_by_chunk_proxy(self, env):
        result = ic.run_identity_candidates(document_id=DOC, session=self._session())
        assert result["links_created"] == 0
        assert env["created_links"] == []

    def test_other_papers_still_use_chunk_proxy(self, env):
        session = FakeSession(
            parents=[_rich_component("tex1", "Training loss system", DOC, chunk="chunk-a")],
            others=[_rich_component("pdf1", "TOV network map", PDF_DOC, chunk="chunk-b")],
            documents=[_doc(DOC, TITLE), _doc(PDF_DOC, "A different paper title")],
            neighbours=[("chunk-b", 0.99)],
        )
        result = ic.run_identity_candidates(document_id=DOC, session=session)
        assert result["links_created"] == 2
        assert {l["mapping_justification"] for l in env["created_links"]} == {
            library_schema.JUSTIFICATION_VECTOR
        }

    def test_rules_version_was_raised(self):
        assert ic.IDENTITY_RULES_VERSION == "r7-same-paper-symbols"


class TestWave5RebuildAfterStaleProxy:
    """IK-0980: 未決の chunk-proxy 候補しか持たない部品は、段の再実行で規則 ②b にかけ直す。"""

    def _session(self, *, proxy_only):
        return FakeSession(
            parents=[
                _rich_component(
                    "tex1", "TOV network map", DOC,
                    evidence_claims=["claim-tex"], equations=["eq_tex_3"],
                )
            ],
            others=[
                _rich_component(
                    "pdf1", "Structure network map", PDF_DOC,
                    linked_claims=["c_12"], equations=["eq_blk_7"],
                )
            ],
            documents=[_doc(DOC, TITLE), _doc(PDF_DOC, TITLE)],
            material_claims=[
                _mclaim("claim-tex", DOC, "c_1", text=LONG_CLAIM),
                _mclaim("claim-pdf", PDF_DOC, "c_12", text=LONG_CLAIM),
            ],
            equations=[
                (DOC, "eq_tex_3", "$" + LONG_EQ + "$", "", "12"),
                (PDF_DOC, "eq_blk_7", LONG_EQ, "", "12"),
            ],
            linked=["tex1", "pdf1"],
            proxy_only=proxy_only,
        )

    def test_stale_proxy_only_parts_are_rebuilt_with_a_separate_entry(self, env):
        result = ic.run_identity_candidates(
            document_id=DOC, session=self._session(proxy_only=["tex1", "pdf1"])
        )
        assert result["links_created"] == 2
        key = env["created_entries"][0]["candidate_key"]
        assert key.endswith(library_schema.KEY_SEPARATOR + ic.SAME_PAPER_REBUILD_KEY_SUFFIX)
        reason = env["created_links"][0]["reason"]
        assert "式 (12)" in reason  # IK-0981: 何が一致したかを式の表示ラベルで

    def test_parts_with_a_human_decision_are_not_rebuilt(self, env):
        result = ic.run_identity_candidates(
            document_id=DOC, session=self._session(proxy_only=[])
        )
        assert result["links_created"] == 0


class TestWave5SymbolConceptRule:
    """IK-0983: 記号の定義の文 × 確定済みエントリの名前・別名の語境界一致 → candidate。"""

    def test_symbol_definition_mentioning_entry_name_creates_a_candidate(self, env):
        from core.library import symbol_concepts as sc

        session = FakeSession(
            entries=[("e1", "astrophysics", "quantity", "stellar radius"), ("e2", "astro", "quantity", "r")],
            symbols=[(DOC, "sym_r", "r", ["where r is the stellar radius of the star"])],
        )
        stats = sc.derive_symbol_concept_candidates(session, document_ids=[DOC])
        links = [l for l in env["created_links"] if l["ref"].element_type == "symbol"]
        assert [l["shared_part_id"] for l in links] == ["e1"]  # 記号のような名前「r」は照合しない
        assert links[0]["ref"].element_id == "sym_r"
        assert links[0]["mapping_justification"] == library_schema.JUSTIFICATION_LEXICAL
        assert "stellar radius" in links[0]["reason"]
        assert stats["symbols_scanned"] == 1

    def test_no_partial_word_match(self, env):
        from core.library import symbol_concepts as sc

        session = FakeSession(
            entries=[("e1", "astrophysics", "quantity", "mass")],
            symbols=[(DOC, "sym_m", "m", ["where m is the massive body"])],
        )
        sc.derive_symbol_concept_candidates(session, document_ids=[DOC])
        assert [l for l in env["created_links"] if l["ref"].element_type == "symbol"] == []

    def test_module_calls_no_llm_or_embedding(self):
        source = (BACKEND / "core" / "library" / "symbol_concepts.py").read_text(encoding="utf-8")
        for needle in ("from core.llm", "import core.llm", "from core import llm", "generate_embeddings"):
            assert needle not in source
        assert "DELETE FROM" not in source
