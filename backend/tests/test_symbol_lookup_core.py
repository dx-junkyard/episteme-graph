"""記号の「直前の定義」の core（概念レジストリ P3-5 / ``concept_registry_design.md`` §7）。

fake session（DB へ行かない）で次を固定する:

1. **ScholarPhi 規則** — タップ位置より前で最も近い定義が選ばれる。
2. **後方フォールバック** — 前に無ければ後ろの最初の定義 + ``FACT_DEFINED_LATER``。
3. **順序が解けない** — ``FACT_POSITION_UNKNOWN`` を添えて最初の定義（黙って出さない）。
4. **定義なし** — ``FACT_NO_DEFINITION``（主語は「この論文」= KR8）。
5. **部分一致しない** — 記号の照合は ``normalize_key`` の完全一致のみ（P0-2 / F-7）。
6. **内部 ID / 数値を漏らさない** — ``learner_context_common.contains_internal_id`` で自己検査。
7. **fail-closed** — document 集合が空なら SQL を1本も発行しない。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for path in (str(BACKEND), str(BACKEND / "api")):
    if path not in sys.path:
        sys.path.insert(0, path)

from core import symbol_lookup  # noqa: E402
from core.learner_context_common import contains_internal_id  # noqa: E402
from core.symbol_lookup import (  # noqa: E402
    FACT_DEFINED_LATER,
    FACT_NO_DEFINITION,
    FACT_POSITION_UNKNOWN,
    lookup_symbol_definition,
)

DOC = "11111111-1111-4111-8111-111111111111"
OTHER_DOC = "22222222-2222-4222-8222-222222222222"
CHUNK_1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
CHUNK_2 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"


# ---------------------------------------------------------------------------
# フェイクセッション
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows=()):
        self._rows = [dict(r) for r in rows]

    def mappings(self):
        return self

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """``symbol_lookup`` が読む5つの表の最小フェイク。"""

    def __init__(
        self,
        *,
        symbols=(),
        equations=(),
        evidence=(),
        chunks=(),
        documents=(),
        identity_links=(),
    ):
        self.symbols = [dict(s) for s in symbols]
        self.equations = [dict(e) for e in equations]
        self.evidence = [dict(e) for e in evidence]
        self.chunks = [dict(c) for c in chunks]
        self.documents = [dict(d) for d in documents]
        self.identity_links = [dict(link) for link in identity_links]
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        params = params or {}
        docs = set(params.get("doc_ids") or [])

        if "knowledge_symbols_live" in sql:
            return _Result([s for s in self.symbols if s["document_id"] in docs])
        if "knowledge_equations" in sql:
            return _Result([e for e in self.equations if e["document_id"] in docs])
        if "knowledge_evidence" in sql:
            return _Result([e for e in self.evidence if e["document_id"] in docs])
        if "FROM documents" in sql:
            return _Result([d for d in self.documents if d["id"] in docs])
        if "element_identity_links" in sql:
            rows = [
                link
                for link in self.identity_links
                if link.get("instance_element_id") == params.get("symbol_id")
                and link.get("instance_document_id") == params.get("document_id")
            ]
            return _Result(rows)
        if "FROM chunks" in sql and ":chunk_id" in sql:
            return _Result(
                [c for c in self.chunks if c["id"] == params.get("chunk_id") and c["document_id"] in docs]
            )
        if "FROM chunks" in sql:
            rows = sorted(
                (c for c in self.chunks if c["document_id"] in docs),
                key=lambda c: (c["document_id"], c["chunk_index"]),
            )
            return _Result(rows)
        raise AssertionError("unexpected SQL: " + sql)

    def close(self):  # pragma: no cover - route 側でのみ呼ばれる
        pass


def _chunks():
    """本文の順（block_id の並び）を与える chunk 2件。"""
    return [
        {"id": CHUNK_1, "document_id": DOC, "chunk_index": 0, "block_ids": ["b1", "b2", "b3"]},
        {"id": CHUNK_2, "document_id": DOC, "chunk_index": 1, "block_ids": ["b4", "b5", "b6"]},
    ]


def _documents():
    return [{"id": DOC, "title": "Semileptonic B decays"}]


# ---------------------------------------------------------------------------
# 1〜3. ScholarPhi 規則
# ---------------------------------------------------------------------------


class TestScholarPhiRule:
    def _session(self):
        return FakeSession(
            symbols=[
                {
                    "document_id": DOC,
                    "agent_symbol_id": "sym_%s_F" % DOC,
                    "canonical_symbol": "F",
                    "notation_variants": ["F(w)"],
                    "kind": "function",
                    "unit": "",
                    "scope": "document",
                    "definition_status": "defined",
                    "defining_equation_ids": ["eq_1", "eq_5"],
                    "source_evidence_ids": [],
                    "definition_evidence_texts": [
                        "F is the form factor at maximum recoil.",
                        "F is redefined at zero recoil.",
                    ],
                }
            ],
            equations=[
                {"document_id": DOC, "agent_equation_id": "eq_1", "block_id": "b1", "page": 1, "label": "(1)"},
                {"document_id": DOC, "agent_equation_id": "eq_5", "block_id": "b5", "page": 2, "label": "(5)"},
                {"document_id": DOC, "agent_equation_id": "eq_3", "block_id": "b3", "page": 1, "label": "(3)"},
            ],
            evidence=[],
            chunks=_chunks(),
            documents=_documents(),
        )

    def test_picks_the_nearest_definition_before_the_tap(self):
        """b3（タップ位置）より前は b1 の定義だけ。b5 は後ろなので選ばれない。"""
        result = lookup_symbol_definition(
            self._session(), symbol="F", document_ids=[DOC], equation_id="eq_3"
        )
        assert result["available"] is True
        assert "maximum recoil" in result["definition"]["text"]
        assert result["definition"]["equation_label"] == "(1)"
        assert result["facts"] == []

    def test_falls_back_to_the_first_definition_after_the_tap(self):
        """タップ位置（b1 の式）より前に定義が無ければ、後ろの最初を事実文つきで返す。"""
        session = self._session()
        # b1 の定義を消し、後ろ（b5）だけを残す。
        session.symbols[0]["defining_equation_ids"] = ["eq_5"]
        session.symbols[0]["definition_evidence_texts"] = ["F is redefined at zero recoil."]
        result = lookup_symbol_definition(
            session, symbol="F", document_ids=[DOC], equation_id="eq_1"
        )
        assert "zero recoil" in result["definition"]["text"]
        assert FACT_DEFINED_LATER in result["facts"]

    def test_unresolvable_position_is_stated_honestly(self):
        """タップ位置が解けないときは黙って先頭を出さず、事実文を添える。"""
        result = lookup_symbol_definition(
            self._session(), symbol="F", document_ids=[DOC], equation_id="", chunk_id=""
        )
        assert FACT_POSITION_UNKNOWN in result["facts"]
        assert result["definition"]["text"]

    def test_chunk_id_is_used_when_no_equation_is_given(self):
        """式が無くてもチャンク（c2 = b4..b6）を位置として使える。"""
        result = lookup_symbol_definition(
            self._session(), symbol="F", document_ids=[DOC], chunk_id=CHUNK_2
        )
        # CHUNK_2 の先頭 b4 より前は b1 の定義。
        assert "maximum recoil" in result["definition"]["text"]
        assert result["facts"] == []

    def test_notation_variant_matches(self):
        """``notation_variants`` に載っている表記でも同じ行に当たる。"""
        result = lookup_symbol_definition(
            self._session(), symbol="F(w)", document_ids=[DOC], equation_id="eq_3"
        )
        assert result["available"] is True


# ---------------------------------------------------------------------------
# 4. 定義なし
# ---------------------------------------------------------------------------


class TestNoDefinition:
    def test_missing_definition_is_a_fact_about_this_paper(self):
        session = FakeSession(
            symbols=[
                {
                    "document_id": DOC,
                    "agent_symbol_id": "sym_%s_q" % DOC,
                    "canonical_symbol": "q",
                    "notation_variants": [],
                    "kind": "",
                    "unit": "",
                    "scope": "equation_local",
                    "definition_status": "definition_missing",
                    "defining_equation_ids": [],
                    "source_evidence_ids": [],
                    "definition_evidence_texts": [],
                }
            ],
            chunks=_chunks(),
            documents=_documents(),
        )
        result = lookup_symbol_definition(session, symbol="q", document_ids=[DOC])
        assert result["available"] is True
        assert result["definition"] is None
        assert FACT_NO_DEFINITION in result["facts"]
        # 定義状態のラベルは element_vocab の既存訳語（新しい訳語表を作らない）。
        assert "定義なし" in result["facts"]
        assert result["scope_label"] == "この式の中だけ"

    def test_closed_world_wording_never_claims_the_field(self):
        """「この分野には無い」「誰も定義していない」とは言わない（KR8 / SL1）。"""
        for line in (FACT_NO_DEFINITION, FACT_DEFINED_LATER, FACT_POSITION_UNKNOWN):
            assert "分野" not in line
            assert "世界" not in line
            assert "誰も" not in line


# ---------------------------------------------------------------------------
# 5. 完全一致（部分一致しない）
# ---------------------------------------------------------------------------


class TestExactMatchOnly:
    def _session(self):
        return FakeSession(
            symbols=[
                {
                    "document_id": DOC,
                    "agent_symbol_id": "sym_%s_Vcb" % DOC,
                    "canonical_symbol": "V_{cb}",
                    "notation_variants": [],
                    "kind": "",
                    "unit": "",
                    "scope": "document",
                    "definition_status": "defined",
                    "defining_equation_ids": ["eq_1"],
                    "source_evidence_ids": [],
                    "definition_evidence_texts": ["V_{cb} is the CKM matrix element."],
                }
            ],
            equations=[
                {"document_id": DOC, "agent_equation_id": "eq_1", "block_id": "b1", "page": 1, "label": "(1)"}
            ],
            chunks=_chunks(),
            documents=_documents(),
        )

    def test_subscripted_symbol_matches_after_normalization(self):
        """``V_cb``（フロントが組み直す形）は ``V_{cb}`` と同じキーに畳まれる。"""
        result = lookup_symbol_definition(self._session(), symbol="V_cb", document_ids=[DOC])
        assert result["available"] is True

    @pytest.mark.parametrize("probe", ["V", "cb", "b", "VV"])
    def test_substring_does_not_match(self, probe):
        """``V`` / ``cb`` は ``V_{cb}`` に当たらない（部分一致は F-7 の再発）。"""
        result = lookup_symbol_definition(self._session(), symbol=probe, document_ids=[DOC])
        assert result["available"] is False

    def test_empty_symbol_returns_unavailable(self):
        result = lookup_symbol_definition(self._session(), symbol="  ", document_ids=[DOC])
        assert result["available"] is False


# ---------------------------------------------------------------------------
# 6. 非漏洩
# ---------------------------------------------------------------------------


class TestNoLeakage:
    def _result(self):
        session = FakeSession(
            symbols=[
                {
                    "document_id": DOC,
                    "agent_symbol_id": "sym_%s_F" % DOC,
                    "canonical_symbol": "F",
                    "notation_variants": [],
                    "kind": "",
                    "unit": "GeV",
                    "scope": "document",
                    "definition_status": "defined",
                    "defining_equation_ids": ["eq_1"],
                    "source_evidence_ids": ["ev_0007"],
                    "definition_evidence_texts": ["F is the form factor."],
                    # 本来 DTO に出てはいけない列（読んでも載せないことの確認）。
                    "stable_key": "k1:deadbeef",
                    "produced_by_run_id": "run-1",
                }
            ],
            equations=[
                {"document_id": DOC, "agent_equation_id": "eq_1", "block_id": "b1", "page": 1, "label": "(1)"}
            ],
            evidence=[
                {
                    "document_id": DOC,
                    "agent_evidence_id": "ev_0007",
                    "block_id": "b2",
                    "page": 1,
                    "evidence_text": "The form factor F parametrises the hadronic current.",
                }
            ],
            chunks=_chunks(),
            documents=_documents(),
        )
        return lookup_symbol_definition(session, symbol="F", document_ids=[DOC], equation_id="eq_1")

    def test_no_confidence_stable_key_or_run_id(self):
        blob = json.dumps(self._result(), ensure_ascii=False)
        for forbidden in ("confidence", "stable_key", "produced_by_run_id", "k1:", "run-1"):
            assert forbidden not in blob

    def test_no_internal_ids_in_displayed_strings(self):
        """表示文字列（記号 / 事実文 / 出典 / 式ラベル）に内部 ID が混ざらない。"""
        result = self._result()
        displayed = [result["symbol"], result.get("source", "")] + list(result["facts"])
        definition = result.get("definition") or {}
        displayed.append(definition.get("equation_label", ""))
        for value in displayed:
            assert not contains_internal_id(value), value
        blob = json.dumps(result, ensure_ascii=False)
        for forbidden in ("sym_", "ev_0007", "eq_op_"):
            assert forbidden not in blob

    def test_unit_and_scope_are_included(self):
        result = self._result()
        assert result["unit"] == "GeV"
        assert result["scope_label"] == "論文全体"
        assert result["source"] == "Semileptonic B decays"


# ---------------------------------------------------------------------------
# 7. fail-closed / concept_ref
# ---------------------------------------------------------------------------


class TestScopeAndConceptRef:
    def test_no_documents_issues_no_sql(self):
        session = FakeSession()
        result = lookup_symbol_definition(session, symbol="F", document_ids=[])
        assert result["available"] is False
        assert session.statements == []

    def test_non_uuid_document_ids_are_dropped(self):
        """material_id 形（UUID でない）が混ざっても ``uuid[]`` に流さない。"""
        session = FakeSession()
        lookup_symbol_definition(session, symbol="F", document_ids=["material-abc"])
        assert session.statements == []

    def test_symbols_of_other_documents_are_not_returned(self):
        session = FakeSession(
            symbols=[
                {
                    "document_id": OTHER_DOC,
                    "agent_symbol_id": "sym_other_F",
                    "canonical_symbol": "F",
                    "notation_variants": [],
                    "kind": "",
                    "unit": "",
                    "scope": "",
                    "definition_status": "defined",
                    "defining_equation_ids": [],
                    "source_evidence_ids": [],
                    "definition_evidence_texts": ["should not be visible"],
                }
            ],
            chunks=_chunks(),
            documents=_documents(),
        )
        result = lookup_symbol_definition(session, symbol="F", document_ids=[DOC])
        assert result["available"] is False

    def test_confirmed_identity_link_becomes_concept_ref(self):
        session = FakeSession(
            symbols=[
                {
                    "document_id": DOC,
                    "agent_symbol_id": "sym_F",
                    "canonical_symbol": "F",
                    "notation_variants": [],
                    "kind": "",
                    "unit": "",
                    "scope": "",
                    "definition_status": "defined",
                    "defining_equation_ids": [],
                    "source_evidence_ids": [],
                    "definition_evidence_texts": ["F is the form factor."],
                }
            ],
            chunks=_chunks(),
            documents=_documents(),
            identity_links=[
                {
                    "instance_element_id": "sym_F",
                    "instance_document_id": DOC,
                    "entry_id": "33333333-3333-4333-8333-333333333333",
                    "name": "形状因子",
                    "entry_type": "concept",
                }
            ],
        )
        result = lookup_symbol_definition(session, symbol="F", document_ids=[DOC])
        assert result["concept_ref"]["name"] == "形状因子"
        # P3-R4: 学習者 DTO には内部 ID も生の語彙キーも載せない（表示ラベルのみ）。
        assert "entry_id" not in result["concept_ref"]
        assert "entry_type" not in result["concept_ref"]
        assert set(result["concept_ref"]) <= {"name", "entry_type_label"}

    def test_concept_ref_query_is_restricted_to_confirmed_links(self):
        """SQL が ``status='confirmed'`` と ``instance_element_type='symbol'`` を含む。"""
        source = (BACKEND / "core" / "symbol_lookup.py").read_text(encoding="utf-8")
        assert "l.instance_element_type = 'symbol'" in source
        assert "l.status = 'confirmed'" in source
        # candidate を学習者へ出さない（KR2）。
        assert "'candidate'" not in source

    def test_no_concept_ref_without_a_link(self):
        session = FakeSession(
            symbols=[
                {
                    "document_id": DOC,
                    "agent_symbol_id": "sym_F",
                    "canonical_symbol": "F",
                    "notation_variants": [],
                    "kind": "",
                    "unit": "",
                    "scope": "",
                    "definition_status": "defined",
                    "defining_equation_ids": [],
                    "source_evidence_ids": [],
                    "definition_evidence_texts": ["F is the form factor."],
                }
            ],
            chunks=_chunks(),
            documents=_documents(),
        )
        result = lookup_symbol_definition(session, symbol="F", document_ids=[DOC])
        assert result["concept_ref"] is None


# ---------------------------------------------------------------------------
# ガードレール（core の純度）
# ---------------------------------------------------------------------------


class TestCoreGuardrails:
    def test_core_does_not_import_fastapi_or_llm(self):
        """import 文そのものを AST で見る（docstring の言及で誤検出しない）。"""
        import ast

        source = (BACKEND / "core" / "symbol_lookup.py").read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {"fastapi", "core.llm", "openai", "core.embedder"}
        assert not (imported & forbidden), sorted(imported & forbidden)
        assert not any(m.startswith("fastapi") for m in imported)
        # 決定論・LLM 0 回（KR5）: 生成・埋め込みの呼び出しが本文に現れない。
        body = source.split('"""', 2)[-1]
        for forbidden_call in ("generate_text", "generate_embeddings", "usage_context"):
            assert forbidden_call not in body

    def test_scope_is_enforced_in_sql(self):
        """``document_id = ANY(CAST(:doc_ids AS uuid[]))`` が SQL の中にある（KR10）。"""
        source = (BACKEND / "core" / "symbol_lookup.py").read_text(encoding="utf-8")
        assert source.count("document_id = ANY(CAST(:doc_ids AS uuid[]))") >= 3

    def test_module_exposes_no_write_paths(self):
        source = (BACKEND / "core" / "symbol_lookup.py").read_text(encoding="utf-8")
        for statement in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
            assert statement not in source

    def test_display_strings_go_through_hygiene(self):
        """制御文字は表示前に落とす（core/text_hygiene.py が正本）。"""
        assert symbol_lookup._clean("a\x1b[0mb") == "ab"
