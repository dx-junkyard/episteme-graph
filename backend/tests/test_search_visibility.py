"""Phase 0（discuss モード設計書 §6.1）— 全域ベクトル検索の可視性フィルタ強制テスト。

背景: `search_chunks_with_metadata`（api/services.py）はコース非依存の全域ベクトル検索で、
document の Public/Group/Private を一切考慮していなかった（他人の Private 文書のチャンクが
誰の検索結果にも出うる潜在バグ）。discuss モードとは独立した既存バグの単独修正として、
Phase 0 で以下を強制する:

- `search_chunks_with_metadata` に `allowed_document_ids` を必須キーワード引数として追加
  （呼び忘れを TypeError で防ぐ。`core/help_kb/manual.py::search_manual(..., audience)` と
  同じ規律）。`None` はテスト・本番未接続コード専用の「無フィルタ」。
- 「本人が閲覧可能な document id 集合」ヘルパー `list_visible_document_ids` を新設
  （チャンク単位ループの N+1 判定は横断基盤の既存ルールで禁止のため、1 SQL で計算する）。

外部 DB・LLM への実接続は行わない（session / embed_text はすべてモック）。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from api import services
from tests.guardrail_helpers import assert_module_tree_forbids

_BACKEND = Path(__file__).resolve().parents[1]
_ROUTES_DIR = _BACKEND / "api" / "routes"


class _Rows:
    """session.execute(...).fetchall() の戻り値スタブ。"""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _CapturingSession:
    """execute() に渡された SQL 文字列と params を記録するフェイクセッション（rows は固定）。"""

    def __init__(self, rows=()):
        self._rows = rows
        self.executed_sql: str | None = None
        self.executed_params: dict | None = None

    def execute(self, sql, params=None):
        self.executed_sql = str(sql)
        self.executed_params = params or {}
        return _Rows(self._rows)

    def close(self):
        pass


# ---------------------------------------------------------------------------
# 1. allowed_document_ids は必須キーワード引数（呼び忘れは TypeError）
# ---------------------------------------------------------------------------


class TestSearchChunksSignatureIsKeywordOnlyRequired:
    def test_missing_allowed_document_ids_raises_type_error(self):
        with pytest.raises(TypeError):
            services.search_chunks_with_metadata("質問")

    def test_positional_allowed_document_ids_is_rejected(self):
        """キーワード専用であることの確認（位置引数では渡せない）。"""
        with pytest.raises(TypeError):
            services.search_chunks_with_metadata("質問", 8, set())

    def test_parameter_is_keyword_only_without_default(self):
        sig = inspect.signature(services.search_chunks_with_metadata)
        param = sig.parameters["allowed_document_ids"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty


# ---------------------------------------------------------------------------
# 2. 空集合 → SQL 発行なしで空リスト（fail-closed）
# ---------------------------------------------------------------------------


class TestEmptyAllowedDocumentIdsShortCircuits:
    def test_empty_set_returns_empty_without_embedding_call(self, monkeypatch):
        def _boom(*_a, **_k):
            raise AssertionError("embed_text should not be called when allowed_document_ids is empty")

        monkeypatch.setattr(services, "embed_text", _boom)
        assert services.search_chunks_with_metadata("質問", allowed_document_ids=set()) == []

    def test_empty_list_returns_empty_without_embedding_call(self, monkeypatch):
        def _boom(*_a, **_k):
            raise AssertionError("embed_text should not be called when allowed_document_ids is empty")

        monkeypatch.setattr(services, "embed_text", _boom)
        assert services.search_chunks_with_metadata("質問", allowed_document_ids=[]) == []


# ---------------------------------------------------------------------------
# 3. 非空集合 → SQL に document フィルタ句が入り、material_id は SELECT に残る
# ---------------------------------------------------------------------------


class TestNonEmptyAllowedDocumentIdsFiltersSql:
    def test_sql_contains_document_filter_and_keeps_material_id(self, monkeypatch):
        session = _CapturingSession(rows=[])
        monkeypatch.setattr(services, "embed_text", lambda _q: [0.0] * 8)
        monkeypatch.setattr(services, "get_embedding_dim", lambda: 8)
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        result = services.search_chunks_with_metadata(
            "質問", top_k=5, allowed_document_ids={"doc-1", "doc-2"},
        )

        assert result == []
        assert session.executed_sql is not None
        # component_context.py:131 の ANY(:doc_ids) 先例と同型だが、chunks.document_id は
        # UUID 列（theory_components.document_id は TEXT）なので明示 CAST が要る。
        assert "c.document_id = ANY(CAST(:doc_ids AS uuid[]))" in session.executed_sql
        assert "c.material_id" in session.executed_sql
        assert set(session.executed_params["doc_ids"]) == {"doc-1", "doc-2"}

    def test_none_means_no_filter_test_only(self, monkeypatch):
        """None は無フィルタ（テスト・本番未接続コード専用。docstring にも明記）。"""
        session = _CapturingSession(rows=[])
        monkeypatch.setattr(services, "embed_text", lambda _q: [0.0] * 8)
        monkeypatch.setattr(services, "get_embedding_dim", lambda: 8)
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        services.search_chunks_with_metadata("質問", allowed_document_ids=None)

        assert "document_id = ANY" not in session.executed_sql
        assert "c.material_id" in session.executed_sql
        assert "doc_ids" not in session.executed_params


# ---------------------------------------------------------------------------
# 4. 静的ガードレール: routes/*.py に allowed_document_ids=None が出現しない（呼び忘れ検出）
# ---------------------------------------------------------------------------


class TestNoRouteExplicitlyDisablesTheFilter:
    def test_routes_never_pass_none_explicitly(self):
        assert_module_tree_forbids(_ROUTES_DIR, ["allowed_document_ids=None"])

    def test_learning_and_lecture_wire_list_visible_document_ids(self):
        """本番の呼び出し元2箇所が list_visible_document_ids の結果を渡していること。"""
        learning_src = (_ROUTES_DIR / "learning.py").read_text(encoding="utf-8")
        lecture_src = (_ROUTES_DIR / "lecture.py").read_text(encoding="utf-8")
        for src in (learning_src, lecture_src):
            assert "list_visible_document_ids(current_user[\"id\"])" in src
            assert "allowed_document_ids=allowed_document_ids" in src


# ---------------------------------------------------------------------------
# 5. list_visible_document_ids は例外時に空集合（fail-closed）
# ---------------------------------------------------------------------------


class TestListVisibleDocumentIds:
    def test_exception_returns_empty_set(self, monkeypatch):
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(services, "_pg_session", _boom)
        assert services.list_visible_document_ids("user-1") == set()

    def test_returns_ids_from_rows(self, monkeypatch):
        session = _CapturingSession(rows=[("doc-a",), ("doc-b",), (None,)])
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        result = services.list_visible_document_ids("user-1")

        assert result == {"doc-a", "doc-b"}  # None 行は落ちる

    def test_sql_covers_all_five_visibility_sources(self, monkeypatch):
        """(a)所有者 (b)public (c)group (d)object_group_permissions (e)コース経由 の
        全経路が1 SQL に含まれていることを構造的に確認する。
        """
        session = _CapturingSession(rows=[])
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        services.list_visible_document_ids("user-1")

        sql = session.executed_sql
        assert sql is not None
        assert "d.uploaded_by = CAST(:uid AS uuid)" in sql  # (a)
        assert "d.visibility = 'public'" in sql  # (b)
        assert "d.visibility = 'group'" in sql and "group_members" in sql  # (c)
        assert "object_group_permissions" in sql  # (d)
        assert "learning_states" in sql and "jsonb_array_elements" in sql  # (e)
        assert session.executed_params == {"uid": "user-1"}


# ---------------------------------------------------------------------------
# IK-0381: 同じ論文からの出典を見分ける「箇所の手がかり」（meta）
# ---------------------------------------------------------------------------


_DOC_ROW_COMMON = ("2605.26810v1", "2605.26810v1.pdf")


def _learning_routes():
    """routes/learning.py は ``dependencies`` を api/ 直下から import するため、api/ を path に足す。"""
    import sys

    api_dir = str(_BACKEND / "api")
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    from api.routes import learning as learning_routes

    return learning_routes


class TestChunkLocationHint:
    def _search(self, monkeypatch, rows):
        from core import learning_experience

        session = _CapturingSession(rows=rows)
        monkeypatch.setattr(services, "embed_text", lambda _q: [0.0] * 8)
        monkeypatch.setattr(services, "get_embedding_dim", lambda: 8)
        monkeypatch.setattr(services, "_pg_session", lambda: session)
        monkeypatch.setattr(learning_experience, "approved_chunk_ids", lambda _s, _ids: set())
        results = services.search_chunks_with_metadata(
            "質問", top_k=8, allowed_document_ids={"doc-1"},
        )
        return session, results

    def test_sql_selects_section_title_from_the_chunk_row_in_one_query(self, monkeypatch):
        session, _ = self._search(monkeypatch, rows=[])
        assert "source_metadata->>'section_title'" in session.executed_sql
        # grounding 判定の生命線は落とさない。
        assert "c.material_id" in session.executed_sql

    def test_two_chunks_of_the_same_paper_get_different_meta(self, monkeypatch):
        """同じ文書（同じ題名・同じファイル名）の2区画は、meta で見分けられる。"""
        learning_routes = _learning_routes()

        rows = [
            ("chunk-1", "We measure the spectral index of the pulsar emission.",
             *_DOC_ROW_COMMON, 0.8, "mat-1", "Introduction"),
            ("chunk-2", "The flux density follows a power law P \\propto I^{-\\alpha}.",
             *_DOC_ROW_COMMON, 0.7, "mat-1", "Introduction"),
            ("chunk-3", "[[FORMULA_0]] where alpha is the index.",
             *_DOC_ROW_COMMON, 0.6, "mat-1", ""),
        ]
        _, results = self._search(monkeypatch, rows)
        assert len(results) == 3
        metas = [learning_routes._source_location_meta(r) for r in results]
        assert len(set(metas)) == 3
        assert all(m for m in metas)
        # 題名・ファイル名は全件同じ（IK-0381 の再現条件）。
        assert {r["source_title"] for r in results} == {"2605.26810v1"}
        assert metas[0].startswith("節「Introduction」")
        # 節が無い区画は冒頭だけ。数式のプレースホルダーは読める語に置き換える。
        assert "節「" not in metas[2]
        assert "FORMULA" not in metas[2]
        # 数値（類似度など）を meta に入れない。
        for m in metas:
            assert "0.8" not in m and "0.7" not in m and "0.6" not in m

    def test_hint_strips_control_sequences_and_is_short(self):
        hint = services.chunk_location_hint(
            section_title="\x1b[31mResults\x1b[0m", text="A" * 500,
        )
        assert "\x1b" not in hint and "[0m" not in hint
        assert hint.startswith("節「Results」")
        assert len(hint) < 80

    def test_meta_falls_back_to_filename_when_no_hint(self):
        learning_routes = _learning_routes()

        assert learning_routes._source_location_meta({"source_file": "a.pdf"}) == "a.pdf"
        assert learning_routes._source_location_meta({}) == ""

    def test_both_learning_source_builders_use_the_same_meta_function(self):
        source = (_ROUTES_DIR / "learning.py").read_text(encoding="utf-8")
        assert '"meta": r.get("source_file")' not in source
        # IK-0432: 2つの組み立て箇所（本体 RAG・前提知識の説明）は _adopted_source_entry 1本を共有する。
        assert source.count('"meta": _source_location_meta(result),') == 1
        assert source.count("_adopted_source_entry(") >= 3  # 定義 + 呼び出し2箇所


class TestChunkLocationHintJunkHeading:
    """GROBID 由来の断片見出し（「K」「DE」）を節名にしない（IK-0381 追補・IK-0370 の判定を再利用）。"""

    def test_fragment_heading_is_dropped(self):
        hint = services.chunk_location_hint(section_title="K", text="The Hubble parameter H(z) is defined by")
        assert "節「" not in hint
        assert hint.startswith("冒頭「")

    def test_real_heading_is_kept(self):
        hint = services.chunk_location_hint(section_title="Results", text="We find that")
        assert hint.startswith("節「Results」")


# ---------------------------------------------------------------------------
# IK-0390: 本文ではない区画（参考文献・謝辞・データの所在・書誌の列・目盛り・URL）を出典にしない
# IK-0391: チャンク本文の [[FORMULA_i]] を LaTeX に解決して LLM・出典へ渡す
# ---------------------------------------------------------------------------


def _search_rows(monkeypatch, rows, *, top_k=8):
    from core import learning_experience

    session = _CapturingSession(rows=rows)
    monkeypatch.setattr(services, "embed_text", lambda _q: [0.0] * 8)
    monkeypatch.setattr(services, "get_embedding_dim", lambda: 8)
    monkeypatch.setattr(services, "_pg_session", lambda: session)
    monkeypatch.setattr(learning_experience, "approved_chunk_ids", lambda _s, _ids: set())
    results = services.search_chunks_with_metadata(
        "質問", top_k=top_k, allowed_document_ids={"doc-1"},
    )
    return session, results


_BIBLIOGRAPHY_TEXT = (
    "Pattle K., Ward-Thompson D., Berry D., et al. 2017, ApJ, 846, 122 "
    "Houde M., Vaillancourt J. E., Hildebrand R. H., et al. 2009, ApJ, 706, 1504 "
    "Crutcher R. M. 2012, ARA&A, 50, 29 Davis L. 1951, PhRv, 81, 890"
)
_BODY_WITH_CITATIONS = (
    "The DCF method (Davis 1951; Chandrasekhar & Fermi 1953) has been widely used to "
    "estimate field strengths (e.g., Crutcher 2012; Pattle et al. 2017), and we adopt "
    "Q = 0.5 following Ostriker et al. (2001)."
)


class TestNonContentSectionsAreExcluded:
    def test_sql_excludes_non_content_section_titles_in_the_same_query(self, monkeypatch):
        session, _ = _search_rows(monkeypatch, rows=[])
        sql = session.executed_sql
        assert "ANY(CAST(:non_content_titles AS text[]))" in sql
        assert "source_metadata->>'section_title'" in sql
        # 1本の SQL のまま・可視性フィルタと grounding の生命線は落とさない。
        assert "c.document_id = ANY(CAST(:doc_ids AS uuid[]))" in sql
        assert "c.material_id" in sql
        titles = set(session.executed_params["non_content_titles"])
        assert {"references", "bibliography", "data availability", "acknowledgements",
                "参考文献", "謝辞"} <= titles
        # 付録は本文の続きなので落とさない。
        assert not any("appendix" in t for t in titles)
        prefixes = {v for k, v in session.executed_params.items() if k.startswith("non_content_prefix_")}
        assert "acknowledg%" in prefixes

    def test_denylist_is_exact_match_not_substring(self):
        # 部分一致（LIKE '%references%'）で本文見出しを落とさない。
        assert "'%references" not in services._NON_CONTENT_SECTION_FILTER_SQL
        assert services.NON_CONTENT_SECTION_TITLES == tuple(
            t.casefold().strip() for t in services.NON_CONTENT_SECTION_TITLES
        )

    def test_overfetches_twice_top_k(self, monkeypatch):
        session, _ = _search_rows(monkeypatch, rows=[], top_k=8)
        assert session.executed_params["limit"] == 16

    def test_post_filter_drops_bibliography_and_trims_to_top_k(self, monkeypatch):
        rows = [
            ("c-bib", _BIBLIOGRAPHY_TEXT, *_DOC_ROW_COMMON, 0.9, "mat-1", ""),
            ("c-1", _BODY_WITH_CITATIONS, *_DOC_ROW_COMMON, 0.8, "mat-1", "Method"),
            ("c-2", "We find that the filament is magnetically subcritical.",
             *_DOC_ROW_COMMON, 0.7, "mat-1", ""),
            ("c-3", "0\n5\n10\n15\n20\nRA\n(J2000)\n0.5\n1.0\n1.5",
             *_DOC_ROW_COMMON, 0.6, "mat-1", ""),
            ("c-4", "The ionization fraction sets the ambipolar diffusion timescale.",
             *_DOC_ROW_COMMON, 0.5, "mat-1", ""),
        ]
        _, results = _search_rows(monkeypatch, rows, top_k=2)
        # 書誌の列と目盛りは落ち、引用の多い本文段落は残る。件数は top_k に詰める。
        assert [r["id"] for r in results] == ["c-1", "c-2"]

    def test_chunk_without_section_title_is_not_excluded_by_heuristic(self, monkeypatch):
        rows = [("c-1", "We find that B is about 150 microgauss.", *_DOC_ROW_COMMON, 0.8, "mat-1", "")]
        _, results = _search_rows(monkeypatch, rows)
        assert [r["id"] for r in results] == ["c-1"]


class TestNonContentChunkReason:
    def test_bibliography(self):
        assert services.non_content_chunk_reason(_BIBLIOGRAPHY_TEXT) == "bibliography"

    def test_citation_dense_body_is_kept(self):
        assert services.non_content_chunk_reason(_BODY_WITH_CITATIONS) is None

    def test_axis_tick_labels(self):
        text = "0\n5\n10\n15\n20\nRA\n(J2000)\n0.5\n1.0\n1.5"
        assert services.non_content_chunk_reason(text) == "numeric_labels"

    def test_url_only_footnote(self):
        text = "1 https://www.eaobservatory.org/jcmt/ 2 http://www.starlink.ac.uk/"
        assert services.non_content_chunk_reason(text) == "url_only"

    def test_japanese_body_is_kept(self):
        assert services.non_content_chunk_reason("磁場強度は約150マイクロガウスと見積もられる。") is None


class TestFormulaPlaceholdersAreResolved:
    def test_placeholder_substituted_with_latex(self):
        text = "The critical line mass is [[FORMULA_0]] for an isothermal filament."
        formulas = [{"id": "[[FORMULA_0]]", "latex": "(M/L)_{crit} =\n 2c_s^2/G", "is_display": True}]
        out = services.resolve_formula_placeholders(text, formulas)
        assert out == "The critical line mass is $(M/L)_{crit} = 2c_s^2/G$ for an isothermal filament."

    def test_missing_or_broken_formula_becomes_fact_placeholder(self):
        text = "A [[FORMULA_0]] B [[FORMULA_1]] C [[FORMULA_2]]"
        formulas = [
            {"id": "[[FORMULA_0]]", "latex": ""},
            {"id": "[[FORMULA_1]]", "latex": "[LaTeX extraction error: manual completion required]"},
        ]
        out = services.resolve_formula_placeholders(text, formulas)
        assert out == "A （数式） B （数式） C （数式）"
        assert "FORMULA_" not in out

    def test_id_match_wins_over_position(self):
        formulas = [{"id": "[[FORMULA_1]]", "latex": "b"}, {"id": "[[FORMULA_0]]", "latex": "a"}]
        assert services.resolve_formula_placeholders("[[FORMULA_0]][[FORMULA_1]]", formulas) == "$a$$b$"

    def test_formulas_as_json_string_and_none(self):
        assert services.resolve_formula_placeholders(
            "x [[FORMULA_0]]", '[{"id": "[[FORMULA_0]]", "latex": "y"}]'
        ) == "x $y$"
        assert services.resolve_formula_placeholders("x [[FORMULA_0]]", None) == "x （数式）"

    def test_search_returns_substituted_text_and_raw_text(self, monkeypatch):
        raw = "The DCF relation [[FORMULA_0]] gives B."
        rows = [
            ("c-1", raw, *_DOC_ROW_COMMON, 0.8, "mat-1", "Method",
             [{"id": "[[FORMULA_0]]", "latex": "B = Q\\sqrt{4\\pi\\rho}\\,\\sigma_v/\\sigma_\\theta"}]),
        ]
        session, results = _search_rows(monkeypatch, rows)
        assert "c.formulas" in session.executed_sql
        assert results[0]["text"] == (
            "The DCF relation $B = Q\\sqrt{4\\pi\\rho}\\,\\sigma_v/\\sigma_\\theta$ gives B."
        )
        assert results[0]["raw_text"] == raw


class TestChunkLocationHintLeadingFragment:
    def test_leading_word_fragment_is_dropped(self):
        hint = services.chunk_location_hint(section_title="", text="s was assumed to be constant across the sample")
        assert hint.startswith("冒頭「was assumed")

    def test_capitalized_start_is_kept(self):
        hint = services.chunk_location_hint(section_title="", text="The sound speed of dark energy")
        assert hint.startswith("冒頭「The sound speed")

    def test_japanese_start_is_kept(self):
        hint = services.chunk_location_hint(section_title="", text="磁場の形は場所によって違う")
        assert hint.startswith("冒頭「磁場の形")
