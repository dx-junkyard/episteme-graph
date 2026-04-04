"""数式レンダリングパイプラインの結合テスト (Issue #91)。

プレースホルダー分離方式（[[FORMULA_N]]）による数式レンダリングの安定性を検証する。
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
# テスト用定数 — LLM モックが返すプレースホルダー方式レスポンス
# ---------------------------------------------------------------------------

_CHUNK_TEXT_RAW = (
    "量子力学の基本方程式として、シュレーディンガー方程式 "
    "i\\hbar \\frac{\\partial}{\\partial t}\\Psi = \\hat{H}\\Psi がある。"
    "また、エネルギーと質量の関係は E = mc^2 で表される。"
)

_LLM_RESPONSE = {
    "display_text": (
        "量子力学の基本方程式として、シュレーディンガー方程式 "
        "[[FORMULA_0]] がある。\n\n"
        "また、エネルギーと質量の関係は [[FORMULA_1]] で表される。"
    ),
    "spoken_text": (
        "量子力学の基本方程式として、シュレーディンガー方程式 "
        "アイ エイチバー パーシャルティー分のパーシャルプサイ イコール ハミルトニアンプサイ がある。"
        "...また、エネルギーと質量の関係は Eイコールmcの二乗 で表される。"
    ),
    "formulas": [
        {
            "id": "[[FORMULA_0]]",
            "latex": "i\\hbar \\frac{\\partial}{\\partial t}\\Psi = \\hat{H}\\Psi",
            "spoken": "アイ エイチバー パーシャルティー分のパーシャルプサイ イコール ハミルトニアンプサイ",
            "is_display": True,
        },
        {
            "id": "[[FORMULA_1]]",
            "latex": "E = mc^2",
            "spoken": "Eイコールmcの二乗",
            "is_display": False,
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

# プレースホルダーパターン: [[FORMULA_N]] 形式
_PLACEHOLDER_PATTERN = re.compile(r"\[\[FORMULA_\d+\]\]")


# ---------------------------------------------------------------------------
# 1. LLM モック設定 — プレースホルダー + 数式辞書を返す
# ---------------------------------------------------------------------------


class TestLLMMockConfiguration:
    """LLM モックがプレースホルダー方式の数式メタデータ付きレスポンスを正しく生成すること。"""

    @patch("core.lecture.generate_text")
    def test_mock_returns_display_text_with_placeholders(self, mock_gen):
        """LLM モックが display_text にプレースホルダー [[FORMULA_N]] を含むレスポンスを返す。"""
        from core.lecture import generate_spoken_text_and_formulas

        mock_gen.return_value = json.dumps(_LLM_RESPONSE)

        result = generate_spoken_text_and_formulas(_CHUNK_TEXT_RAW)

        # display_text にプレースホルダーが含まれること
        assert "[[FORMULA_0]]" in result["display_text"]
        assert "[[FORMULA_1]]" in result["display_text"]
        # display_text に生の LaTeX デリミタ ($, $$) が含まれないこと
        assert "$$" not in result["display_text"]
        assert re.search(r"(?<!\[)\$(?!\$)", result["display_text"]) is None

    @patch("core.lecture.generate_text")
    def test_mock_returns_formulas_with_required_keys(self, mock_gen):
        """formulas 配列の各要素に id / latex / spoken / is_display が含まれること。"""
        from core.lecture import generate_spoken_text_and_formulas

        mock_gen.return_value = json.dumps(_LLM_RESPONSE)

        result = generate_spoken_text_and_formulas(_CHUNK_TEXT_RAW)

        assert len(result["formulas"]) == 2
        for f in result["formulas"]:
            assert "id" in f
            assert "latex" in f
            assert "spoken" in f
            assert "is_display" in f
            assert f["latex"]  # 空文字でないこと
            assert f["spoken"]  # 空文字でないこと
            assert isinstance(f["is_display"], bool)

    @patch("core.lecture.generate_text")
    def test_formula_ids_match_placeholders(self, mock_gen):
        """formula ID が display_text 内のプレースホルダーと完全一致すること。"""
        from core.lecture import generate_spoken_text_and_formulas

        mock_gen.return_value = json.dumps(_LLM_RESPONSE)

        result = generate_spoken_text_and_formulas(_CHUNK_TEXT_RAW)

        ids = [f["id"] for f in result["formulas"]]
        assert ids == ["[[FORMULA_0]]", "[[FORMULA_1]]"]

        # 各 ID が display_text 内に存在すること
        for fid in ids:
            assert fid in result["display_text"]

    @patch("core.lecture.generate_text")
    def test_spoken_text_contains_no_raw_latex(self, mock_gen):
        """spoken_text に生の LaTeX デリミタやプレースホルダーが含まれないこと。"""
        from core.lecture import generate_spoken_text_and_formulas

        mock_gen.return_value = json.dumps(_LLM_RESPONSE)

        result = generate_spoken_text_and_formulas(_CHUNK_TEXT_RAW)

        assert "$" not in result["spoken_text"]
        assert "\\frac" not in result["spoken_text"]
        assert "[[FORMULA_" not in result["spoken_text"]

    @patch("core.lecture.generate_text")
    def test_is_display_flag_values(self, mock_gen):
        """is_display フラグがブロック数式/インライン数式を正しく区別すること。"""
        from core.lecture import generate_spoken_text_and_formulas

        mock_gen.return_value = json.dumps(_LLM_RESPONSE)

        result = generate_spoken_text_and_formulas(_CHUNK_TEXT_RAW)

        # formula_0 はブロック数式 (シュレーディンガー方程式)
        assert result["formulas"][0]["is_display"] is True
        # formula_1 はインライン数式 (E=mc^2)
        assert result["formulas"][1]["is_display"] is False


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

        # display_text にプレースホルダーが含まれていること
        assert "[[FORMULA_0]]" in params["display_text"]
        assert "[[FORMULA_1]]" in params["display_text"]
        # display_text に生の LaTeX デリミタが含まれないこと
        assert "$$" not in params["display_text"]

        # formulas に is_display フラグが含まれていること
        assert formulas_json[0]["is_display"] is True
        assert formulas_json[1]["is_display"] is False

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
                "formulas": [{"id": "[[FORMULA_0]]", "latex": "x", "spoken": "エックス", "is_display": False}],
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
    def test_sequence_api_returns_display_text_with_placeholders(
        self, mock_course, mock_pg, mock_audio, mock_mastery,
    ):
        """GET /sequence のレスポンス segments[].text にプレースホルダーが含まれること。"""
        from api.routes.lecture import get_lecture_sequence

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [self._build_db_row()]
        mock_pg.return_value = mock_session

        current_user = {"id": _USER_ID, "username": "student", "email": "s@test.com", "role": "STUDENT"}
        resp = get_lecture_sequence(_COURSE_ID, _TOPIC_ID, current_user)

        assert resp.total_segments >= 1
        seg = resp.segments[0]

        # display_text にプレースホルダーが含まれていること
        assert "[[FORMULA_0]]" in seg.text
        assert "[[FORMULA_1]]" in seg.text
        # 生の LaTeX デリミタが含まれないこと
        assert "$$" not in seg.text

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

        # 各 formula に id / latex / spoken / is_display が含まれること
        for f in seg.formulas:
            assert f.id.startswith("[[FORMULA_")
            assert f.latex  # 空でない
            assert f.spoken  # 空でない
            assert isinstance(f.is_display, bool)

        # 具体的な値の検証
        assert seg.formulas[0].latex == _LLM_RESPONSE["formulas"][0]["latex"]
        assert seg.formulas[1].latex == "E = mc^2"
        assert seg.formulas[0].is_display is True
        assert seg.formulas[1].is_display is False

    @patch("api.routes.lecture.get_user_mastered_concepts", return_value=set())
    @patch("api.routes.lecture._check_audio_cache", return_value=False)
    @patch("api.routes.lecture._pg_session")
    @patch("api.routes.lecture.get_course_data", return_value=_COURSE_DATA)
    def test_sequence_api_spoken_text_is_natural_language(
        self, mock_course, mock_pg, mock_audio, mock_mastery,
    ):
        """GET /sequence の spoken_text が自然言語であり、
        LaTeX やプレースホルダーを含まないこと。"""
        from api.routes.lecture import get_lecture_sequence

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [self._build_db_row()]
        mock_pg.return_value = mock_session

        current_user = {"id": _USER_ID, "username": "student", "email": "s@test.com", "role": "STUDENT"}
        resp = get_lecture_sequence(_COURSE_ID, _TOPIC_ID, current_user)

        seg = resp.segments[0]
        assert "$" not in seg.spoken_text
        assert "\\frac" not in seg.spoken_text
        assert "[[FORMULA_" not in seg.spoken_text
        assert "Eイコール" in seg.spoken_text

    @patch("api.routes.lecture.get_user_mastered_concepts", return_value=set())
    @patch("api.routes.lecture._check_audio_cache", return_value=False)
    @patch("api.routes.lecture._pg_session")
    @patch("api.routes.lecture.get_course_data", return_value=_COURSE_DATA)
    def test_formula_ids_match_display_text_placeholders(
        self, mock_course, mock_pg, mock_audio, mock_mastery,
    ):
        """formulas[].id の値が display_text 内のプレースホルダーと完全一致すること。"""
        from api.routes.lecture import get_lecture_sequence

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [self._build_db_row()]
        mock_pg.return_value = mock_session

        current_user = {"id": _USER_ID, "username": "student", "email": "s@test.com", "role": "STUDENT"}
        resp = get_lecture_sequence(_COURSE_ID, _TOPIC_ID, current_user)

        seg = resp.segments[0]
        display = seg.text

        # display_text 内の全プレースホルダーを抽出
        placeholders_in_text = set(_PLACEHOLDER_PATTERN.findall(display))
        formula_ids = {f.id for f in seg.formulas}

        # formulas 配列の全 ID が display_text に存在すること
        assert formula_ids == placeholders_in_text, (
            f"Mismatch: formula IDs={formula_ids}, placeholders in text={placeholders_in_text}"
        )


# ---------------------------------------------------------------------------
# 4. フロントエンド置換の妥当性 — formulas データで正しく復元できること
# ---------------------------------------------------------------------------


class TestFrontendSubstitutionValidity:
    """フロントエンドが formulas 配列を使って display_text から
    プレースホルダーを数式に置換できることを検証するシミュレーション。"""

    def test_all_placeholders_have_matching_formula(self):
        """display_text 内の全プレースホルダーに対応する formula エントリが存在すること。"""
        display = _LLM_RESPONSE["display_text"]
        formulas = _LLM_RESPONSE["formulas"]

        placeholders_in_text = set(_PLACEHOLDER_PATTERN.findall(display))
        formula_ids = {f["id"] for f in formulas}

        assert placeholders_in_text == formula_ids, (
            f"Mismatch: placeholders={placeholders_in_text}, formula IDs={formula_ids}"
        )

    def test_placeholder_substitution_replaces_all(self):
        """formulas の ID を使った split/join 置換で全プレースホルダーが消えること。
        これはフロントエンドの text.split(f.id).join(rendered) と同等のロジック。"""
        display = _LLM_RESPONSE["display_text"]
        formulas = _LLM_RESPONSE["formulas"]

        result = display
        for f in formulas:
            # フロントエンドと同じ split/join 置換
            result = result.replace(f["id"], f"[RENDERED:{f['latex']}]")

        # 置換後にプレースホルダーが残っていないこと
        remaining = _PLACEHOLDER_PATTERN.findall(result)
        assert remaining == [], f"Unreplaced placeholders: {remaining}"

    def test_formula_spoken_can_replace_placeholders_for_audio(self):
        """formulas[].spoken を使ってプレースホルダーを音声用テキストに置換できること。"""
        display = _LLM_RESPONSE["display_text"]
        formulas = _LLM_RESPONSE["formulas"]

        audio_text = display
        for f in formulas:
            audio_text = audio_text.replace(f["id"], f["spoken"])

        # 置換後にプレースホルダーが残っていないこと
        remaining = _PLACEHOLDER_PATTERN.findall(audio_text)
        assert remaining == [], f"Unreplaced placeholders: {remaining}"
        # LaTeX デリミタも含まれないこと
        assert "$$" not in audio_text

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
        assert dumped["formulas"][0]["is_display"] is True
        assert dumped["formulas"][1]["is_display"] is False
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

    def test_display_text_contains_no_latex_delimiters(self):
        """display_text に $...$ や $$...$$ が含まれないこと（プレースホルダー方式の核心）。"""
        display = _LLM_RESPONSE["display_text"]
        assert "$$" not in display
        # 単独の $ もないこと（文脈的に通貨記号等を除外）
        assert not re.search(r"\$[A-Za-z\\]", display), (
            "display_text contains LaTeX-like dollar sign pattern"
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
                "text": "チャンク1: [[FORMULA_0]]",
                "spoken_text": "チャンク1",
                "formulas": [{"id": "[[FORMULA_0]]", "latex": "E = mc^2", "spoken": "Eイコールmcの二乗", "is_display": False}],
            },
            {
                "id": "c2",
                "chunk_index": 1,
                "text": "チャンク2: [[FORMULA_0]]",
                "spoken_text": "チャンク2",
                "formulas": [{"id": "[[FORMULA_0]]", "latex": "F = ma", "spoken": "Fイコールma", "is_display": False}],
            },
        ]

        result = build_lecture_sequence("topic-1", {"topics": []}, chunks)

        assert len(result) == 2
        assert result[0]["formulas"][0]["latex"] == "E = mc^2"
        assert result[1]["formulas"][0]["latex"] == "F = ma"
        # 各チャンクの formulas が混在していないこと
        assert len(result[0]["formulas"]) == 1
        assert len(result[1]["formulas"]) == 1


# ---------------------------------------------------------------------------
# 7. フォールバック（LLM 失敗時）のプレースホルダー方式テスト
# ---------------------------------------------------------------------------


class TestFallbackPlaceholderFormat:
    """LLM が失敗した場合のフォールバックもプレースホルダー方式を使用すること。"""

    def test_fallback_display_math_uses_placeholder(self):
        """フォールバックで $$...$$ がプレースホルダーに変換されること。"""
        from core.lecture import _fallback_spoken_text

        text = "エネルギーは $$E = mc^2$$ で表される。"
        result = _fallback_spoken_text(text)

        # display_text にプレースホルダーが含まれること
        assert "[[FORMULA_0]]" in result["display_text"]
        # display_text に生の LaTeX デリミタが含まれないこと
        assert "$$" not in result["display_text"]
        # formulas の id がプレースホルダー形式であること
        assert result["formulas"][0]["id"] == "[[FORMULA_0]]"
        assert result["formulas"][0]["latex"] == "E = mc^2"
        assert result["formulas"][0]["is_display"] is True

    def test_fallback_inline_math_uses_placeholder(self):
        """フォールバックで $...$ がプレースホルダーに変換されること。"""
        from core.lecture import _fallback_spoken_text

        text = "変数 $x$ と $y$ の関係"
        result = _fallback_spoken_text(text)

        assert "[[FORMULA_0]]" in result["display_text"]
        assert "[[FORMULA_1]]" in result["display_text"]
        assert "$" not in result["display_text"]
        assert len(result["formulas"]) == 2
        assert result["formulas"][0]["is_display"] is False
        assert result["formulas"][1]["is_display"] is False

    def test_fallback_mixed_math_uses_placeholder(self):
        """フォールバックでインラインとディスプレイ数式が混在するケース。"""
        from core.lecture import _fallback_spoken_text

        text = "インライン $a+b$ とディスプレイ $$F = ma$$ が混在"
        result = _fallback_spoken_text(text)

        assert len(result["formulas"]) == 2
        # 各 formula に is_display フラグが正しくセットされていること
        display_flags = {f["id"]: f["is_display"] for f in result["formulas"]}
        # $$F = ma$$ はブロック数式
        block_formulas = [f for f in result["formulas"] if f["is_display"] is True]
        inline_formulas = [f for f in result["formulas"] if f["is_display"] is False]
        assert len(block_formulas) == 1
        assert len(inline_formulas) == 1
        assert block_formulas[0]["latex"] == "F = ma"
        assert inline_formulas[0]["latex"] == "a+b"

    def test_fallback_no_math(self):
        """フォールバックで数式なしテキストはそのまま返ること。"""
        from core.lecture import _fallback_spoken_text

        text = "これは普通のテキストです。"
        result = _fallback_spoken_text(text)

        assert result["display_text"] == text
        assert result["spoken_text"] == text
        assert result["formulas"] == []

    def test_fallback_spoken_text_replaces_with_reference(self):
        """フォールバックの spoken_text で数式が（数式N）に置換されること。"""
        from core.lecture import _fallback_spoken_text

        text = "式は $$E = mc^2$$ である。"
        result = _fallback_spoken_text(text)

        assert "（数式1）" in result["spoken_text"]
        assert "$$" not in result["spoken_text"]
