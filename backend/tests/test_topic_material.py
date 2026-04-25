"""Issue #151: 学習パス選択時の教材コンテンツ取得テスト。

テスト対象:
  - `backend/api/routes/learning.py` — GET /api/learning/courses/{course_id}/topics/{topic_id}/material
  - `backend/api/services.py`        — fetch_topic_material_chunks
  - `backend/api/schemas.py`         — TopicMaterialChunk, TopicMaterialResponse
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
LEARNING_PY = BACKEND_DIR / "api" / "routes" / "learning.py"
SERVICES_PY = BACKEND_DIR / "api" / "services.py"
SCHEMAS_PY = BACKEND_DIR / "api" / "schemas.py"


# ---------------------------------------------------------------------------
# 1. スキーマの定義確認
# ---------------------------------------------------------------------------


class TestTopicMaterialSchemas:
    """TopicMaterialChunk / TopicMaterialResponse が schemas.py に定義されていること。"""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = SCHEMAS_PY.read_text(encoding="utf-8")

    def test_topic_material_chunk_defined(self):
        assert re.search(r"class\s+TopicMaterialChunk\s*\(", self.src), \
            "TopicMaterialChunk が schemas.py に定義されていない"

    def test_topic_material_response_defined(self):
        assert re.search(r"class\s+TopicMaterialResponse\s*\(", self.src), \
            "TopicMaterialResponse が schemas.py に定義されていない"

    def test_chunk_has_text_field(self):
        m = re.search(r"class\s+TopicMaterialChunk\b.*?(?=\n(?:class |#)\b)", self.src, re.DOTALL)
        assert m, "TopicMaterialChunk 本体が抽出できない"
        body = m.group(0)
        assert "text" in body

    def test_chunk_has_source_title_field(self):
        m = re.search(r"class\s+TopicMaterialChunk\b.*?(?=\n(?:class |#)\b)", self.src, re.DOTALL)
        assert m
        body = m.group(0)
        assert "source_title" in body

    def test_response_has_chunks_field(self):
        m = re.search(r"class\s+TopicMaterialResponse\b.*?(?=\n(?:class |#)\b)", self.src, re.DOTALL)
        assert m, "TopicMaterialResponse 本体が抽出できない"
        body = m.group(0)
        assert "chunks" in body

    def test_response_has_topic_id_and_title(self):
        m = re.search(r"class\s+TopicMaterialResponse\b.*?(?=\n(?:class |#)\b)", self.src, re.DOTALL)
        assert m
        body = m.group(0)
        assert "topic_id" in body
        assert "topic_title" in body


# ---------------------------------------------------------------------------
# 2. サービス関数の定義確認
# ---------------------------------------------------------------------------


class TestFetchTopicMaterialChunksDefinition:
    """fetch_topic_material_chunks が services.py に定義されていること。"""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = SERVICES_PY.read_text(encoding="utf-8")

    def test_function_defined(self):
        assert re.search(
            r"def\s+fetch_topic_material_chunks\s*\(",
            self.src,
        ), "fetch_topic_material_chunks が services.py に定義されていない"

    def test_accepts_user_id_course_id_topic_id(self):
        m = re.search(
            r"def\s+fetch_topic_material_chunks\s*\(.*?\)\s*(?:->.*?)?:",
            self.src,
            re.DOTALL,
        )
        assert m
        sig = m.group(0)
        assert "user_id" in sig
        assert "course_id" in sig
        assert "topic_id" in sig

    def test_uses_get_course_data(self):
        m = re.search(
            r"def\s+fetch_topic_material_chunks\b.*?(?=\ndef |\nclass |\Z)",
            self.src,
            re.DOTALL,
        )
        assert m
        body = m.group(0)
        assert "get_course_data" in body

    def test_searches_material_ids_from_sources(self):
        m = re.search(
            r"def\s+fetch_topic_material_chunks\b.*?(?=\ndef |\nclass |\Z)",
            self.src,
            re.DOTALL,
        )
        assert m
        body = m.group(0)
        assert "material_id" in body

    def test_returns_list_of_dicts(self):
        m = re.search(
            r"def\s+fetch_topic_material_chunks\b.*?(?=\ndef |\nclass |\Z)",
            self.src,
            re.DOTALL,
        )
        assert m
        body = m.group(0)
        assert "return [" in body or "return []" in body


# ---------------------------------------------------------------------------
# 3. エンドポイントの定義確認
# ---------------------------------------------------------------------------


class TestTopicMaterialEndpoint:
    """GET /api/learning/courses/{course_id}/topics/{topic_id}/material が定義されていること。"""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = LEARNING_PY.read_text(encoding="utf-8")

    def test_route_path_defined(self):
        assert '"/courses/{course_id}/topics/{topic_id}/material"' in self.src, \
            "material エンドポイントのルートパスが learning.py に存在しない"

    def test_response_model_is_topic_material_response(self):
        assert "TopicMaterialResponse" in self.src, \
            "TopicMaterialResponse が learning.py で使われていない"

    def test_imports_fetch_topic_material_chunks(self):
        assert "fetch_topic_material_chunks" in self.src, \
            "fetch_topic_material_chunks が learning.py にインポートされていない"

    def test_imports_topic_material_chunk(self):
        assert "TopicMaterialChunk" in self.src, \
            "TopicMaterialChunk が learning.py にインポートされていない"

    def test_returns_404_when_course_not_found(self):
        m = re.search(
            r"def\s+get_topic_material\b.*?(?=\n@router|\nclass |\Z)",
            self.src,
            re.DOTALL,
        )
        assert m, "get_topic_material 関数本体が抽出できない"
        body = m.group(0)
        assert "404" in body

    def test_returns_404_when_topic_not_found(self):
        m = re.search(
            r"def\s+get_topic_material\b.*?(?=\n@router|\nclass |\Z)",
            self.src,
            re.DOTALL,
        )
        assert m
        body = m.group(0)
        # 2か所 404 があることを確認（course not found / topic not found）
        assert body.count("404") >= 2


# ---------------------------------------------------------------------------
# 4. fetch_topic_material_chunks のロジックテスト（モック）
# ---------------------------------------------------------------------------


class TestFetchTopicMaterialChunksLogic:
    """fetch_topic_material_chunks の振る舞いをモックでテストする。"""

    @pytest.fixture(autouse=True)
    def _patch_session(self):
        """DB セッションをモックアウトし外部依存を排除する。"""
        with patch("api.services._pg_session") as mock_pg:
            self.mock_session = MagicMock()
            mock_pg.return_value = self.mock_session
            yield

    def _make_course_data(self, material_id: str | None = "mat1") -> dict:
        sources = [{"title": "テスト教材", "material_id": material_id}] if material_id else []
        return {
            "id": "course1",
            "title": "テストコース",
            "topics": [
                {"id": "topic1", "title": "波動関数", "chapter_index": 0, "status": "in_progress"},
            ],
            "concepts": [{"name": "シュレーディンガー方程式"}],
            "sources": sources,
        }

    def test_returns_empty_when_course_not_found(self):
        with patch("api.services.get_course_data", return_value=None):
            from api.services import fetch_topic_material_chunks
            result = fetch_topic_material_chunks("user1", "course1", "topic1")
        assert result == []

    def test_returns_empty_when_topic_not_found(self):
        course_data = self._make_course_data()
        with patch("api.services.get_course_data", return_value=course_data):
            from api.services import fetch_topic_material_chunks
            result = fetch_topic_material_chunks("user1", "course1", "nonexistent_topic")
        assert result == []

    def test_uses_material_ids_from_sources(self):
        """course.sources に material_id があれば DB 検索を試みること。"""
        course_data = self._make_course_data(material_id="mat1")

        fake_row = (
            "chunk-uuid-1",
            "波動関数とは確率振幅の関数である。",
            "量子力学教科書",
            "quantum.pdf",
            0.85,
        )
        self.mock_session.execute.return_value.fetchall.return_value = [fake_row]

        with patch("api.services.get_course_data", return_value=course_data):
            with patch("api.services.embed_text", return_value=[0.1] * 10):
                with patch("api.services.get_embedding_dim", return_value=10):
                    from api.services import fetch_topic_material_chunks
                    result = fetch_topic_material_chunks("user1", "course1", "topic1")

        assert len(result) == 1
        assert result[0]["text"] == "波動関数とは確率振幅の関数である。"
        assert result[0]["source_title"] == "量子力学教科書"
        assert result[0]["score"] == pytest.approx(0.85)

    def test_returns_list_with_required_keys(self):
        """結果 dict に id / text / source_title / score が含まれること。"""
        course_data = self._make_course_data(material_id="mat1")

        fake_row = ("cid", "教材テキスト", "書籍A", "a.pdf", 0.7)
        self.mock_session.execute.return_value.fetchall.return_value = [fake_row]

        with patch("api.services.get_course_data", return_value=course_data):
            with patch("api.services.embed_text", return_value=[0.1] * 10):
                with patch("api.services.get_embedding_dim", return_value=10):
                    from api.services import fetch_topic_material_chunks
                    result = fetch_topic_material_chunks("user1", "course1", "topic1")

        assert result
        chunk = result[0]
        for key in ("id", "text", "source_title", "score"):
            assert key in chunk, f"キー '{key}' が結果に含まれていない"

    def test_fallback_to_system_wide_search_when_no_material_ids(self):
        """course.sources に material_id がない場合、システム全域検索にフォールバックすること。"""
        course_data = self._make_course_data(material_id=None)

        mock_chunks = [
            {"id": "c1", "text": "フォールバックチャンク", "source_title": "DB", "source_file": "", "score": 0.4},
        ]
        with patch("api.services.get_course_data", return_value=course_data):
            with patch("api.services.search_chunks_with_metadata", return_value=mock_chunks) as mock_search:
                from api.services import fetch_topic_material_chunks
                result = fetch_topic_material_chunks("user1", "course1", "topic1")

        mock_search.assert_called_once()
        assert len(result) == 1

    def test_embedding_failure_returns_empty(self):
        """embed_text が失敗した場合は空リストを返すこと（クラッシュしないこと）。"""
        course_data = self._make_course_data(material_id="mat1")

        with patch("api.services.get_course_data", return_value=course_data):
            with patch("api.services.embed_text", side_effect=Exception("embedding error")):
                from api.services import fetch_topic_material_chunks
                result = fetch_topic_material_chunks("user1", "course1", "topic1")

        assert result == []
