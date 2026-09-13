"""学習者の概念マップから記号を除く（案 E） — `claim_concept_grounding_design.md` §8 / CG5・CG6。

- コース登録（`create_course`）の概念マップに記号を入れない。除いた名前は
  `data.excluded_symbol_concepts` に残す（情報を落とさない）。
- 除いた名前は学習者向け DTO に漏れない。
- コースビルダーの材料文脈・教材一覧の概念供給も同じ判定で記号を除き、
  学ぶ単位（Phase 2）の `teaches` が教える言葉を先に並べる。
- 記号判定は A層 `is_symbol_like_concept_name`（P0-3）に委譲する（第2の規則を作らない）。
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

_API_DIR = str(Path(__file__).resolve().parents[1] / "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from core import course_data as course_data_module  # noqa: E402
from core.course_data import excluded_symbol_concepts, is_symbol_concept_name  # noqa: E402
from core.course_units import unit_concept_terms_by_document  # noqa: E402
import routes.admin as admin_routes  # noqa: E402
import routes.learning as learning_routes  # noqa: E402

_BACKEND_DIR = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> set[tuple[str, str]]:
    """``(module, name)`` の集合（from-import のみ）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                found.add((node.module, alias.name))
    return found


# ---------------------------------------------------------------------------
# 判定そのもの（A層委譲）
# ---------------------------------------------------------------------------


class TestSymbolPredicate:
    @pytest.mark.parametrize("name", ["R", "λ", "\\lambda", "b_1", "x^2", "", "  ", "e"])
    def test_symbols_are_excluded(self, name):
        assert is_symbol_concept_name(name) is True

    @pytest.mark.parametrize("name", ["dark energy", "重力", "zero_recoil_limit", "赤方偏移"])
    def test_field_words_are_kept(self, name):
        assert is_symbol_concept_name(name) is False

    def test_predicate_delegates_to_a_layer(self):
        """判定の正本は A層の P0-3。``core/course_data.py`` が import して使う。"""
        assert (
            "episteme_graph.agents.component_assembly.schema",
            "is_symbol_like_concept_name",
        ) in _imports(_BACKEND_DIR / "core" / "course_data.py")

    def test_routes_use_the_shared_predicate(self):
        """学習者向け/教員向けのどちらの経路も同じ述語を通す（第2の正規表現を書かない）。"""
        for path in (
            _BACKEND_DIR / "api" / "routes" / "learning.py",
            _BACKEND_DIR / "api" / "routes" / "admin.py",
        ):
            assert ("core.course_data", "is_symbol_concept_name") in _imports(path)


# ---------------------------------------------------------------------------
# 概念マップの組み立て（案 E 本体）
# ---------------------------------------------------------------------------


class TestSplitSymbolConcepts:
    def test_symbol_named_concept_is_removed_and_remembered(self):
        kept, excluded = learning_routes._split_symbol_concepts([
            {"name": "R", "children": ["λ"]},
            {"name": "dark energy", "children": ["赤方偏移"]},
        ])
        assert [c["name"] for c in kept] == ["dark energy"]
        assert excluded == ["R", "λ"]

    def test_symbol_children_are_removed_without_dropping_the_concept(self):
        kept, excluded = learning_routes._split_symbol_concepts([
            {"name": "dark energy", "children": ["λ", "状態方程式"]},
        ])
        assert kept[0]["children"] == ["状態方程式"]
        assert excluded == ["λ"]

    def test_excluded_names_are_deduplicated_in_order(self):
        _, excluded = learning_routes._split_symbol_concepts([
            {"name": "R", "children": []},
            {"name": "dark energy", "children": ["R", "λ", "R"]},
        ])
        assert excluded == ["R", "λ"]

    def test_input_is_not_mutated(self):
        original = [{"name": "dark energy", "children": ["λ", "状態方程式"]}]
        learning_routes._split_symbol_concepts(original)
        assert original[0]["children"] == ["λ", "状態方程式"]

    def test_nothing_excluded_keeps_everything(self):
        concepts = [{"name": "dark energy", "children": ["状態方程式"]}]
        kept, excluded = learning_routes._split_symbol_concepts(concepts)
        assert kept == concepts and excluded == []


class TestCreateCourseWiring:
    def test_filter_runs_before_save(self):
        src = inspect.getsource(learning_routes.create_course)
        assert src.index("_split_symbol_concepts(") < src.index("save_course_data(")
        assert '"excluded_symbol_concepts"' in src

    def test_excluded_names_are_stored_only_when_present(self):
        """空のときにキーを足さない（空欄は正常な状態で、欠落を作らない）。"""
        src = inspect.getsource(learning_routes.create_course)
        assert "if excluded_symbol_names:" in src


class TestExcludedAccessor:
    def test_accessor_reads_list_of_names(self):
        assert excluded_symbol_concepts({"excluded_symbol_concepts": ["R", " ", "λ"]}) == ["R", "λ"]

    @pytest.mark.parametrize("value", [None, {}, {"excluded_symbol_concepts": "R"}])
    def test_missing_or_malformed_is_empty(self, value):
        assert excluded_symbol_concepts(value) == []

    def test_course_data_model_keeps_the_key(self):
        """``extra="allow"`` の素通しではなく、カタログに宣言されたフィールドとして持つ。"""
        assert "excluded_symbol_concepts" in course_data_module.CourseData.model_fields


class TestLearnerDtoDoesNotLeak:
    def test_course_detail_drops_excluded_symbol_concepts(self):
        from schemas import LearningCourseDetail

        data = {
            "id": "c1",
            "title": "T",
            "concepts": [{"name": "dark energy"}],
            "excluded_symbol_concepts": ["R", "λ"],
        }
        dumped = LearningCourseDetail(**data).model_dump()
        assert "excluded_symbol_concepts" not in dumped
        assert [c["name"] for c in dumped["concepts"]] == ["dark energy"]

    def test_learner_projection_does_not_add_the_key(self):
        out = learning_routes._project_topics_for_learner({
            "topics": [{"id": "t0"}], "excluded_symbol_concepts": ["R"],
        })
        # 保存形の投影なのでキーは残るが、DTO（上のテスト）で落ちる。
        assert out["excluded_symbol_concepts"] == ["R"]


# ---------------------------------------------------------------------------
# 学ぶ単位が教える言葉（teaches）の供給
# ---------------------------------------------------------------------------


class _UnitsSession:
    """``learning_units_live`` だけを返す最小のフェイク。"""

    def __init__(self, rows):
        self._rows = rows
        self.queries: list[str] = []

    def execute(self, statement, params=None):
        self.queries.append(str(statement))
        return _Result(self._rows)

    def close(self) -> None:
        pass


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


def _unit_row(document_id, kind, order_index, label, teaches):
    return (document_id, kind, order_index, label, teaches)


class TestUnitConceptTerms:
    def test_concept_items_only_and_symbols_dropped(self):
        session = _UnitsSession([
            _unit_row("doc-1", "section_block", 1, "章2", [
                {"kind": "concept", "ref": "λ", "label": "λ"},
                {"kind": "concept", "ref": "状態方程式", "label": "状態方程式"},
                {"kind": "claim", "ref": "uuid-1", "label": ""},
            ]),
            _unit_row("doc-1", "section_block", 0, "章1", [
                {"kind": "concept", "ref": "dark energy", "label": "dark energy"},
            ]),
        ])
        terms = unit_concept_terms_by_document(session, ["doc-1"])
        # order_index 順（章1 → 章2）。記号と claim 参照は出ない。
        assert terms == {"doc-1": ["dark energy", "状態方程式"]}

    def test_dismissed_units_are_excluded_in_sql(self):
        session = _UnitsSession([])
        unit_concept_terms_by_document(session, ["doc-1"])
        assert "review_status <> 'dismissed'" in session.queries[0]
        assert "learning_units_live" in session.queries[0]

    def test_no_documents_means_no_sql(self):
        session = _UnitsSession([])
        assert unit_concept_terms_by_document(session, []) == {}
        assert session.queries == []

    def test_unreadable_table_degrades_to_empty(self):
        class _Boom:
            def execute(self, *_a, **_k):
                raise RuntimeError("no such table")

        assert unit_concept_terms_by_document(_Boom(), ["doc-1"]) == {}


# ---------------------------------------------------------------------------
# 教員向けの概念供給（材料文脈 / 教材一覧）
# ---------------------------------------------------------------------------


class _MaterialContextSession:
    """``_build_material_context`` が撃つ SQL を本文で振り分ける最小のフェイク。"""

    def __init__(self, *, doc_rows, unit_rows):
        self._doc_rows = doc_rows
        self._unit_rows = unit_rows

    def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM documents" in sql:
            return _Result(self._doc_rows)
        if "learning_units_live" in sql:
            return _Result(self._unit_rows)
        return _Result([])

    def close(self) -> None:
        pass


@pytest.fixture
def material_context(monkeypatch):
    monkeypatch.setattr(admin_routes, "resolve_artifact_runs", lambda _s, _d: {})
    monkeypatch.setattr(admin_routes, "list_unit_candidates", lambda _s, _d: [])

    def _build(*, unit_rows, knowledge_graph):
        doc_rows = [("m1", "論文A", "a.pdf", "doc-1", "completed", knowledge_graph)]
        session = _MaterialContextSession(doc_rows=doc_rows, unit_rows=unit_rows)
        return admin_routes._build_material_context(["m1"], pg_session_factory=lambda: session)

    return _build


class TestMaterialContextConceptSupply:
    def test_unit_terms_come_first_and_symbols_are_dropped(self, material_context):
        text = material_context(
            unit_rows=[_unit_row("doc-1", "section_block", 0, "章1", [
                {"kind": "concept", "ref": "dark energy", "label": "dark energy"},
                {"kind": "concept", "ref": "λ", "label": "λ"},
            ])],
            knowledge_graph={"concepts": [
                {"name": "R", "type": "symbol"},
                {"name": "赤方偏移", "type": "concept"},
            ]},
        )
        assert "この教材が教える言葉" in text
        assert "dark energy" in text
        # legacy 概念より前に出る（teaches を先に並べる）
        assert text.index("この教材が教える言葉") < text.index("赤方偏移")
        # 記号は teaches からも legacy からも出ない
        assert "λ" not in text
        assert "- R\n" not in text and "- R (" not in text

    def test_section_is_omitted_when_no_units(self, material_context):
        text = material_context(unit_rows=[], knowledge_graph={"concepts": [{"name": "赤方偏移"}]})
        assert "この教材が教える言葉" not in text
        assert "赤方偏移" in text


class TestMaterialListTopConcepts:
    def test_unit_terms_precede_legacy_and_symbols_are_dropped(self):
        src = inspect.getsource(admin_routes.list_materials)
        assert "unit_concept_terms_by_document(" in src
        assert src.index("top_concepts.extend(unit_terms_by_mid") < src.index('kg.get("concepts")')
        assert "is_symbol_concept_name(name)" in src

    def test_limit_is_unchanged(self):
        """件数は増やさない（上限は既存のまま = 6 件）。"""
        assert "top_concepts=top_concepts[:6]," in inspect.getsource(admin_routes.list_materials)
