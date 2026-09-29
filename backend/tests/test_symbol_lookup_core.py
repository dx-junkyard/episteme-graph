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
        # タップ位置（チャンク）がこの論文にあるときだけ「この式の中だけ」を出す（IK-0387）。
        result = lookup_symbol_definition(
            session, symbol="q", document_ids=[DOC], chunk_id=CHUNK_1
        )
        assert result["available"] is True
        assert result["definition"] is None
        assert FACT_NO_DEFINITION in result["facts"]
        # 定義状態のラベルは element_vocab の既存訳語（新しい訳語表を作らない）。
        assert "定義なし" in result["facts"]
        assert result["scope_label"] == "この式の中だけ"

    @pytest.mark.parametrize("status", ["used", "defined", "redefined", "unknown", ""])
    def test_no_definition_never_paired_with_a_label_implying_one_exists(self, status):
        """IK-0380: 定義なしのとき「定義は別の箇所」「この論文で定義」を並べない。"""
        session = FakeSession(
            symbols=[
                {
                    "document_id": DOC,
                    "agent_symbol_id": "sym_%s_alpha" % DOC,
                    "canonical_symbol": "alpha",
                    "notation_variants": [],
                    "kind": "",
                    "unit": "",
                    "scope": "",
                    "definition_status": status,
                    "defining_equation_ids": [],
                    "source_evidence_ids": [],
                    "definition_evidence_texts": [],
                }
            ],
            chunks=_chunks(),
            documents=_documents(),
        )
        result = lookup_symbol_definition(session, symbol="alpha", document_ids=[DOC])
        assert result["definition"] is None
        assert FACT_NO_DEFINITION in result["facts"]
        joined = "\n".join(result["facts"])
        assert "別の箇所" not in joined
        assert "この論文で定義" not in joined
        assert "この論文で再定義" not in joined
        assert result["facts"] == [FACT_NO_DEFINITION]

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


# ---------------------------------------------------------------------------
# 8. IK-0386 / IK-0387 — 事実文なしの unavailable・大文字小文字・論文をまたぐ定義
# ---------------------------------------------------------------------------


from core.symbol_lookup import (  # noqa: E402
    FACT_DEFINITION_FROM,
    FACT_DEFINITION_FROM_OTHER_DOCUMENT,
    FACT_NO_SOURCE_DOCUMENTS,
    FACT_SYMBOL_NOT_REGISTERED,
    symbol_key,
)


def _symbol_row(document_id, canonical, *, texts, scope="section", variants=()):
    return {
        "document_id": document_id,
        "agent_symbol_id": "sym_%s_%s" % (document_id[:4], canonical),
        "canonical_symbol": canonical,
        "notation_variants": list(variants),
        "kind": "",
        "unit": "",
        "scope": scope,
        "definition_status": "defined",
        "defining_equation_ids": [],
        "source_evidence_ids": [],
        "definition_evidence_texts": list(texts),
    }


class TestUnavailableAlwaysStatesAFact:
    """IK-0386: ``available=False`` は必ず事実文を1つ持つ。"""

    def test_symbol_absent_from_registry(self):
        session = FakeSession(
            symbols=[_symbol_row(DOC, "F", texts=["F is the form factor."])],
            chunks=_chunks(),
            documents=_documents(),
        )
        result = lookup_symbol_definition(session, symbol="B_{3D}", document_ids=[DOC])
        assert result["available"] is False
        assert result["facts"] == [FACT_SYMBOL_NOT_REGISTERED]

    def test_no_source_documents(self):
        result = lookup_symbol_definition(FakeSession(), symbol="F", document_ids=[])
        assert result["available"] is False
        assert result["facts"] == [FACT_NO_SOURCE_DOCUMENTS]

    def test_symbol_that_normalizes_to_nothing(self):
        session = FakeSession(symbols=[], documents=_documents())
        result = lookup_symbol_definition(session, symbol="{}", document_ids=[DOC])
        assert result["available"] is False
        assert result["facts"] == [FACT_SYMBOL_NOT_REGISTERED]

    @pytest.mark.parametrize(
        "line", [FACT_SYMBOL_NOT_REGISTERED, FACT_NO_SOURCE_DOCUMENTS]
    )
    def test_closed_world_wording(self, line):
        for word in ("分野", "世界", "誰も", "どこにも"):
            assert word not in line
        assert not any(ch.isdigit() for ch in line)


class TestSymbolKeyIsCaseSensitive:
    """IK-0387: ``λ`` と ``Λ``、``B`` と ``b`` は別の記号。"""

    @pytest.mark.parametrize(
        "a,b",
        [
            ("\\lambda", "λ"),
            ("\\Lambda", "Λ"),
            ("V_{cb}", "V_cb"),
            ("B_{\\rm 3D}", "B_3D"),
            ("B_{\\mathrm{3D}}", "B_{3D}"),
            ("$\\rho$", "ρ"),
        ],
    )
    def test_notation_variants_fold_together(self, a, b):
        assert symbol_key(a) == symbol_key(b)

    @pytest.mark.parametrize(
        "a,b",
        [("\\lambda", "\\Lambda"), ("λ", "Λ"), ("B", "b"), ("\\delta", "\\Delta"), ("V_cb", "V^cb")],
    )
    def test_case_and_script_are_kept(self, a, b):
        assert symbol_key(a) != symbol_key(b)

    def test_lowercase_lambda_does_not_hit_capital_lambda_row(self):
        session = FakeSession(
            symbols=[
                _symbol_row(
                    OTHER_DOC,
                    "\\Lambda",
                    texts=["xi(Lambda) is the selection function"],
                )
            ],
            documents=[{"id": OTHER_DOC, "title": "Binary black holes"}],
        )
        result = lookup_symbol_definition(
            session, symbol="\\lambda", document_ids=[DOC, OTHER_DOC]
        )
        assert result["available"] is False
        assert result["facts"] == [FACT_SYMBOL_NOT_REGISTERED]

    def test_unicode_tap_matches_tex_row(self):
        session = FakeSession(
            symbols=[_symbol_row(DOC, "\\lambda", texts=["lambda is the mass-to-flux ratio."])],
            chunks=_chunks(),
            documents=_documents(),
        )
        result = lookup_symbol_definition(session, symbol="λ", document_ids=[DOC])
        assert result["available"] is True
        assert "mass-to-flux" in result["definition"]["text"]


class TestDocumentAndScopeFacts:
    """IK-0387: 出所の論文を言う・相対的な有効範囲はタップ位置の論文でだけ出す。"""

    def _session(self):
        return FakeSession(
            symbols=[
                _symbol_row(OTHER_DOC, "\\lambda", texts=["lambda is a coupling in the other paper."]),
                _symbol_row(DOC, "F", texts=["F is the form factor."]),
            ],
            chunks=_chunks()
            + [{"id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1", "document_id": OTHER_DOC,
                "chunk_index": 0, "block_ids": ["o1"]}],
            documents=[
                {"id": DOC, "title": "Magnetic fields in Cep B"},
                {"id": OTHER_DOC, "title": "Binary black holes"},
            ],
        )

    def test_no_tap_position_names_the_paper_and_drops_section_label(self):
        result = lookup_symbol_definition(
            self._session(), symbol="λ", document_ids=[DOC, OTHER_DOC]
        )
        assert result["available"] is True
        assert "scope_label" not in result
        assert FACT_DEFINITION_FROM.format(title="Binary black holes") in result["facts"]
        assert FACT_POSITION_UNKNOWN in result["facts"]

    def test_fallback_to_other_paper_is_stated(self):
        """タップした論文（DOC）に λ が無く、別の論文の定義へ倒すとき。"""
        result = lookup_symbol_definition(
            self._session(), symbol="λ", document_ids=[DOC, OTHER_DOC], chunk_id=CHUNK_1
        )
        assert result["available"] is True
        assert result["facts"] == [
            FACT_DEFINITION_FROM_OTHER_DOCUMENT.format(title="Binary black holes")
        ]
        assert "scope_label" not in result
        assert result["source"] == "Binary black holes"

    def test_same_paper_row_is_preferred(self):
        session = self._session()
        session.symbols.append(
            _symbol_row(DOC, "\\lambda", texts=["lambda is the mass-to-flux ratio."])
        )
        result = lookup_symbol_definition(
            session, symbol="λ", document_ids=[DOC, OTHER_DOC], chunk_id=CHUNK_2
        )
        assert "mass-to-flux" in result["definition"]["text"]
        assert result["scope_label"] == "この節の中"
        # 所在の残っていない定義文なので位置の事実文だけ（同じ論文なのでタイトルは足さない）。
        assert result["facts"] == [FACT_POSITION_UNKNOWN]

    def test_titles_carry_no_internal_ids(self):
        result = lookup_symbol_definition(
            self._session(), symbol="λ", document_ids=[DOC, OTHER_DOC], chunk_id=CHUNK_1
        )
        for value in result["facts"]:
            assert not contains_internal_id(value), value
            assert OTHER_DOC not in value
