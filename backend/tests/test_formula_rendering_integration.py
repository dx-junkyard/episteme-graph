"""数式レンダリングパイプラインの結合テスト (Issue #91)。

教員によるバッチスクリプト生成 → DB 永続化 → 学生によるレクチャーシーケンス取得
の一連の流れを通して、数式データ（display_text / formulas）が欠落・破損なく
フロントエンドに届くことを検証する。

外部 API (OpenAI, PostgreSQL, Neo4j, MinIO) は一切呼び出さない。
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

# api/ ディレクトリを sys.path に追加
_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


# ---------------------------------------------------------------------------
# テスト用定数 — LLM モックが返す数式入りレスポンス
# ---------------------------------------------------------------------------

_CHUNK_TEXT_RAW = (
    "量子力学の基本方程式として、シュレーディンガー方程式 "
    "i\\hbar \\frac{\\partial}{\\partial t}\\Psi = \\hat{H}\\Psi がある。"
    "また、エネルギーと質量の関係は E = mc^2 で表される。"
)

_LLM_RESPONSE = {
    "display_text": (
        "量子力学の基本方程式として、シュレーディンガー方程式 "
        "$$i\\hbar \\frac{\\partial}{\\partial t}\\Psi = \\hat{H}\\Psi$$ がある。\n\n"
        "また、エネルギーと質量の関係は $E = mc^2$ で表される。"
    ),
    "spoken_text": (
        "量子力学の基本方程式として、シュレーディンガー方程式 "
        "アイ エイチバー パーシャルティー分のパーシャルプサイ イコール ハミルトニアンプサイ がある。"
        "...また、エネルギーと質量の関係は Eイコールmcの二乗 で表される。"
    ),
    "formulas": [
        {
            "id": "formula_0",
            "latex": "i\\hbar \\frac{\\partial}{\\partial t}\\Psi = \\hat{H}\\Psi",
            "spoken": "アイ エイチバー パーシャルティー分のパーシャルプサイ イコール ハミルトニアンプサイ",
        },
        {
            "id": "formula_1",
            "latex": "E = mc^2",
            "spoken": "Eイコールmcの二乗",
        },
    ],
}

_COURSE_ID = "course-test-001"
_TOPIC_ID = "topic-test-001"
_MATERIAL_ID = "material-test-001"
_CHUNK_ID = str(uuid.uuid4())
_USER_ID = str(uuid.uuid4())
_TEACHER_ID = str(uuid.uuid4())

_COURSE_DATA = {
    "title": "量子力学入門",
    "goal": "量子力学の基礎を理解する",
    "prerequisites": ["線形代数", "微分積分"],
    "chapters": ["量子力学の基礎"],
    "topics": [
        {
            "id": _TOPIC_ID,
            "title": "シュレーディンガー方程式",
            "chapter_index": 0,
            "prerequisites": [],
        },
    ],
    "sources": [{"title": "教材A", "material_id": _MATERIAL_ID}],
}


# ---------------------------------------------------------------------------
# 1. LLM モック設定 — 数式プレースホルダー + 数式辞書を返す
# ---------------------------------------------------------------------------


class TestLLMMockConfiguration:
    """LLM モックが数式メタデータ付きレスポンスを正しく生成すること。"""

    @patch("core.lecture.generate_text")
    def test_mock_returns_display_text_with_latex(self, mock_gen):
        """LLM モックが display_text に LaTeX 数式を含むレスポンスを返す。"""
        from core.lecture import generate_spoken_text_and_formulas

        mock_gen.return_value = json.dumps(_LLM_RESPONSE)

        result = generate_spoken_text_and_formulas(_CHUNK_TEXT_RAW)

        # display_text に LaTeX デリミタ ($...$ や $$...$$) が含まれること
        assert "$$" in result["display_text"] or "$" in result["display_text"]
        assert "\\hbar" in result["display_text"]
        assert "$E = mc^2$" in result["display_text"]

    @patch("core.lecture.generate_text")
    def test_mock_returns_formulas_with_required_keys(self, mock_gen):
        """formulas 配列の各要素に id / latex / spoken が含まれること。"""
        from core.lecture import generate_spoken_text_and_formulas

        mock_gen.return_value = json.dumps(_LLM_RESPONSE)

        result = generate_spoken_text_and_formulas(_CHUNK_TEXT_RAW)

        assert len(result["formulas"]) == 2
        for f in result["formulas"]:
            assert "id" in f
            assert "latex" in f
            assert "spoken" in f
            assert f["latex"]  # 空文字でないこと
            assert f["spoken"]  # 空文字でないこと

    @patch("core.lecture.generate_text")
    def test_formula_ids_are_sequential(self, mock_gen):
        """formula ID が formula_0, formula_1, ... の連番であること。"""
        from core.lecture import generate_spoken_text_and_formulas

        mock_gen.return_value = json.dumps(_LLM_RESPONSE)

        result = generate_spoken_text_and_formulas(_CHUNK_TEXT_RAW)

        ids = [f["id"] for f in result["formulas"]]
        assert ids == ["formula_0", "formula_1"]

    @patch("core.lecture.generate_text")
    def test_spoken_text_contains_no_raw_latex(self, mock_gen):
        """spoken_text に生の LaTeX デリミタが含まれないこと。"""
        from core.lecture import generate_spoken_text_and_formulas

        mock_gen.return_value = json.dumps(_LLM_RESPONSE)

        result = generate_spoken_text_and_formulas(_CHUNK_TEXT_RAW)

        assert "$" not in result["spoken_text"]
        assert "\\frac" not in result["spoken_text"]


# ---------------------------------------------------------------------------
# 2. 教員アクション — バッチ生成で DB に正しく永続化される
# ---------------------------------------------------------------------------


class TestTeacherBatchGeneration:
    """教員のバッチスクリプト生成で数式データが DB に正しく永続化されること。"""

    @patch("core.lecture.generate_text")
    def test_batch_worker_persists_display_text_and_formulas(self, mock_gen):
        """_batch_generate_worker が display_text / spoken_text / formulas を
        UPDATE 文で DB に書き込むことを検証。"""
        from api.routes.lecture_studio import _batch_generate_worker

        mock_gen.return_value = json.dumps(_LLM_RESPONSE)

        mock_session = MagicMock()
        chunks = [
            {
                "id": _CHUNK_ID,
                "chunk_index": 0,
                "text": _CHUNK_TEXT_RAW,
                "spoken_text": "",  # 未生成
                "formulas": [],
            },
        ]

        with (
            patch("api.routes.lecture_studio._pg_session", return_value=mock_session),
            patch("api.routes.lecture_studio.update_background_task"),
            patch("api.routes.lecture_studio.create_background_task"),
        ):
            _batch_generate_worker("task-1", _COURSE_ID, chunks, False, _COURSE_DATA)

        # session.execute が UPDATE で呼ばれたことを確認
        execute_calls = mock_session.execute.call_args_list
        assert len(execute_calls) >= 1

        # UPDATE 文のパラメータを検証 — session.execute(sa_text(...), {params})
        update_call = execute_calls[0]
        # call_args[0] は positional args のタプル: (sa_text_obj, params_dict)
        params = update_call[0][1]
        assert params["id"] == _CHUNK_ID
        assert "display_text" in params
        assert "spoken_text" in params
        assert "formulas" in params

        # formulas が JSON 文字列として正しくシリアライズされていること
        formulas_json = json.loads(params["formulas"])
        assert len(formulas_json) == 2
        assert formulas_json[0]["latex"] == _LLM_RESPONSE["formulas"][0]["latex"]
        assert formulas_json[1]["latex"] == _LLM_RESPONSE["formulas"][1]["latex"]

        # display_text に LaTeX が含まれていること
        assert "$$" in params["display_text"] or "$" in params["display_text"]

        # commit が呼ばれたこと
        mock_session.commit.assert_called()

    @patch("core.lecture.generate_text")
    def test_batch_worker_skips_already_generated_chunks(self, mock_gen):
        """spoken_text が既に存在するチャンクはスキップされること。"""
        from api.routes.lecture_studio import _batch_generate_worker

        chunks = [
            {
                "id": _CHUNK_ID,
                "chunk_index": 0,
                "text": _CHUNK_TEXT_RAW,
                "spoken_text": "既に生成済みのテキスト",
                "formulas": [{"id": "formula_0", "latex": "x", "spoken": "エックス"}],
            },
        ]

        mock_session = MagicMock()
        with (
            patch("api.routes.lecture_studio._pg_session", return_value=mock_session),
            patch("api.routes.lecture_studio.update_background_task") as mock_update,
            patch("api.routes.lecture_studio.create_background_task"),
        ):
            _batch_generate_worker("task-1", _COURSE_ID, chunks, False, _COURSE_DATA)

        # LLM は呼ばれないこと
        mock_gen.assert_not_called()
        # DB UPDATE も呼ばれないこと
        mock_session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# 3. 学生アクション — レクチャーシーケンスAPIが数式データを正しく返す
# ---------------------------------------------------------------------------


class TestStudentLectureSequenceRetrieval:
    """学生がレクチャーシーケンスAPIで取得するデータに
    数式が欠落なく含まれることを検証。"""

    def _build_db_row(self):
        """DB から返るチャンク行をシミュレートする。"""
        return (
            uuid.UUID(_CHUNK_ID),           # c.id
            0,                               # c.chunk_index
            _CHUNK_TEXT_RAW,                 # c.text
            _LLM_RESPONSE["display_text"],  # c.display_text
            _LLM_RESPONSE["spoken_text"],   # c.spoken_text
            _LLM_RESPONSE["formulas"],      # c.formulas (JSONB → list)
            "第1章",                          # c.chapter
            "1.1",                           # c.section
        )

    @patch("api.routes.lecture.get_user_mastered_concepts", return_value=set())
    @patch("api.routes.lecture._check_audio_cache", return_value=False)
    @patch("api.routes.lecture._pg_session")
    @patch("api.routes.lecture.get_course_data", return_value=_COURSE_DATA)
    def test_sequence_api_returns_display_text_with_latex(
        self, mock_course, mock_pg, mock_audio, mock_mastery,
    ):
        """GET /sequence のレスポンス segments[].text に LaTeX 数式が含まれること。"""
        from api.routes.lecture import get_lecture_sequence

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [self._build_db_row()]
        mock_pg.return_value = mock_session

        current_user = {"id": _USER_ID, "username": "student", "email": "s@test.com", "role": "STUDENT"}
        resp = get_lecture_sequence(_COURSE_ID, _TOPIC_ID, current_user)

        assert resp.total_segments >= 1
        seg = resp.segments[0]

        # display_text に LaTeX が含まれていること
        assert "$$" in seg.text or "$" in seg.text
        assert "\\hbar" in seg.text

    @patch("api.routes.lecture.get_user_mastered_concepts", return_value=set())
    @patch("api.routes.lecture._check_audio_cache", return_value=False)
    @patch("api.routes.lecture._pg_session")
    @patch("api.routes.lecture.get_course_data", return_value=_COURSE_DATA)
    def test_sequence_api_returns_formulas_array(
        self, mock_course, mock_pg, mock_audio, mock_mastery,
    ):
        """GET /sequence のレスポンス segments[].formulas が正しい構造であること。"""
        from api.routes.lecture import get_lecture_sequence

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [self._build_db_row()]
        mock_pg.return_value = mock_session

        current_user = {"id": _USER_ID, "username": "student", "email": "s@test.com", "role": "STUDENT"}
        resp = get_lecture_sequence(_COURSE_ID, _TOPIC_ID, current_user)

        seg = resp.segments[0]
        assert len(seg.formulas) == 2

        # 各 formula に id / latex / spoken が含まれること
        for f in seg.formulas:
            assert f.id.startswith("formula_")
            assert f.latex  # 空でない
            assert f.spoken  # 空でない

        # 具体的な値の検証
        assert seg.formulas[0].latex == _LLM_RESPONSE["formulas"][0]["latex"]
        assert seg.formulas[1].latex == "E = mc^2"

    @patch("api.routes.lecture.get_user_mastered_concepts", return_value=set())
    @patch("api.routes.lecture._check_audio_cache", return_value=False)
    @patch("api.routes.lecture._pg_session")
    @patch("api.routes.lecture.get_course_data", return_value=_COURSE_DATA)
    def test_sequence_api_spoken_text_is_natural_language(
        self, mock_course, mock_pg, mock_audio, mock_mastery,
    ):
        """GET /sequence の spoken_text が自然言語であり、LaTeX を含まないこと。"""
        from api.routes.lecture import get_lecture_sequence

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [self._build_db_row()]
        mock_pg.return_value = mock_session

        current_user = {"id": _USER_ID, "username": "student", "email": "s@test.com", "role": "STUDENT"}
        resp = get_lecture_sequence(_COURSE_ID, _TOPIC_ID, current_user)

        seg = resp.segments[0]
        assert "$" not in seg.spoken_text
        assert "\\frac" not in seg.spoken_text
        assert "Eイコール" in seg.spoken_text

    @patch("api.routes.lecture.get_user_mastered_concepts", return_value=set())
    @patch("api.routes.lecture._check_audio_cache", return_value=False)
    @patch("api.routes.lecture._pg_session")
    @patch("api.routes.lecture.get_course_data", return_value=_COURSE_DATA)
    def test_formula_latex_matches_display_text_content(
        self, mock_course, mock_pg, mock_audio, mock_mastery,
    ):
        """formulas[].latex の値が display_text 内の LaTeX 表現と一致すること。"""
        from api.routes.lecture import get_lecture_sequence

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [self._build_db_row()]
        mock_pg.return_value = mock_session

        current_user = {"id": _USER_ID, "username": "student", "email": "s@test.com", "role": "STUDENT"}
        resp = get_lecture_sequence(_COURSE_ID, _TOPIC_ID, current_user)

        seg = resp.segments[0]
        display = seg.text

        # display_text 内の LaTeX 式を抽出
        display_math = re.findall(r"\$\$([\s\S]+?)\$\$", display)
        inline_math = re.findall(r"\$([^\$\n]+?)\$", display)
        all_latex_in_text = display_math + inline_math

        # formulas 配列の latex 値がすべて display_text のどこかに存在すること
        for f in seg.formulas:
            assert any(
                f.latex in latex_expr for latex_expr in all_latex_in_text
            ), f"Formula '{f.latex}' not found in display_text LaTeX expressions: {all_latex_in_text}"


# ---------------------------------------------------------------------------
# 4. フロントエンド置換の妥当性 — formulas データで正しく復元できること
# ---------------------------------------------------------------------------


class TestFrontendSubstitutionValidity:
    """フロントエンドが formulas 配列を使って display_text から
    数式をレンダリングできることを検証するシミュレーション。"""

    def test_all_latex_in_display_text_have_matching_formula(self):
        """display_text 内の全 LaTeX 式に対応する formula エントリが存在すること。"""
        display = _LLM_RESPONSE["display_text"]
        formulas = _LLM_RESPONSE["formulas"]

        # display_text 内の LaTeX 式を抽出
        display_math = re.findall(r"\$\$([\s\S]+?)\$\$", display)
        inline_math = re.findall(r"(?<!\$)\$([^\$\n]+?)\$(?!\$)", display)
        all_latex_in_text = display_math + inline_math

        formula_latex_set = {f["latex"] for f in formulas}

        for latex_expr in all_latex_in_text:
            assert latex_expr in formula_latex_set, (
                f"LaTeX expression '{latex_expr}' in display_text has no matching formula entry. "
                f"Available: {formula_latex_set}"
            )

    def test_formula_spoken_can_replace_latex_for_audio(self):
        """formulas[].spoken を使って display_text の LaTeX 部分を
        音声用テキストに置換できること（フロントエンド TTS フォールバック相当）。"""
        display = _LLM_RESPONSE["display_text"]
        formulas = _LLM_RESPONSE["formulas"]

        audio_text = display
        for f in formulas:
            # display math
            audio_text = audio_text.replace(f"$${f['latex']}$$", f["spoken"])
            # inline math
            audio_text = audio_text.replace(f"${f['latex']}$", f["spoken"])

        # 置換後のテキストに LaTeX デリミタが残っていないこと
        assert "$$" not in audio_text
        remaining_dollars = re.findall(r"\$[^\$]+\$", audio_text)
        assert remaining_dollars == [], f"Unreplaced LaTeX: {remaining_dollars}"

    def test_formulas_array_is_not_empty_for_math_content(self):
        """数式を含むチャンクの formulas が空でないこと。"""
        assert len(_LLM_RESPONSE["formulas"]) > 0

    def test_formula_id_uniqueness(self):
        """formula ID が重複していないこと。"""
        ids = [f["id"] for f in _LLM_RESPONSE["formulas"]]
        assert len(ids) == len(set(ids))

    def test_display_text_preserves_non_formula_content(self):
        """display_text が数式以外の本文テキストも保持していること。"""
        display = _LLM_RESPONSE["display_text"]
        assert "量子力学" in display
        assert "シュレーディンガー方程式" in display
        assert "エネルギーと質量" in display

    def test_pydantic_serialization_preserves_formulas(self):
        """Pydantic モデル経由でシリアライズしても数式データが欠落しないこと。"""
        from schemas import LectureFormulaItem, LectureSegment

        seg = LectureSegment(
            chunk_id=_CHUNK_ID,
            chunk_index=0,
            text=_LLM_RESPONSE["display_text"],
            spoken_text=_LLM_RESPONSE["spoken_text"],
            formulas=[LectureFormulaItem(**f) for f in _LLM_RESPONSE["formulas"]],
        )

        dumped = seg.model_dump()
        assert len(dumped["formulas"]) == 2
        assert dumped["formulas"][0]["latex"] == _LLM_RESPONSE["formulas"][0]["latex"]
        assert dumped["formulas"][1]["latex"] == "E = mc^2"
        assert dumped["text"] == _LLM_RESPONSE["display_text"]

    def test_json_roundtrip_preserves_backslashes(self):
        """JSON シリアライズ/デシリアライズで LaTeX バックスラッシュが保持されること。"""
        original = _LLM_RESPONSE["formulas"]
        json_str = json.dumps(original, ensure_ascii=False)
        restored = json.loads(json_str)

        for orig, rest in zip(original, restored):
            assert orig["latex"] == rest["latex"], (
                f"LaTeX corrupted by JSON roundtrip: {orig['latex']!r} → {rest['latex']!r}"
            )


# ---------------------------------------------------------------------------
# 5. 数式なしチャンクの安全性
# ---------------------------------------------------------------------------


class TestNoFormulaChunk:
    """数式を含まないチャンクでもパイプラインが正常に動作すること。"""

    @patch("core.lecture.generate_text")
    def test_no_formula_chunk_returns_empty_formulas(self, mock_gen):
        """数式なしチャンクで formulas が空リストになること。"""
        from core.lecture import generate_spoken_text_and_formulas

        no_math_response = {
            "display_text": "これは数式を含まない純粋なテキストです。",
            "spoken_text": "これは数式を含まない純粋なテキストです。",
            "formulas": [],
        }
        mock_gen.return_value = json.dumps(no_math_response)

        result = generate_spoken_text_and_formulas("これは数式を含まない純粋なテキストです。")
        assert result["formulas"] == []
        assert result["display_text"] == no_math_response["display_text"]

    def test_pydantic_segment_with_empty_formulas(self):
        """formulas が空でも LectureSegment が正常に構築できること。"""
        from schemas import LectureSegment

        seg = LectureSegment(
            chunk_id="abc",
            chunk_index=0,
            text="数式なしテキスト",
            spoken_text="数式なしテキスト",
            formulas=[],
        )
        assert seg.formulas == []
        assert seg.text == "数式なしテキスト"


# ---------------------------------------------------------------------------
# 6. 複数チャンクのシーケンス整合性
# ---------------------------------------------------------------------------


class TestMultiChunkFormulaIntegrity:
    """複数チャンクを含むシーケンスで、各チャンクの数式が
    他のチャンクと混在しないことを検証。"""

    def test_formulas_are_isolated_per_segment(self):
        """build_lecture_sequence が各チャンクの formulas を独立に保持すること。"""
        from core.lecture import build_lecture_sequence

        chunks = [
            {
                "id": "c1",
                "chunk_index": 0,
                "text": "チャンク1: $E = mc^2$",
                "spoken_text": "チャンク1",
                "formulas": [{"id": "formula_0", "latex": "E = mc^2", "spoken": "Eイコールmcの二乗"}],
            },
            {
                "id": "c2",
                "chunk_index": 1,
                "text": "チャンク2: $F = ma$",
                "spoken_text": "チャンク2",
                "formulas": [{"id": "formula_0", "latex": "F = ma", "spoken": "Fイコールma"}],
            },
        ]

        result = build_lecture_sequence("topic-1", {"topics": []}, chunks)

        assert len(result) == 2
        assert result[0]["formulas"][0]["latex"] == "E = mc^2"
        assert result[1]["formulas"][0]["latex"] == "F = ma"
        # 各チャンクの formulas が混在していないこと
        assert len(result[0]["formulas"]) == 1
        assert len(result[1]["formulas"]) == 1
