"""Issue #113: ソース文献準拠のコース生成 — コンテキスト注入・プロンプトのテスト。

コースビルダーにおいて、選択された教材のナレッジグラフとチャンクテキストが
LLMプロンプトに正しく注入されること、およびシステムプロンプトがソース準拠と
段階的構造化を要求していることを検証する。
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

# Load test fixture data
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
NEUTRALINO_SEED = json.loads(
    (FIXTURE_DIR / "neutralino_seed.json").read_text(encoding="utf-8")
)


# ---------------------------------------------------------------------------
# Helper: build DB mock rows from fixture
# ---------------------------------------------------------------------------

def _make_doc_rows(seed=NEUTRALINO_SEED):
    """fixtures の documents を DB 行形式 (source_path, title, filename, knowledge_graph) に変換。"""
    rows = []
    for doc in seed["documents"]:
        rows.append((
            doc["material_id"],
            doc["title"],
            doc["filename"],
            doc["knowledge_graph"],
        ))
    return rows


def _make_chunk_rows(seed=NEUTRALINO_SEED):
    """fixtures の chunks を DB 行形式 (material_id, chunk_index, chapter, section, text) に変換。"""
    rows = []
    for ch in seed["chunks"]:
        rows.append((
            ch["material_id"],
            ch["chunk_index"],
            ch.get("chapter", ""),
            ch.get("section", ""),
            ch["text"],
        ))
    return rows


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
        # First call: doc query returns empty
        mock_session.execute.return_value.fetchall.return_value = []
        mock_pg.return_value = mock_session

        result = func(["nonexistent-id"])
        assert result is None

    @patch("routes.admin._pg_session")
    def test_includes_knowledge_graph_summary(self, mock_pg):
        """ナレッジグラフの概要がコンテキストに含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = _make_doc_rows()
        chunk_rows = _make_chunk_rows()
        mock_session.execute.return_value.fetchall.side_effect = [doc_rows, chunk_rows]
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert ctx is not None
        assert "unitarity" in ctx.lower() or "Unitarity" in ctx
        assert "neutralino" in ctx.lower() or "Neutralino" in ctx

    @patch("routes.admin._pg_session")
    def test_includes_knowledge_graph_concepts(self, mock_pg):
        """ナレッジグラフの主要概念がコンテキストに含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = _make_doc_rows()
        chunk_rows = _make_chunk_rows()
        mock_session.execute.return_value.fetchall.side_effect = [doc_rows, chunk_rows]
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert ctx is not None
        # Check that key domain concepts appear
        assert "Neutralino" in ctx
        assert "Dark Matter" in ctx
        assert "Annihilation Cross Section" in ctx
        assert "MSSM" in ctx

    @patch("routes.admin._pg_session")
    def test_includes_chunk_text(self, mock_pg):
        """チャンクテキストがコンテキストに含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = _make_doc_rows()
        chunk_rows = _make_chunk_rows()
        mock_session.execute.return_value.fetchall.side_effect = [doc_rows, chunk_rows]
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert ctx is not None
        # Introduction text should be present
        assert "dark matter" in ctx.lower()
        assert "monochromatic gamma-ray" in ctx.lower() or "gamma-ray" in ctx.lower()

    @patch("routes.admin._pg_session")
    def test_includes_chunk_headers(self, mock_pg):
        """チャンクのチャプター・セクション情報がヘッダとして含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = _make_doc_rows()
        chunk_rows = _make_chunk_rows()
        mock_session.execute.return_value.fetchall.side_effect = [doc_rows, chunk_rows]
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert ctx is not None
        assert "Introduction" in ctx
        assert "Framework" in ctx or "One-Loop Amplitudes" in ctx

    @patch("routes.admin._pg_session")
    def test_includes_domain_field(self, mock_pg):
        """ナレッジグラフの分野 (domain) がコンテキストに含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = _make_doc_rows()
        chunk_rows = _make_chunk_rows()
        mock_session.execute.return_value.fetchall.side_effect = [doc_rows, chunk_rows]
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert ctx is not None
        assert "Particle Physics" in ctx

    @patch("routes.admin._pg_session")
    def test_chunk_text_is_truncated_within_budget(self, mock_pg):
        """チャンクテキストが _MAX_CHUNK_CHARS_PER_MATERIAL 以内に収まること。"""
        func = self._import_func()
        from routes.admin import _MAX_CHUNK_CHARS_PER_MATERIAL

        mock_session = MagicMock()
        # Create a document with very long chunks
        doc_rows = [("mat-long", "Long Doc", "long.pdf", {"summary": "test"})]
        long_text = "A" * 10000
        chunk_rows = [
            ("mat-long", 0, "Ch1", "S1", long_text),
            ("mat-long", 1, "Ch2", "S2", long_text),
        ]
        mock_session.execute.return_value.fetchall.side_effect = [doc_rows, chunk_rows]
        mock_pg.return_value = mock_session

        ctx = func(["mat-long"])
        assert ctx is not None
        # The total chunk text should not exceed budget significantly
        # (headers add some overhead but the bulk text is within budget)
        chunk_text_portions = [line for line in ctx.split("\n") if line.startswith("AAA")]
        total_a_chars = sum(line.count("A") for line in chunk_text_portions)
        assert total_a_chars <= _MAX_CHUNK_CHARS_PER_MATERIAL + 100  # small header overhead

    @patch("routes.admin._pg_session")
    def test_context_section_header(self, mock_pg):
        """コンテキストに「教材の内容詳細」セクションヘッダが含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = _make_doc_rows()
        chunk_rows = _make_chunk_rows()
        mock_session.execute.return_value.fetchall.side_effect = [doc_rows, chunk_rows]
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert ctx is not None
        assert "教材の内容詳細" in ctx


# ===========================================================================
# 2. システムプロンプトのテスト
# ===========================================================================

class TestCourseBuilderSystemPrompt:
    """_COURSE_BUILDER_SYSTEM_PROMPT がソース準拠・段階的構造化を要求していることを検証。"""

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
        # Check for anti-hallucination instruction
        assert "一般的だから" in prompt

    def test_prompt_requires_stepwise_structure(self):
        """段階的構造化（依存関係に基づくシラバス）を要求していること。"""
        prompt = self._get_prompt()
        assert "段階的" in prompt or "依存関係" in prompt
        assert "chunk_index" in prompt or "チャンクの並び" in prompt

    def test_prompt_mentions_knowledge_graph(self):
        """プロンプトがナレッジグラフの活用に言及していること。"""
        prompt = self._get_prompt()
        assert "ナレッジグラフ" in prompt

    def test_prompt_mentions_chunks(self):
        """プロンプトがチャンクの活用に言及していること。"""
        prompt = self._get_prompt()
        assert "チャンク" in prompt

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


# ===========================================================================
# 3. course_builder_chat エンドポイントの結合テスト
# ===========================================================================

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient
except ImportError:
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
            "## 教材の内容詳細\n### 教材: Neutralino Paper\n"
            "**概要:** Unitarity constraints on neutralino annihilation\n"
            "**主要概念:**\n- Neutralino\n- Dark Matter\n"
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

        # generate_text に渡されたメッセージを検証
        call_args = mock_gen.call_args
        messages = call_args[1].get("messages") or call_args[0][0]
        all_content = " ".join(m["content"] for m in messages)
        assert "教材の内容詳細" in all_content
        assert "Neutralino" in all_content

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
        # _build_material_context は呼ばれない
        mock_build_ctx.assert_not_called()

    @patch("routes.admin.save_cb_session")
    @patch("routes.admin.generate_text")
    @patch("routes.admin._build_material_context")
    def test_system_prompt_is_first_message(
        self, mock_build_ctx, mock_gen, mock_save, client, auth_headers,
    ):
        """最初のメッセージがシステムプロンプトであること。"""
        mock_build_ctx.return_value = "## 教材の内容詳細\nテスト"
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
        mock_build_ctx.return_value = "## 教材の内容詳細\nNeutralino論文"
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

        # Find context message and user message positions
        ctx_idx = None
        user_msg_idx = None
        for i, m in enumerate(messages):
            if "教材の内容詳細" in m.get("content", ""):
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
        # Mock for sources injection query
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("mat-neutralino-001", "Neutralino Paper", "0212022v2.pdf"),
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
# 4. テストデータ（フィクスチャ）の検証
# ===========================================================================

class TestNeutralinoSeedData:
    """ニュートラリーノテストデータの構造とドメイン特化性を検証。"""

    def test_seed_has_documents(self):
        """documents セクションが存在すること。"""
        assert "documents" in NEUTRALINO_SEED
        assert len(NEUTRALINO_SEED["documents"]) >= 1

    def test_seed_has_chunks(self):
        """chunks セクションが存在すること。"""
        assert "chunks" in NEUTRALINO_SEED
        assert len(NEUTRALINO_SEED["chunks"]) >= 3

    def test_document_has_knowledge_graph(self):
        """ドキュメントにナレッジグラフが含まれること。"""
        doc = NEUTRALINO_SEED["documents"][0]
        kg = doc["knowledge_graph"]
        assert "summary" in kg
        assert "concepts" in kg
        assert len(kg["concepts"]) >= 5

    def test_document_domain_is_particle_physics(self):
        """ドメインが素粒子物理学であること。"""
        doc = NEUTRALINO_SEED["documents"][0]
        assert "Particle Physics" in doc["knowledge_graph"]["domain"]

    def test_chunks_are_ordered_by_index(self):
        """チャンクが chunk_index 順に並んでいること。"""
        indices = [c["chunk_index"] for c in NEUTRALINO_SEED["chunks"]]
        assert indices == sorted(indices)

    def test_chunks_cover_multiple_sections(self):
        """チャンクが複数のセクションをカバーしていること。"""
        chapters = {c.get("chapter", "") for c in NEUTRALINO_SEED["chunks"]}
        assert len(chapters) >= 3

    def test_concepts_include_neutralino(self):
        """主要概念にニュートラリーノが含まれること。"""
        concepts = NEUTRALINO_SEED["documents"][0]["knowledge_graph"]["concepts"]
        names = [c["name"] for c in concepts]
        assert "Neutralino" in names

    def test_concepts_include_dark_matter(self):
        """主要概念にダークマターが含まれること。"""
        concepts = NEUTRALINO_SEED["documents"][0]["knowledge_graph"]["concepts"]
        names = [c["name"] for c in concepts]
        assert "Dark Matter" in names

    def test_concepts_include_unitarity(self):
        """主要概念にユニタリティが含まれること。"""
        concepts = NEUTRALINO_SEED["documents"][0]["knowledge_graph"]["concepts"]
        names = [c["name"] for c in concepts]
        assert "Unitarity" in names

    def test_concepts_have_types(self):
        """各概念に type フィールドがあること。"""
        concepts = NEUTRALINO_SEED["documents"][0]["knowledge_graph"]["concepts"]
        for c in concepts:
            assert "type" in c, f"Concept '{c['name']}' is missing 'type'"

    def test_chunks_contain_domain_specific_terms(self):
        """チャンクテキストがドメイン固有の用語を含むこと（一般的な物理学ではなく）。"""
        all_text = " ".join(c["text"] for c in NEUTRALINO_SEED["chunks"])
        # These terms should appear in the fixture (domain-specific, not general physics)
        assert "neutralino" in all_text.lower()
        assert "MSSM" in all_text or "mssm" in all_text.lower()
        assert "chargino" in all_text.lower()
        assert "sfermion" in all_text.lower()
        assert "partial-wave" in all_text.lower() or "partial wave" in all_text.lower()

    def test_seed_material_id_consistency(self):
        """documents と chunks の material_id が一致すること。"""
        doc_ids = {d["material_id"] for d in NEUTRALINO_SEED["documents"]}
        chunk_mids = {c["material_id"] for c in NEUTRALINO_SEED["chunks"]}
        assert chunk_mids.issubset(doc_ids)


# ===========================================================================
# 5. _build_material_context の出力形式検証
# ===========================================================================

class TestMaterialContextFormat:
    """_build_material_context が生成するコンテキスト文字列のフォーマット検証。"""

    def _import_func(self):
        from routes.admin import _build_material_context
        return _build_material_context

    @patch("routes.admin._pg_session")
    def test_context_has_material_title_header(self, mock_pg):
        """各教材のタイトルがMarkdownヘッダとして出力されること。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = _make_doc_rows()
        chunk_rows = _make_chunk_rows()
        mock_session.execute.return_value.fetchall.side_effect = [doc_rows, chunk_rows]
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert "### 教材:" in ctx

    @patch("routes.admin._pg_session")
    def test_context_has_summary_section(self, mock_pg):
        """概要セクションが含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = _make_doc_rows()
        chunk_rows = _make_chunk_rows()
        mock_session.execute.return_value.fetchall.side_effect = [doc_rows, chunk_rows]
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert "**概要:**" in ctx

    @patch("routes.admin._pg_session")
    def test_context_has_concepts_section(self, mock_pg):
        """主要概念セクションが含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = _make_doc_rows()
        chunk_rows = _make_chunk_rows()
        mock_session.execute.return_value.fetchall.side_effect = [doc_rows, chunk_rows]
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert "**主要概念:**" in ctx

    @patch("routes.admin._pg_session")
    def test_context_has_text_excerpt_section(self, mock_pg):
        """教材テキスト抜粋セクションが含まれること。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = _make_doc_rows()
        chunk_rows = _make_chunk_rows()
        mock_session.execute.return_value.fetchall.side_effect = [doc_rows, chunk_rows]
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        assert "**教材テキスト（抜粋）:**" in ctx

    @patch("routes.admin._pg_session")
    def test_context_does_not_contain_general_physics_only(self, mock_pg):
        """コンテキストが一般的な物理学ではなくドメイン固有の内容を含むこと。"""
        func = self._import_func()
        mock_session = MagicMock()
        doc_rows = _make_doc_rows()
        chunk_rows = _make_chunk_rows()
        mock_session.execute.return_value.fetchall.side_effect = [doc_rows, chunk_rows]
        mock_pg.return_value = mock_session

        ctx = func(["mat-neutralino-001"])
        # The context should contain specific particle physics terms from the fixture
        ctx_lower = ctx.lower()
        assert "neutralino" in ctx_lower
        assert "annihilation" in ctx_lower or "cross section" in ctx_lower
