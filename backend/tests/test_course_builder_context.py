"""Issue #230: コース構築コンテキストをAgent生成済み理論コンポーネント中心に置き換える。

_build_material_context() が theory_components / theory_component_graphs / theory_claims を
主入力として使用し、documents.knowledge_graph はAgent未実行の場合のfallbackに限定されること、
および _COURSE_BUILDER_SYSTEM_PROMPT が理論コンポーネント中心の方針に更新されていることを検証する。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure api/ is importable
_backend_dir = str(Path(__file__).resolve().parents[1])
_api_dir = str(Path(__file__).resolve().parents[1] / "api")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if _api_dir not in sys.path:
    sys.path.insert(0, _api_dir)

# Python 3.11+ では pkgutil.resolve_name が routes パッケージ属性の
# 遅延解決をサポートしないため、patch() 前に明示的にロードしておく
import importlib as _importlib
try:
    import routes.admin as _routes_admin_mod  # noqa: F401
except Exception:
    pass

# Load test fixture data
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
NEUTRALINO_SEED = json.loads(
    (FIXTURE_DIR / "neutralino_seed.json").read_text(encoding="utf-8")
)

DOC_UUID = NEUTRALINO_SEED["documents"][0]["doc_uuid"]


# ---------------------------------------------------------------------------
# Helper: build DB mock rows from fixture
# ---------------------------------------------------------------------------

def _make_doc_rows(seed=NEUTRALINO_SEED):
    """documents を DB 行形式 (source_path, title, filename, doc_uuid, status, knowledge_graph) に変換。"""
    rows = []
    for doc in seed["documents"]:
        rows.append((
            doc["material_id"],
            doc["title"],
            doc["filename"],
            doc["doc_uuid"],
            doc["status"],
            doc["knowledge_graph"],
        ))
    return rows


def _make_component_rows(seed=NEUTRALINO_SEED):
    """theory_components を DB 行形式に変換。
    (id, document_id, name, component_type, component_type_text,
     summary, inputs, outputs, preconditions, cautions,
     source_chunks, evidence_claims, review_status, maturity_level)
    """
    rows = []
    for c in seed["theory_components"]:
        rows.append((
            c["id"],
            c["document_id"],
            c["name"],
            c["component_type"],
            c["component_type_text"],
            c["summary"],
            c["inputs"],
            c["outputs"],
            c["preconditions"],
            c["cautions"],
            c["source_chunks"],
            c["evidence_claims"],
            c["review_status"],
            c["maturity_level"],
        ))
    return rows


def _make_graph_rows(seed=NEUTRALINO_SEED):
    """theory_component_graphs を DB 行形式 (document_id, graph_json) に変換。"""
    return [
        (g["document_id"], g["graph_json"])
        for g in seed["theory_component_graphs"]
    ]


def _make_claim_rows(seed=NEUTRALINO_SEED):
    """theory_claims を DB 行形式に変換。
    (id, document_id, claim_type, text, normalized_text,
     source_scope, evidence_text, support_status, review_status)
    """
    rows = []
    for cl in seed["theory_claims"]:
        rows.append((
            cl["id"],
            cl["document_id"],
            cl["claim_type"],
            cl["text"],
            cl["normalized_text"],
            cl["source_scope"],
            cl["evidence_text"],
            cl["support_status"],
            cl["review_status"],
        ))
    return rows


def _make_chunk_rows(seed=NEUTRALINO_SEED):
    """chunks を DB 行形式 (material_id, chunk_index, section_id, text, page_start, page_end) に変換。"""
    rows = []
    for ch in seed["chunks"]:
        rows.append((
            ch["material_id"],
            ch["chunk_index"],
            ch.get("section", ch.get("section_id", "")),
            ch["text"],
            None,  # page_start
            None,  # page_end
        ))
    return rows


def _make_analysis_rows(seed=NEUTRALINO_SEED):
    """analysis_runs を DB 行形式 (document_id, status, stage_outputs) に変換。"""
    return [
        (r["document_id"], r["status"], r["stage_outputs"])
        for r in seed["analysis_runs"]
    ]


def _full_side_effect(seed=NEUTRALINO_SEED):
    """_build_material_context の全6クエリに対応する side_effect リストを返す。"""
    return [
        _make_doc_rows(seed),
        _make_component_rows(seed),
        _make_graph_rows(seed),
        _make_claim_rows(seed),
        _make_chunk_rows(seed),
        _make_analysis_rows(seed),
    ]


# ===========================================================================
# 1. _build_material_context 単体テスト
# ===========================================================================

class TestBuildMaterialContext:
    """_build_material_context ヘルパー関数のテスト。"""

    def _import_func(self):
        from routes.admin import _build_material_context
        return _build_material_context

    def test_returns_none_for_empty_ids(self):
        """material_ids が空の場合 None を返すこと。"""
        func = self._import_func()
        assert func([]) is None

    @patch("routes.admin._pg_session")
    def test_returns_none_when_no_docs_found(self, mock_pg):
        """DB にマッチするドキュメントがない場合 None を返すこと。"""
        func = self._import_func()
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = []
        mock_pg.return_value = mock_session

        result = func(["nonexistent-id"])
        assert result is None

    @patch("routes.admin._pg_session")
    def test_includes_theory_components(self, mock_pg):
        """theory_components の名前・summaryがコンテキストに含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.side_effect = _full_side_effect()
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert ctx is not None
        assert "Neutralino Dark Matter Candidate" in ctx
        assert "Loop-Induced Annihilation Amplitude" in ctx
        assert "Partial-Wave Unitarity Constraint" in ctx

    @patch("routes.admin._pg_session")
    def test_includes_component_dependencies(self, mock_pg):
        """theory_component_graphs の依存エッジがコンテキストに含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.side_effect = _full_side_effect()
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert ctx is not None
        assert "comp-uuid-0001" in ctx
        assert "comp-uuid-0002" in ctx
        assert "->" in ctx or "→" in ctx

    @patch("routes.admin._pg_session")
    def test_includes_theory_claims(self, mock_pg):
        """theory_claims のテキストがコンテキストに含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.side_effect = _full_side_effect()
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert ctx is not None
        assert "claim-uuid-0001" in ctx
        assert "Majorana fermion" in ctx

    @patch("routes.admin._pg_session")
    def test_includes_chunk_excerpts(self, mock_pg):
        """chunks の原文抜粋がコンテキストに含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.side_effect = _full_side_effect()
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert ctx is not None
        assert "dark matter" in ctx.lower()

    @patch("routes.admin._pg_session")
    def test_chunk_text_truncated_within_budget(self, mock_pg):
        """チャンクテキストが _MAX_CHUNK_CHARS_PER_MATERIAL 以内に収まること。"""
        func = self._import_func()
        from routes.admin import _MAX_CHUNK_CHARS_PER_MATERIAL

        mock_session = MagicMock()
        doc_rows = [("mat-long", "Long Doc", "long.pdf", "uuid-long-001", "completed", {})]
        long_text = "A" * 10000
        chunk_rows = [
            ("mat-long", 0, "", long_text, None, None),
            ("mat-long", 1, "", long_text, None, None),
        ]
        mock_session.execute.return_value.fetchall.side_effect = [
            doc_rows,   # doc query
            [],         # components (empty → fallback)
            [],         # graphs
            [],         # claims
            chunk_rows, # chunks
            [],         # analysis runs
        ]
        mock_pg.return_value = mock_session

        ctx = func(["mat-long"])
        assert ctx is not None
        chunk_text_portions = [line for line in ctx.split("\n") if line.startswith("AAA")]
        total_a_chars = sum(line.count("A") for line in chunk_text_portions)
        assert total_a_chars <= _MAX_CHUNK_CHARS_PER_MATERIAL + 100

    @patch("routes.admin._pg_session")
    def test_fallback_to_knowledge_graph_when_no_agent_data(self, mock_pg):
        """theory_components が存在しない場合、旧 knowledge_graph にfallbackすること。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = [("mat-legacy", "Legacy Doc", "legacy.pdf", "uuid-legacy-001", "completed", {
            "domain": "Particle Physics / Dark Matter",
            "summary": "Legacy knowledge graph summary about neutralino annihilation",
            "concepts": [
                {"name": "Neutralino", "type": "PARTICLE", "description": "DM candidate"},
                {"name": "Dark Matter", "type": "PHENOMENON", "description": "Non-luminous matter"},
            ],
        })]
        mock_session.execute.return_value.fetchall.side_effect = [
            doc_rows,  # doc query
            [],        # no components → fallback
            [],        # no graphs
            [],        # no claims
            [],        # no chunks
            [],        # no analysis runs
        ]
        mock_pg.return_value = mock_session

        ctx = func(["mat-legacy"])
        assert ctx is not None
        assert "legacy" in ctx.lower() or "Neutralino" in ctx
        assert "Particle Physics" in ctx

    @patch("routes.admin._pg_session")
    def test_no_fallback_when_agent_data_exists(self, mock_pg):
        """theory_components が存在する場合、旧 knowledge_graph の概念一覧が主コンテキストに出ないこと。"""
        func = self._import_func()
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.side_effect = _full_side_effect()
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert ctx is not None
        # Agent component data should dominate
        assert "理論コンポーネント" in ctx
        # Legacy knowledge_graph concepts section should NOT appear as primary content
        assert "**主要概念 (legacy):**" not in ctx

    @patch("routes.admin._pg_session")
    def test_context_section_header(self, mock_pg):
        """コンテキストに「Agent解析済み教材コンテキスト」ヘッダが含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.side_effect = _full_side_effect()
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert ctx is not None
        assert "Agent解析済み教材コンテキスト" in ctx

    @patch("routes.admin._pg_session")
    def test_warning_when_pipeline_incomplete(self, mock_pg):
        """Agentパイプライン未完了の場合、警告メッセージがコンテキストに含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = [("mat-pending", "Pending Doc", "pending.pdf", "uuid-pending-001", "processing", {})]
        mock_session.execute.return_value.fetchall.side_effect = [
            doc_rows,
            [],  # no components
            [],  # no graphs
            [],  # no claims
            [],  # no chunks
            [("uuid-pending-001", "running", {})],  # analysis running
        ]
        mock_pg.return_value = mock_session

        ctx = func(["mat-pending"])
        assert ctx is not None
        assert "警告" in ctx or "未完了" in ctx

    @patch("routes.admin._pg_session")
    def test_document_structure_included(self, mock_pg):
        """document_structure のセクション一覧がコンテキストに含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.side_effect = _full_side_effect()
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert ctx is not None
        assert "文書構造" in ctx
        assert "Introduction" in ctx or "Framework" in ctx

    @patch("routes.admin._pg_session")
    def test_material_title_header(self, mock_pg):
        """各教材のタイトルが ### ヘッダとして出力されること。"""
        func = self._import_func()
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.side_effect = _full_side_effect()
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert ctx is not None
        assert "### 教材:" in ctx


# ===========================================================================
# 2. サイズ制限テスト
# ===========================================================================

class TestMaterialContextSizeLimits:
    """_build_material_context のサイズ制限を検証。"""

    def _import_func(self):
        from routes.admin import _build_material_context
        return _build_material_context

    @patch("routes.admin._pg_session")
    def test_components_capped_at_max(self, mock_pg):
        """コンポーネント数が _MAX_COMPONENTS_PER_MATERIAL を超えないこと。"""
        from routes.admin import _MAX_COMPONENTS_PER_MATERIAL
        func = self._import_func()
        mock_session = MagicMock()

        doc_uuid = "uuid-many-001"
        doc_rows = [("mat-many", "Many Doc", "many.pdf", doc_uuid, "completed", {})]
        many_components = [
            (f"comp-{i:04d}", doc_uuid, f"Component {i}", "theory", "", f"Summary {i}",
             [], [], [], [], [], [], "teacher_review_required", "paper_claim")
            for i in range(60)
        ]
        mock_session.execute.return_value.fetchall.side_effect = [
            doc_rows, many_components, [], [], [], [],
        ]
        mock_pg.return_value = mock_session

        ctx = func(["mat-many"])
        assert ctx is not None
        # Only up to _MAX_COMPONENTS_PER_MATERIAL components should appear
        component_count = ctx.count("Component ID:")
        assert component_count <= _MAX_COMPONENTS_PER_MATERIAL

    @patch("routes.admin._pg_session")
    def test_claims_capped_at_max(self, mock_pg):
        """Claim数が _MAX_CLAIMS_PER_MATERIAL を超えないこと。"""
        from routes.admin import _MAX_CLAIMS_PER_MATERIAL
        func = self._import_func()
        mock_session = MagicMock()

        doc_uuid = "uuid-claims-001"
        doc_rows = [("mat-claims", "Claims Doc", "claims.pdf", doc_uuid, "completed", {})]
        many_claims = [
            (f"claim-{i:04d}", doc_uuid, "result", f"Claim text {i}", f"Normalized {i}",
             {}, f"Evidence {i}", "source_backed", "teacher_review_required")
            for i in range(100)
        ]
        mock_session.execute.return_value.fetchall.side_effect = [
            doc_rows, [], [], many_claims, [], [],
        ]
        mock_pg.return_value = mock_session

        ctx = func(["mat-claims"])
        assert ctx is not None
        claim_count = ctx.count("Claim ID:")
        assert claim_count <= _MAX_CLAIMS_PER_MATERIAL

    @patch("routes.admin._pg_session")
    def test_graph_edges_capped_at_max(self, mock_pg):
        """グラフエッジ数が _MAX_GRAPH_EDGES_PER_MATERIAL を超えないこと。"""
        from routes.admin import _MAX_GRAPH_EDGES_PER_MATERIAL
        func = self._import_func()
        mock_session = MagicMock()

        doc_uuid = "uuid-graph-001"
        doc_rows = [("mat-graph", "Graph Doc", "graph.pdf", doc_uuid, "completed", {})]
        many_edges = [
            {"source": f"comp-{i}", "target": f"comp-{i+1}", "type": "requires"}
            for i in range(100)
        ]
        graph_rows = [(doc_uuid, {"nodes": [], "edges": many_edges})]
        mock_session.execute.return_value.fetchall.side_effect = [
            doc_rows, [], graph_rows, [], [], [],
        ]
        mock_pg.return_value = mock_session

        ctx = func(["mat-graph"])
        assert ctx is not None
        edge_count = ctx.count("comp-")
        # Each edge line contains 2 comp- references (source + target)
        assert edge_count <= _MAX_GRAPH_EDGES_PER_MATERIAL * 2 + 10  # allow small overhead


# ===========================================================================
# 3. システムプロンプトのテスト
# ===========================================================================

class TestCourseBuilderSystemPrompt:
    """_COURSE_BUILDER_SYSTEM_PROMPT が理論コンポーネント中心の方針を要求していることを検証。"""

    def _get_prompt(self):
        from routes.admin import _COURSE_BUILDER_SYSTEM_PROMPT
        return _COURSE_BUILDER_SYSTEM_PROMPT

    def test_prompt_requires_source_adherence(self):
        """プロンプトがソース文献への準拠を要求していること。"""
        prompt = self._get_prompt()
        assert "教材の記述" in prompt or "教材に記載" in prompt
        assert "追加してはならない" in prompt or "禁止" in prompt

    def test_prompt_prohibits_hallucination(self):
        """教材にないトピックの追加を明示的に禁止していること。"""
        prompt = self._get_prompt()
        assert "一般的だから" in prompt

    def test_prompt_mentions_theory_components_as_primary(self):
        """プロンプトが theory_components をコース章・トピック候補の主入力として言及していること。"""
        prompt = self._get_prompt()
        assert "theory_components" in prompt

    def test_prompt_mentions_dependency_graph(self):
        """プロンプトが theory_component_graphs の依存関係を学習順序の主判断として言及していること。"""
        prompt = self._get_prompt()
        assert "theory_component_graphs" in prompt
        assert "依存関係" in prompt

    def test_prompt_mentions_claims_for_evidence(self):
        """プロンプトが theory_claims を根拠確認用として言及していること。"""
        prompt = self._get_prompt()
        assert "theory_claims" in prompt

    def test_prompt_treats_knowledge_graph_as_fallback(self):
        """プロンプトが documents.knowledge_graph をfallbackとして位置づけていること。"""
        prompt = self._get_prompt()
        assert "fallback" in prompt or "旧教材" in prompt or "Agentパイプライン未実行" in prompt

    def test_prompt_requires_stepwise_structure(self):
        """段階的構造化（依存関係に基づくシラバス）を要求していること。"""
        prompt = self._get_prompt()
        assert "段階的" in prompt or "依存関係" in prompt

    def test_prompt_requires_prerequisites_ordering(self):
        """前提知識の順序付けルールが含まれていること。"""
        prompt = self._get_prompt()
        assert "prerequisites" in prompt
        assert "前提知識" in prompt

    def test_prompt_is_in_japanese(self):
        """プロンプトが日本語で記述されていること。"""
        prompt = self._get_prompt()
        assert "日本語" in prompt

    def test_prompt_has_json_schema(self):
        """コースドラフトのJSONスキーマ定義が含まれていること。"""
        prompt = self._get_prompt()
        assert "course_draft" in prompt
        assert '"chapters"' in prompt
        assert '"topics"' in prompt

    def test_prompt_chunks_as_supplementary(self):
        """プロンプトが chunks を補助情報として位置づけていること。"""
        prompt = self._get_prompt()
        assert "チャンク" in prompt or "chunks" in prompt


# ===========================================================================
# 4. course_builder_chat エンドポイントの結合テスト
# ===========================================================================

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient
except (ImportError, RuntimeError):
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI,
    reason="FastAPI not installed (run in Docker for full API tests)",
)


@pytest.fixture
def client():
    if not _HAS_FASTAPI:
        pytest.skip("FastAPI not installed")
    from api.main import app
    return TestClient(app)


@pytest.fixture
def teacher_token():
    import jwt
    payload = {
        "sub": "test-teacher-id",
        "role": "TEACHER",
        "username": "teacher1",
        "email": "teacher1@test.com",
    }
    return jwt.encode(payload, "test-secret-key", algorithm="HS256")


@pytest.fixture
def auth_headers(teacher_token):
    return {"Authorization": f"Bearer {teacher_token}"}


@_skip_no_fastapi
class TestCourseBuilderChatContextInjection:
    """course_builder_chat エンドポイントが教材コンテキストをLLMに渡すことを検証。"""

    @patch("routes.admin.save_cb_session")
    @patch("routes.admin.generate_text")
    @patch("routes.admin._build_material_context")
    def test_context_injected_into_messages(
        self, mock_build_ctx, mock_gen, mock_save, client, auth_headers,
    ):
        """_build_material_context の結果がメッセージに注入されること。"""
        mock_build_ctx.return_value = (
            "## Agent解析済み教材コンテキスト\n### 教材: Neutralino Paper\n"
            "#### 理論コンポーネント\n- Component ID: comp-uuid-0001\n"
            "  Name: Neutralino Dark Matter Candidate\n"
        )
        mock_gen.return_value = "テスト応答"

        resp = client.post(
            "/api/admin/course-builder/chat",
            json={
                "message": "このテーマでコースを作ってください",
                "selected_material_ids": ["mat-neutralino-001"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200

        call_args = mock_gen.call_args
        messages = call_args[1].get("messages") or call_args[0][0]
        all_content = " ".join(m["content"] for m in messages)
        assert "Agent解析済み教材コンテキスト" in all_content
        assert "Neutralino Dark Matter Candidate" in all_content

    @patch("routes.admin.save_cb_session")
    @patch("routes.admin.generate_text")
    @patch("routes.admin._build_material_context")
    def test_no_context_when_no_materials(
        self, mock_build_ctx, mock_gen, mock_save, client, auth_headers,
    ):
        """教材未選択時にはコンテキスト注入が行われないこと。"""
        mock_gen.return_value = "テスト応答"

        resp = client.post(
            "/api/admin/course-builder/chat",
            json={
                "message": "自由にコースを作ってください",
                "selected_material_ids": [],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        mock_build_ctx.assert_not_called()

    @patch("routes.admin.save_cb_session")
    @patch("routes.admin.generate_text")
    @patch("routes.admin._build_material_context")
    def test_system_prompt_is_first_message(
        self, mock_build_ctx, mock_gen, mock_save, client, auth_headers,
    ):
        """最初のメッセージがシステムプロンプトであること。"""
        mock_build_ctx.return_value = "## Agent解析済み教材コンテキスト\nテスト"
        mock_gen.return_value = "テスト応答"

        resp = client.post(
            "/api/admin/course-builder/chat",
            json={
                "message": "テスト",
                "selected_material_ids": ["mat-001"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200

        call_args = mock_gen.call_args
        messages = call_args[1].get("messages") or call_args[0][0]
        assert messages[0]["role"] == "system"
        assert "ソース文献" in messages[0]["content"] or "教材の記述" in messages[0]["content"]

    @patch("routes.admin.save_cb_session")
    @patch("routes.admin.generate_text")
    @patch("routes.admin._build_material_context")
    def test_context_placed_before_user_message(
        self, mock_build_ctx, mock_gen, mock_save, client, auth_headers,
    ):
        """教材コンテキストがユーザーの質問メッセージより前に配置されること。"""
        mock_build_ctx.return_value = "## Agent解析済み教材コンテキスト\nNeutralino論文"
        mock_gen.return_value = "テスト応答"

        resp = client.post(
            "/api/admin/course-builder/chat",
            json={
                "message": "コースを設計して",
                "selected_material_ids": ["mat-001"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200

        call_args = mock_gen.call_args
        messages = call_args[1].get("messages") or call_args[0][0]

        ctx_idx = None
        user_msg_idx = None
        for i, m in enumerate(messages):
            if "Agent解析済み教材コンテキスト" in m.get("content", ""):
                ctx_idx = i
            if m.get("content", "") == "コースを設計して":
                user_msg_idx = i

        assert ctx_idx is not None, "教材コンテキストメッセージが見つからない"
        assert user_msg_idx is not None, "ユーザーメッセージが見つからない"
        assert ctx_idx < user_msg_idx, "教材コンテキストはユーザーメッセージより前に配置されるべき"

    @patch("routes.admin.save_cb_session")
    @patch("routes.admin.generate_text")
    @patch("routes.admin._pg_session")
    def test_course_draft_json_parsing(
        self, mock_pg, mock_gen, mock_save, client, auth_headers,
    ):
        """LLMレスポンスのCOURSE_DRAFT_JSON区切り文字が正しくパースされること。"""
        draft = {
            "title": "ニュートラリーノ暗黒物質入門",
            "chapters": [{"title": "暗黒物質概論", "topics": []}],
            "sources": [],
        }
        mock_gen.return_value = (
            "以下のコース案を提案します。\n\n"
            "---COURSE_DRAFT_JSON---\n"
            + json.dumps(draft, ensure_ascii=False)
        )
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.side_effect = [
            # _build_material_context: 6 queries
            _make_doc_rows(), _make_component_rows(), _make_graph_rows(),
            _make_claim_rows(), _make_chunk_rows(), _make_analysis_rows(),
            # sources injection query
            [("mat-neutralino-001", "Neutralino Paper", "0212022v2.pdf")],
        ]
        mock_pg.return_value = mock_session

        resp = client.post(
            "/api/admin/course-builder/chat",
            json={
                "message": "コースを作ってください",
                "selected_material_ids": ["mat-neutralino-001"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["course_draft"] is not None
        assert data["course_draft"]["title"] == "ニュートラリーノ暗黒物質入門"


# ===========================================================================
# 5. フィクスチャデータの検証
# ===========================================================================

class TestNeutralinoSeedData:
    """ニュートラリーノテストデータの構造とAgent成果物の整合性を検証。"""

    def test_seed_has_documents(self):
        assert "documents" in NEUTRALINO_SEED
        assert len(NEUTRALINO_SEED["documents"]) >= 1

    def test_seed_has_theory_components(self):
        """theory_components が存在すること。"""
        assert "theory_components" in NEUTRALINO_SEED
        assert len(NEUTRALINO_SEED["theory_components"]) >= 3

    def test_seed_has_theory_component_graphs(self):
        """theory_component_graphs が存在すること。"""
        assert "theory_component_graphs" in NEUTRALINO_SEED
        assert len(NEUTRALINO_SEED["theory_component_graphs"]) >= 1

    def test_seed_has_theory_claims(self):
        """theory_claims が存在すること。"""
        assert "theory_claims" in NEUTRALINO_SEED
        assert len(NEUTRALINO_SEED["theory_claims"]) >= 3

    def test_seed_has_analysis_runs(self):
        """analysis_runs が存在すること。"""
        assert "analysis_runs" in NEUTRALINO_SEED
        assert len(NEUTRALINO_SEED["analysis_runs"]) >= 1

    def test_seed_has_chunks(self):
        assert "chunks" in NEUTRALINO_SEED
        assert len(NEUTRALINO_SEED["chunks"]) >= 3

    def test_document_has_doc_uuid(self):
        """documents に doc_uuid が存在すること。"""
        doc = NEUTRALINO_SEED["documents"][0]
        assert "doc_uuid" in doc
        assert len(doc["doc_uuid"]) > 10

    def test_theory_components_linked_to_doc_uuid(self):
        """theory_components の document_id が documents の doc_uuid と一致すること。"""
        doc_uuid = NEUTRALINO_SEED["documents"][0]["doc_uuid"]
        for comp in NEUTRALINO_SEED["theory_components"]:
            assert comp["document_id"] == doc_uuid

    def test_theory_claims_linked_to_doc_uuid(self):
        """theory_claims の document_id が documents の doc_uuid と一致すること。"""
        doc_uuid = NEUTRALINO_SEED["documents"][0]["doc_uuid"]
        for claim in NEUTRALINO_SEED["theory_claims"]:
            assert claim["document_id"] == doc_uuid

    def test_graph_edges_reference_component_ids(self):
        """グラフのエッジが theory_components の id を参照していること。"""
        comp_ids = {c["id"] for c in NEUTRALINO_SEED["theory_components"]}
        graph = NEUTRALINO_SEED["theory_component_graphs"][0]["graph_json"]
        for edge in graph["edges"]:
            assert edge["source"] in comp_ids, f"Edge source {edge['source']} not in components"
            assert edge["target"] in comp_ids, f"Edge target {edge['target']} not in components"

    def test_analysis_run_is_completed(self):
        """analysis_run の status が completed であること。"""
        run = NEUTRALINO_SEED["analysis_runs"][0]
        assert run["status"] == "completed"

    def test_document_has_knowledge_graph(self):
        """旧 knowledge_graph も fallback テスト用に保持されていること。"""
        doc = NEUTRALINO_SEED["documents"][0]
        kg = doc["knowledge_graph"]
        assert "summary" in kg
        assert "concepts" in kg
        assert len(kg["concepts"]) >= 5

    def test_chunks_are_ordered_by_index(self):
        indices = [c["chunk_index"] for c in NEUTRALINO_SEED["chunks"]]
        assert indices == sorted(indices)

    def test_theory_components_have_required_fields(self):
        """各理論コンポーネントが必要フィールドを持つこと。"""
        required = {"id", "document_id", "name", "summary", "review_status", "maturity_level"}
        for comp in NEUTRALINO_SEED["theory_components"]:
            for field in required:
                assert field in comp, f"Component '{comp.get('name')}' missing field '{field}'"


# ===========================================================================
# 6. fallback 動作の検証
# ===========================================================================

class TestFallbackBehavior:
    """旧 knowledge_graph fallback の動作を検証。"""

    def _import_func(self):
        from routes.admin import _build_material_context
        return _build_material_context

    @patch("routes.admin._pg_session")
    def test_fallback_includes_kg_summary(self, mock_pg):
        """fallback 時に knowledge_graph の summary が含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = [("mat-legacy", "Legacy Doc", "legacy.pdf", "uuid-legacy-001", "completed", {
            "domain": "Particle Physics / Dark Matter",
            "summary": "Legacy summary about neutralino annihilation cross section",
            "concepts": [{"name": "Neutralino", "type": "PARTICLE", "description": "DM candidate"}],
        })]
        mock_session.execute.return_value.fetchall.side_effect = [
            doc_rows, [], [], [], [], [],
        ]
        mock_pg.return_value = mock_session

        ctx = func(["mat-legacy"])
        assert ctx is not None
        assert "Legacy summary" in ctx or "neutralino annihilation" in ctx.lower()

    @patch("routes.admin._pg_session")
    def test_fallback_includes_kg_concepts(self, mock_pg):
        """fallback 時に knowledge_graph の概念一覧が含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = [("mat-legacy2", "Legacy Doc2", "legacy2.pdf", "uuid-legacy-002", "completed", {
            "domain": "Particle Physics",
            "summary": "some summary",
            "concepts": [
                {"name": "Neutralino", "type": "PARTICLE", "description": "DM candidate"},
                {"name": "MSSM", "type": "THEORY", "description": "Supersymmetric model"},
            ],
        })]
        mock_session.execute.return_value.fetchall.side_effect = [
            doc_rows, [], [], [], [], [],
        ]
        mock_pg.return_value = mock_session

        ctx = func(["mat-legacy2"])
        assert ctx is not None
        assert "Neutralino" in ctx
        assert "MSSM" in ctx

    @patch("routes.admin._pg_session")
    def test_fallback_includes_kg_domain(self, mock_pg):
        """fallback 時に knowledge_graph の domain が含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = [("mat-legacy3", "Legacy Doc3", "legacy3.pdf", "uuid-legacy-003", "completed", {
            "domain": "Particle Physics / Dark Matter",
            "summary": "some summary",
            "concepts": [],
        })]
        mock_session.execute.return_value.fetchall.side_effect = [
            doc_rows, [], [], [], [], [],
        ]
        mock_pg.return_value = mock_session

        ctx = func(["mat-legacy3"])
        assert ctx is not None
        assert "Particle Physics" in ctx
