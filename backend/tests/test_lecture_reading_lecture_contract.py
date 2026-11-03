"""読む表示と講義が同じ配信の契約を受け取る（第 25 周 第 3 波・IK-0944 / IK-0946）。

契約: 配信の正本 ``core/lecture.py::build_topic_slides`` の戻り値を、読む表示
（``topic_material_delivery_segments``）も講義（スライド）も同じ形で受け取る —
本文は**割った後**（区切り ``===`` を剥がした本文）、式はトピック全体（id の鍵で引く）。

- IK-0946: 1 枚にしか割れないトピックでも、読む表示に区切り ``===`` を生で出さない。
- IK-0944: 学習画面・原稿スタジオの描画は式を id の鍵で優先して引き、並び順の鍵
  （``FORMULA_i``）は空いているときだけ埋める（部分集合の formulas で別の式を引かない）。
"""

from __future__ import annotations

import re
from pathlib import Path

import core.lecture as lecture

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
ADMIN_LS_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"


def _topic(text: str) -> dict:
    return {"id": "t0", "title": "T", "student_material": {"source_text": text}}


class TestReadingViewUsesSlideText:
    def test_trailing_marker_single_slide_is_stripped(self):
        text = "本文の段落です。式 $E=mc^2$ を使います。\n\n==="
        segments, formulas = lecture.topic_material_delivery_segments(
            _topic(text), {}, text, [], [])
        assert len(segments) == 1
        assert "===" not in segments[0]
        # 本文の式は配った formulas で引ける（本文と式が同じ関数から来る）
        ids = re.findall(r"\[\[FORMULA_\d+\]\]", segments[0])
        assert ids and {f["id"] for f in formulas} >= set(ids)

    def test_leading_marker_single_slide_is_stripped(self):
        text = "===\n\n短い本文です。"
        segments, _formulas = lecture.topic_material_delivery_segments(
            _topic(text), {}, text, [], [])
        assert segments == ["短い本文です。"]

    def test_reading_and_lecture_share_text(self):
        text = "一枚目の本文。\n\n===\n\n二枚目の本文。 $a$ と $b$ と $c$。"
        slides, _d, _s, _f = lecture.build_topic_slides(_topic(text))
        segments, _formulas = lecture.topic_material_delivery_segments(
            _topic(text), {}, text, [], [])
        assert segments == [s["display_text"] for s in slides]


def _function_body(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    depth, started = 0, False
    for j in range(src.index("{", start), len(src)):
        c = src[j]
        if c == "{":
            depth += 1
            started = True
        elif c == "}":
            depth -= 1
            if started and depth == 0:
                return src[start:j + 1]
    raise AssertionError(name)


_UNCONDITIONAL_POSITION_KEY = re.compile(r'formulaById\[\s*"FORMULA_"\s*\+\s*idx\s*\]\s*=')
_UNCONDITIONAL_FALLBACK_KEY = re.compile(r"formulaById\[\s*fallbackId\s*\]\s*=")


def _position_key_overwrites(body: str) -> bool:
    """並び順の鍵を、空きを確かめずに代入する行があるか。"""
    for line in body.splitlines():
        if "hasOwnProperty" in line:
            continue
        if _UNCONDITIONAL_POSITION_KEY.search(line) or _UNCONDITIONAL_FALLBACK_KEY.search(line):
            return True
    return False


class TestFormulaKeysPreferId:
    def test_learner_renderer_prefers_id(self):
        body = _function_body(APP_JS.read_text(encoding="utf-8"), "renderMaterialChunk")
        assert not _position_key_overwrites(body)
        assert "hasOwnProperty.call(formulaById" in body

    def test_studio_renderers_prefer_id(self):
        src = ADMIN_LS_JS.read_text(encoding="utf-8")
        for name in ("lsRenderCourseMaterialPreview", "lsRenderTextWithFormulas"):
            body = _function_body(src, name)
            assert not _position_key_overwrites(body), name
            assert "hasOwnProperty.call(formulaById" in body, name

    def test_simulated_lookup_on_subset(self):
        """Python で同じ鍵規則を再現し、部分集合の formulas で各プレースホルダが自分の式を引く。"""
        subset = [{"id": f"[[FORMULA_{i}]]", "latex": f"x_{i}"} for i in (2, 3, 4)]
        table: dict[str, dict] = {}
        for f in subset:
            bare = f["id"].strip("[]")
            table[f["id"]] = table[bare] = f
        for idx, f in enumerate(subset):
            table.setdefault(f"FORMULA_{idx}", f)
        for i in (2, 3, 4):
            assert table[f"FORMULA_{i}"]["latex"] == f"x_{i}"
