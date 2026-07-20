"""Tier3-16: status projector の UI 接続（list_materials / get_material）。

対象:
  - `backend/core/status/projector.py` の状態導出の純関数化 (`derive_material_status`)
    と N+1 回避バッチ API (`project_material_statuses_bulk` / `list_material_statuses`)
  - `course_atlas_binding_facts`（atlas binding 走査の next_steps.py との共有。正本は
    Tier3-18 で `backend/core/course_data.py` へ移設済み、projector は再エクスポートのみ）
  - `backend/api/routes/admin.py` の `list_materials` / `get_material` が projector 委譲に
    なったこと（レガシー4値語彙へのマッピングの真理表・一覧と詳細の一貫性・get_material の
    バグ修正=runを反映するようになったこと）

外部 DB / FastAPI サーバーは使わない。DB アクセスはすべて MagicMock によるフェイク
session（`session.execute(...).mappings().fetchone()` 等）で代替する。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BACKEND = Path(__file__).resolve().parents[1]
_SRC = BACKEND.parent / "src"
for _p in (str(BACKEND), str(BACKEND / "api"), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.status import projector  # noqa: E402
from core.status import schema  # noqa: E402


def _result(fetchone=None, fetchall=None, mappings_fetchone=None, mappings_all=None):
    r = MagicMock()
    r.fetchone.return_value = fetchone
    if fetchall is not None:
        r.fetchall.return_value = fetchall
    r.mappings.return_value.fetchone.return_value = mappings_fetchone
    r.mappings.return_value.all.return_value = mappings_all or []
    r.mappings.return_value.fetchall.return_value = mappings_all or []
    return r


def _doc_row(doc_id="doc-1", source_path="mat-1", status="uploaded", chunk_count=0, updated_at=None):
    return {"id": doc_id, "source_path": source_path, "status": status, "chunk_count": chunk_count, "updated_at": updated_at}


def _run_row(run_id="run-1", document_id="doc-1", material_id="mat-1", status="running",
             current_stage="paper_skeleton", error_message="", updated_at=None, completed_at=None,
             created_at=None):
    return {
        "id": run_id, "document_id": document_id, "material_id": material_id, "status": status,
        "current_stage": current_stage, "error_message": error_message, "stage_outputs": {},
        "updated_at": updated_at, "completed_at": completed_at, "created_at": created_at,
    }


# ===========================================================================
# derive_material_status: 行データ -> state の純関数の真理表
# ===========================================================================


class TestDeriveMaterialStatusTruthTable:
    def test_no_run_uses_doc_status_map(self):
        for doc_status, expected in [
            ("uploaded", schema.MATERIAL_STATE_UPLOADED),
            ("processing", schema.MATERIAL_STATE_CHUNKING),
            ("completed", schema.MATERIAL_STATE_ANALYZED),
            ("failed", schema.MATERIAL_STATE_ANALYSIS_FAILED),
            (None, schema.MATERIAL_STATE_UNKNOWN),
            ("garbage", schema.MATERIAL_STATE_UNKNOWN),
        ]:
            ms = projector.derive_material_status("doc-1", "mat-1", doc_status, 0, None, None)
            assert ms.state == expected, f"doc_status={doc_status!r}"

    def test_run_pending_is_chunking(self):
        run = _run_row(status="pending")
        ms = projector.derive_material_status("doc-1", "mat-1", "uploaded", 0, None, run)
        assert ms.state == schema.MATERIAL_STATE_CHUNKING

    def test_run_running_is_analyzing(self):
        run = _run_row(status="running")
        ms = projector.derive_material_status("doc-1", "mat-1", "processing", 0, None, run)
        assert ms.state == schema.MATERIAL_STATE_ANALYZING
        assert ms.stage == "paper_skeleton"

    def test_run_failed_is_analysis_failed_with_reason(self):
        run = _run_row(status="failed", error_message="boom")
        ms = projector.derive_material_status("doc-1", "mat-1", "processing", 0, None, run)
        assert ms.state == schema.MATERIAL_STATE_ANALYSIS_FAILED
        assert ms.reason == "boom"

    def test_run_completed_with_chunks_is_analyzed(self):
        run = _run_row(status="completed")
        ms = projector.derive_material_status("doc-1", "mat-1", "completed", 5, None, run)
        assert ms.state == schema.MATERIAL_STATE_ANALYZED

    def test_run_completed_without_chunks_is_analyzing(self):
        """既知の縮退ケース: 完了runでもchunk_count=0なら analyzing のまま
        （抽出成功の代理指標が無い, S1既存仕様）。"""
        run = _run_row(status="completed")
        ms = projector.derive_material_status("doc-1", "mat-1", "completed", 0, None, run)
        assert ms.state == schema.MATERIAL_STATE_ANALYZING


# ===========================================================================
# admin.py の legacy 4値語彙マッピング（真理表）
# ===========================================================================


class TestLegacyMaterialStatusMapping:
    """`routes.admin._legacy_material_status` の真理表。

    uploaded/chunking/unknown は意図的に None（呼び出し側フォールバック維持）、
    analyzing/analyzed/analysis_failed だけが legacy 4値語彙へ変換される。
    """

    @pytest.fixture(autouse=True)
    def _import_admin(self):
        import routes.admin as admin_mod
        self.admin_mod = admin_mod

    @pytest.mark.parametrize(
        "state,expected",
        [
            (schema.MATERIAL_STATE_UPLOADED, None),
            (schema.MATERIAL_STATE_CHUNKING, None),
            (schema.MATERIAL_STATE_ANALYZING, "processing"),
            (schema.MATERIAL_STATE_ANALYZED, "completed"),
            (schema.MATERIAL_STATE_ANALYSIS_FAILED, "failed"),
            (schema.MATERIAL_STATE_UNKNOWN, None),
        ],
    )
    def test_mapping_truth_table(self, state, expected):
        ms = schema.MaterialStatus(document_id="d1", material_id="m1", state=state)
        assert self.admin_mod._legacy_material_status(ms) == expected

    def test_no_run_no_override_preserves_memory_status(self):
        """run が存在しない場合（state=uploaded/chunking）はメモリ上書き値を
        壊さない（アップロード直後の即時フィードバックの保持, race window regression guard）。
        """
        # 例: doc_status='uploaded' でメモリだけが 'processing' を示す直後の状態。
        ms = projector.derive_material_status("doc-1", "mat-1", "uploaded", 0, None, None)
        assert self.admin_mod._legacy_material_status(ms) is None

    def test_completed_no_run_override_is_idempotent(self):
        """doc_status='completed' かつ run 無し（旧データ）は legacy でも 'completed'
        （既に同じ値なので上書きしても無害）。"""
        ms = projector.derive_material_status("doc-1", "mat-1", "completed", 3, None, None)
        assert self.admin_mod._legacy_material_status(ms) == "completed"


# ===========================================================================
# バッチ導出（N+1 回避）
# ===========================================================================


class TestProjectMaterialStatusesBulk:
    def test_bulk_matches_single_for_multiple_materials(self):
        """バッチ導出は単体 project_material_status と同じ結果を返す。"""
        doc1 = _doc_row("doc-1", "mat-1", "completed", 5)
        doc2 = _doc_row("doc-2", "mat-2", "processing", 0)
        run1 = _run_row("run-1", "doc-1", "mat-1", status="completed")
        run2 = _run_row("run-2", "doc-2", "mat-2", status="running")

        # 単体呼び出し（フェイク session 1回ずつ）
        s1 = MagicMock()
        s1.execute.side_effect = [_result(mappings_fetchone=doc1), _result(mappings_fetchone=run1)]
        single1 = projector.project_material_status(s1, "mat-1")

        s2 = MagicMock()
        s2.execute.side_effect = [_result(mappings_fetchone=doc2), _result(mappings_fetchone=run2)]
        single2 = projector.project_material_status(s2, "mat-2")

        # バッチ呼び出し（フェイク session は documents 1クエリ + runs 1クエリのみ）
        sb = MagicMock()
        sb.execute.side_effect = [
            _result(mappings_all=[doc1, doc2]),
            _result(mappings_all=[run1, run2]),
        ]
        bulk = projector.project_material_statuses_bulk(sb, ["mat-1", "mat-2"])

        assert bulk["mat-1"] == single1
        assert bulk["mat-2"] == single2
        assert sb.execute.call_count == 2

    def test_bulk_unknown_for_missing_document(self):
        sb = MagicMock()
        sb.execute.side_effect = [_result(mappings_all=[]), _result(mappings_all=[])]
        bulk = projector.project_material_statuses_bulk(sb, ["missing-ref"])
        assert bulk["missing-ref"].state == schema.MATERIAL_STATE_UNKNOWN
        # documents 0件なら runs クエリは発行しない（ids が空のため）。
        assert sb.execute.call_count == 1

    def test_bulk_empty_input_issues_no_queries(self):
        sb = MagicMock()
        bulk = projector.project_material_statuses_bulk(sb, [])
        assert bulk == {}
        assert sb.execute.call_count == 0

    @pytest.mark.parametrize("n", [1, 5, 20])
    def test_query_count_is_constant_regardless_of_material_count(self, n):
        """documents 1クエリ + document_analysis_runs 1クエリのセットベースで、
        material 数に依らず定数回のクエリしか発行しない（N+1回避）。"""
        refs = [f"mat-{i}" for i in range(n)]
        docs = [_doc_row(f"doc-{i}", f"mat-{i}", "completed", 1) for i in range(n)]
        runs = [_run_row(f"run-{i}", f"doc-{i}", f"mat-{i}", status="completed") for i in range(n)]

        sb = MagicMock()
        sb.execute.side_effect = [_result(mappings_all=docs), _result(mappings_all=runs)]
        bulk = projector.project_material_statuses_bulk(sb, refs)

        assert len(bulk) == n
        assert sb.execute.call_count == 2


class TestListMaterialStatusesUsesBulk:
    """`list_material_statuses`（旧: ループでN+1）がバッチ導出に一本化されたこと。"""

    def test_list_material_statuses_issues_constant_queries(self):
        refs = ["mat-1", "mat-2", "mat-3"]
        docs = [_doc_row(f"doc-{i}", r, "uploaded", 0) for i, r in enumerate(refs)]
        sb = MagicMock()
        sb.execute.side_effect = [_result(mappings_all=docs), _result(mappings_all=[])]
        out = projector.list_material_statuses(sb, refs)
        assert len(out) == 3
        assert sb.execute.call_count == 2

    def test_preserves_input_order_and_unknown_fallback(self):
        docs = [_doc_row("doc-1", "mat-1", "uploaded", 0)]
        sb = MagicMock()
        sb.execute.side_effect = [_result(mappings_all=docs), _result(mappings_all=[])]
        out = projector.list_material_statuses(sb, ["mat-1", "missing"])
        assert [s.material_id for s in out] == ["mat-1", "missing"]
        assert out[1].state == schema.MATERIAL_STATE_UNKNOWN


# ===========================================================================
# atlas binding 走査の共有（course_atlas_binding_facts）
# ===========================================================================


class TestCourseAtlasBindingFactsShared:
    def test_no_cartridge_no_node(self):
        assert projector.course_atlas_binding_facts({}) == (False, False)

    def test_cartridge_without_node(self):
        data = {"cartridge_id": "particle_physics", "topics": []}
        assert projector.course_atlas_binding_facts(data) == (True, False)

    def test_node_without_cartridge_toplevel_topics(self):
        data = {"topics": [{"id": "t1", "atlas_node_id": "n1"}]}
        assert projector.course_atlas_binding_facts(data) == (False, True)

    def test_node_nested_in_chapters(self):
        data = {"chapters": [{"topics": [{"atlas_node_id": "n1"}]}]}
        assert projector.course_atlas_binding_facts(data) == (False, True)

    def test_non_dict_input_is_false_false(self):
        assert projector.course_atlas_binding_facts(None) == (False, False)

    def test_course_atlas_bound_is_and_of_both(self):
        # cartridge のみ -> bound しない
        assert projector._course_atlas_bound({"cartridge_id": "x"}) is False
        # node のみ -> bound しない（projector は AND、next_steps とは方針が異なる）
        assert projector._course_atlas_bound({"topics": [{"atlas_node_id": "n1"}]}) is False
        # 両方あって初めて bound
        assert projector._course_atlas_bound(
            {"cartridge_id": "x", "topics": [{"atlas_node_id": "n1"}]}
        ) is True

    def test_next_steps_delegates_to_shared_helper(self):
        """next_steps.py が独自に走査を再実装せず共有プリミティブを呼ぶこと（構造テスト）。

        Tier3-18: 正本は `core/course_data.py::course_atlas_binding_facts` へ移設され、
        next_steps.py は（projector 経由ではなく）直接そこから import するようになった。
        `core/status/projector.py` は後方互換の再エクスポートとして関数を維持する
        （下の test_projector_reexports_course_atlas_binding_facts_from_course_data 参照）。
        """
        src = (BACKEND / "core" / "admin_assistant" / "next_steps.py").read_text(encoding="utf-8")
        assert "from core.course_data import course_atlas_binding_facts" in src
        assert "course_atlas_binding_facts(data)" in src

    def test_projector_reexports_course_atlas_binding_facts_from_course_data(self):
        """core/status/projector.py は core/course_data.py の関数をそのまま再エクスポートする。"""
        from core import course_data

        assert projector.course_atlas_binding_facts is course_data.course_atlas_binding_facts


# ===========================================================================
# admin.py: list_materials / get_material の projector 委譲（構造テスト）
# ===========================================================================


class TestAdminRoutesDelegateToProjector:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = (BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")

    def _list_materials_body(self) -> str:
        import re
        m = re.search(r"def list_materials\b.*?return materials", self.src, re.DOTALL)
        assert m
        return m.group(0)

    def _get_material_body(self) -> str:
        import re
        m = re.search(r"def get_material\b.*?return MaterialOut", self.src, re.DOTALL)
        assert m
        return m.group(0)

    def test_list_materials_no_longer_hand_rolls_status_override(self):
        body = self._list_materials_body()
        assert 'status = "processing"' not in body
        assert 'status = "completed"' not in body
        assert "status_projector.derive_material_status(" in body
        assert "_legacy_material_status(" in body

    def test_list_materials_latest_run_query_selects_run_id(self):
        body = self._list_materials_body()
        assert "id::text AS id" in body

    def test_get_material_calls_projector_for_run_aware_status(self):
        body = self._get_material_body()
        assert "status_projector.project_material_status(" in body
        assert "_legacy_material_status(" in body


# ===========================================================================
# get_material の挙動修正: run を反映するようになったこと（機能テスト）
# ===========================================================================


class TestGetMaterialReflectsRun:
    @patch("routes.admin.get_user_group_ids", return_value=[])
    @patch("routes.admin._pg_session")
    def test_get_material_reflects_failed_run_despite_completed_doc_status(
        self, mock_pg_session, _mock_groups,
    ):
        """従来 get_material は run を一切見ておらず、documents.status='completed' の
        まま古い値を返すバグがあった。新実装は最新 run（failed）を反映する。"""
        import routes.admin as admin_mod

        own_record = ("mat-1", "file.pdf", "Title", "completed", None, None, "private", None, 3)
        doc_row = _doc_row("doc-uuid-1", "mat-1", "completed", 3)
        run_row = _run_row("run-1", "doc-uuid-1", "mat-1", status="failed", error_message="boom")

        mock_session = MagicMock()
        mock_session.execute.side_effect = [
            _result(fetchone=own_record),
            _result(mappings_fetchone=doc_row),
            _result(mappings_fetchone=run_row),
        ]
        mock_pg_session.return_value = mock_session

        result = admin_mod.get_material("mat-1", current_user={"id": "user-1"})

        assert result.status == "failed"

    @patch("routes.admin.get_user_group_ids", return_value=[])
    @patch("routes.admin._pg_session")
    def test_get_material_reflects_running_run(self, mock_pg_session, _mock_groups):
        import routes.admin as admin_mod

        own_record = ("mat-2", "file2.pdf", "Title2", "processing", None, None, "private", None, 0)
        doc_row = _doc_row("doc-uuid-2", "mat-2", "processing", 0)
        run_row = _run_row("run-2", "doc-uuid-2", "mat-2", status="running")

        mock_session = MagicMock()
        mock_session.execute.side_effect = [
            _result(fetchone=own_record),
            _result(mappings_fetchone=doc_row),
            _result(mappings_fetchone=run_row),
        ]
        mock_pg_session.return_value = mock_session

        result = admin_mod.get_material("mat-2", current_user={"id": "user-1"})

        assert result.status == "processing"

    @patch("routes.admin.get_user_group_ids", return_value=[])
    @patch("routes.admin._pg_session")
    def test_get_material_not_found_still_404(self, mock_pg_session, _mock_groups):
        from fastapi import HTTPException
        import routes.admin as admin_mod

        mock_session = MagicMock()
        mock_session.execute.side_effect = [_result(fetchone=None)]
        mock_pg_session.return_value = mock_session

        with pytest.raises(HTTPException) as exc_info:
            admin_mod.get_material("missing", current_user={"id": "user-1"})
        assert exc_info.value.status_code == 404

    @patch("routes.admin.get_user_group_ids", return_value=[])
    @patch("routes.admin._pg_session")
    def test_get_material_no_run_keeps_doc_status(self, mock_pg_session, _mock_groups):
        """run が存在しない教材は documents.status をそのまま返す（回帰無し）。"""
        import routes.admin as admin_mod

        own_record = ("mat-3", "file3.pdf", "Title3", "uploaded", None, None, "private", None, 0)
        doc_row = _doc_row("doc-uuid-3", "mat-3", "uploaded", 0)

        mock_session = MagicMock()
        mock_session.execute.side_effect = [
            _result(fetchone=own_record),
            _result(mappings_fetchone=doc_row),
            _result(mappings_fetchone=None),  # no run
        ]
        mock_pg_session.return_value = mock_session

        result = admin_mod.get_material("mat-3", current_user={"id": "user-1"})

        assert result.status == "uploaded"


# ===========================================================================
# list_materials と get_material の一貫性
# ===========================================================================


class TestListAndGetMaterialConsistency:
    """同一の doc/run データに対して、list_materials 内部で使われる
    `derive_material_status` と get_material が使う `project_material_status` が
    同じ legacy status を導くこと（一覧と詳細の食い違いバグの再発防止）。
    """

    @pytest.mark.parametrize(
        "doc_status,chunk_count,run_status,expected_legacy",
        [
            ("uploaded", 0, None, None),
            ("processing", 0, None, None),
            ("completed", 3, None, "completed"),
            ("failed", 0, None, "failed"),
            ("processing", 0, "running", "processing"),
            ("processing", 0, "failed", "failed"),
            ("processing", 5, "completed", "completed"),
        ],
    )
    def test_list_and_get_derive_same_legacy_status(
        self, doc_status, chunk_count, run_status, expected_legacy,
    ):
        import routes.admin as admin_mod

        run = _run_row("run-1", "doc-1", "mat-1", status=run_status) if run_status else None

        # list_materials 相当（derive_material_status を直接呼ぶ内部処理と同型）
        list_mstatus = projector.derive_material_status(
            "doc-1", "mat-1", doc_status, chunk_count, None, run,
        )
        list_legacy = admin_mod._legacy_material_status(list_mstatus)

        # get_material 相当（project_material_status 経由）
        s = MagicMock()
        s.execute.side_effect = [
            _result(mappings_fetchone=_doc_row("doc-1", "mat-1", doc_status, chunk_count)),
            _result(mappings_fetchone=run),
        ]
        get_mstatus = projector.project_material_status(s, "mat-1")
        get_legacy = admin_mod._legacy_material_status(get_mstatus)

        assert list_legacy == get_legacy == expected_legacy


# ===========================================================================
# list_materials: analysis_options（最新 run の options JSONB / run 無しは None）
# ===========================================================================


class TestListMaterialsAnalysisOptions:
    """解析再開の analyze_images 継承（画像パイプライン §3-2）のフロント契約。

    フロント（admin.js）が再解析モーダルで前回選択を復元できるよう、
    `GET /api/admin/materials` の各要素に最新 `document_analysis_runs.options` を
    **analysis_options**（確定契約フィールド名）として返す。run が無ければ None。
    既存の latest_runs バルククエリ（DISTINCT ON）に列を足すだけで N+1 は発生しない。
    """

    @patch("routes.admin.get_storage_client")
    @patch("routes.admin.get_user_group_ids", return_value=[])
    @patch("routes.admin._pg_session")
    def test_list_materials_returns_latest_run_options_or_none(
        self, mock_pg_session, _mock_groups, mock_storage,
    ):
        import routes.admin as admin_mod

        records = [
            # (source_path, filename, title, status, created_at, knowledge_graph,
            #  visibility, group_id, chunk_count, document_id, authors, year, doc_type)
            ("mat-1", "a.pdf", "A", "completed", None, None, "private", None, 3,
             "doc-1", [], None, None),
            ("mat-2", "b.pdf", "B", "uploaded", None, None, "private", None, 0,
             "doc-2", [], None, None),
        ]
        run = _run_row("run-1", "doc-1", "mat-1", status="completed")
        run["options"] = {"analyze_images": True}

        mock_session = MagicMock()
        mock_session.execute.side_effect = [
            _result(fetchall=records),
            _result(mappings_all=[run]),
        ]
        mock_pg_session.return_value = mock_session
        mock_storage.return_value.list_objects.return_value = []

        materials = admin_mod.list_materials(current_user={"id": "user-1"})

        by_mid = {m.material_id: m for m in materials}
        assert by_mid["mat-1"].analysis_options == {"analyze_images": True}
        # run が無い教材は None（空 dict ではなく「情報なし」を正直に返す）。
        assert by_mid["mat-2"].analysis_options is None

    @patch("routes.admin.get_storage_client")
    @patch("routes.admin.get_user_group_ids", return_value=[])
    @patch("routes.admin._pg_session")
    def test_run_with_empty_options_returns_empty_dict_not_none(
        self, mock_pg_session, _mock_groups, mock_storage,
    ):
        """migration 041 の NOT NULL DEFAULT '{}' により、run があれば options は
        少なくとも {}。「run はあるが選択情報なし」を None と区別して返す。"""
        import routes.admin as admin_mod

        records = [
            ("mat-1", "a.pdf", "A", "completed", None, None, "private", None, 3,
             "doc-1", [], None, None),
        ]
        run = _run_row("run-1", "doc-1", "mat-1", status="completed")
        run["options"] = {}

        mock_session = MagicMock()
        mock_session.execute.side_effect = [
            _result(fetchall=records),
            _result(mappings_all=[run]),
        ]
        mock_pg_session.return_value = mock_session
        mock_storage.return_value.list_objects.return_value = []

        materials = admin_mod.list_materials(current_user={"id": "user-1"})
        assert materials[0].analysis_options == {}

    def test_latest_runs_bulk_query_selects_options_column(self):
        """options は既存のバルククエリ（DISTINCT ON）に列追加で取得する（N+1 禁止）。"""
        import re

        src = (BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
        m = re.search(r"def list_materials\b.*?return materials", src, re.DOTALL)
        assert m
        body = m.group(0)
        assert "stage_outputs, options, updated_at" in body
        assert 'analysis_options=(run_data.get("options") if run else None)' in body
