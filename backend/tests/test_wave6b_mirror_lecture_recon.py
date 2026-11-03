"""ペルソナ通し受講 第 8 周の是正（discuss の鏡・レクチャーの数式・再構成の縮退事実文）。

- IK-0400: 鏡（〔鏡〕…〔/鏡〕）が本文の途中・末尾に出たとき、取り出した位置に在処の事実文を残す。
- IK-0404: レクチャーのスライドで、本文に既にある ``[[FORMULA_N]]`` を content_blocks の数式で解決する。
- IK-0410: ``GET .../reconstruction/next`` の ``exhausted: true`` に事実文を添える。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.discuss.mirroring import MIRROR_MOVED_NOTE, extract_mirror  # noqa: E402
from core.label_vocab import UNRESOLVED_FORMULA_PLACEHOLDER_TEXT  # noqa: E402
from core.lecture import build_topic_slides, resolve_topic_formula_placeholders  # noqa: E402
from core.reconstruction.schema import (  # noqa: E402
    FACT_NO_DELIVERABLE_ITEM,
    FACT_NO_SOURCE_MATERIAL,
)

# ---------------------------------------------------------------------------
# IK-0400 鏡の在処
# ---------------------------------------------------------------------------

_MSG = "r_d を CMB から較正すると全ビンが同じ向きに動くと思います"
_MIRROR = "〔鏡〕あなたは「全ビンが同じ向きに動く」と捉えている、で合っていますか？〔/鏡〕"


class TestMirrorPlacementNote:
    def test_tail_mirror_after_lead_in_leaves_note(self):
        """第 8 周の実測形: 前置き「次に、あなたの予想についてです。」の直後に鏡だけが来る。"""
        answer = "前提を5つ挙げました。\n\n次に、あなたの予想についてです。\n" + _MIRROR
        clean, mirror = extract_mirror(answer, _MSG)
        assert mirror is not None
        assert clean.endswith(MIRROR_MOVED_NOTE)
        assert "次に、あなたの予想についてです。" in clean
        assert "〔鏡〕" not in clean
        # 鏡文そのものは本文へ写さない（窓の外へ持ち出さない）
        assert "全ビンが同じ向きに動く" not in clean

    def test_middle_mirror_keeps_following_text(self):
        answer = "前置きです。\n" + _MIRROR + "\nここからが論文との突き合わせです。"
        clean, mirror = extract_mirror(answer, _MSG)
        assert mirror is not None
        assert MIRROR_MOVED_NOTE in clean
        assert clean.index(MIRROR_MOVED_NOTE) < clean.index("ここからが論文との突き合わせです。")
        assert clean.startswith("前置きです。")

    def test_head_mirror_leaves_no_note(self):
        answer = _MIRROR + "\n論文の主張と突き合わせましょう。"
        clean, mirror = extract_mirror(answer, _MSG)
        assert mirror is not None
        assert clean == "論文の主張と突き合わせましょう。"

    def test_failed_verbatim_mirror_leaves_no_note(self):
        answer = "前置き。\n〔鏡〕あなたは「まったく別の言い換え」と捉えている、で合っていますか？〔/鏡〕"
        clean, mirror = extract_mirror(answer, _MSG)
        assert mirror is None
        assert MIRROR_MOVED_NOTE not in clean

    def test_note_names_the_learning_screen_mirror_label(self):
        """事実文が指す枠の名前は学習画面の鏡ラベルと同じ語であること。"""
        src = (ROOT / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")
        m = re.search(r'<div class="mirror-label">([^<]+)</div>', src)
        assert m, "app.js の鏡ラベルが見つからない"
        assert f"「{m.group(1)}」" in MIRROR_MOVED_NOTE

    def test_note_has_no_digits(self):
        assert not re.search(r"\d", MIRROR_MOVED_NOTE)


# ---------------------------------------------------------------------------
# IK-0404 レクチャーの数式プレースホルダー
# ---------------------------------------------------------------------------


def _topic(text: str, items: list[dict], **extra) -> dict:
    return {
        "id": "t0",
        "student_material": {"source_text": text},
        "spoken_script": "説明します。",
        "content_blocks": [{"type": "equations", "items": items}],
        **extra,
    }


class TestLectureFormulaPlaceholders:
    def test_positional_resolution(self):
        topic = _topic("式 [[FORMULA_0]] と [[FORMULA_1]]。", [{"latex": "F=ma"}, {"latex": "E=mc^2"}])
        slides, display, _spoken, formulas = build_topic_slides(topic)
        assert "[[FORMULA_0]]" in display and "[[FORMULA_1]]" in display
        assert [f["latex"] for f in formulas] == ["F=ma", "E=mc^2"]
        assert {f["id"] for f in slides[0]["formulas"]} == {"[[FORMULA_0]]", "[[FORMULA_1]]"}

    def test_id_match_takes_priority_over_position(self):
        items = [{"equation_id": "FORMULA_1", "latex": "a=b"}, {"latex": "c=d"}]
        text, formulas = resolve_topic_formula_placeholders("[[FORMULA_1]]", {"content_blocks": [{"type": "equations", "items": items}]})
        assert text == "[[FORMULA_1]]"
        assert formulas[0]["latex"] == "a=b"

    def test_unresolved_placeholder_becomes_fact_text(self):
        topic = _topic("式 [[FORMULA_0]] と [[FORMULA_5]]。", [{"latex": "F=ma"}])
        _slides, display, _spoken, formulas = build_topic_slides(topic)
        assert "[[FORMULA_5]]" not in display
        assert UNRESOLVED_FORMULA_PLACEHOLDER_TEXT in display
        assert [f["id"] for f in formulas] == ["[[FORMULA_0]]"]

    def test_no_content_blocks_leaves_no_raw_placeholder(self):
        topic = {"id": "t0", "student_material": {"source_text": "式 [[FORMULA_0]]。"}}
        _slides, display, _spoken, formulas = build_topic_slides(topic)
        assert "[[FORMULA_" not in display
        assert formulas == []

    def test_spoken_prefers_plain_text(self):
        topic = _topic("[[FORMULA_0]]", [{"latex": "F=ma", "plain_text": "F イコール m a"}])
        _s, _d, _sp, formulas = build_topic_slides(topic)
        assert formulas[0]["spoken"] == "F イコール m a"

    def test_equation_embed_numbering_does_not_collide(self):
        """本文の placeholder の番号が飛んでいても、![[equation:…]] の解決番号とぶつからない。"""
        topic = _topic(
            "[[FORMULA_2]] と ![[equation:eq_z]]",
            [{"latex": "a"}, {"latex": "b"}, {"latex": "c"}],
            evidence_links=[{"kind": "equation", "target_id": "eq_z", "latex": "z=1"}],
        )
        _s, display, _sp, formulas = build_topic_slides(topic)
        ids = [f["id"] for f in formulas]
        assert len(ids) == len(set(ids))
        by_id = {f["id"]: f["latex"] for f in formulas}
        assert by_id["[[FORMULA_2]]"] == "c"
        assert "z=1" in by_id.values()
        assert "equation:" not in display

    def test_text_without_placeholders_is_untouched(self):
        text, formulas = resolve_topic_formula_placeholders("数式なし", {"content_blocks": []})
        assert text == "数式なし" and formulas == []


# ---------------------------------------------------------------------------
# IK-0410 再構成の exhausted に事実文
# ---------------------------------------------------------------------------


class TestReconstructionExhaustedFacts:
    def test_fact_constants_have_no_numbers(self):
        for fact in (FACT_NO_SOURCE_MATERIAL, FACT_NO_DELIVERABLE_ITEM):
            assert fact and not re.search(r"\d", fact)

    def test_every_exhausted_return_carries_facts(self):
        src = (BACKEND / "api" / "routes" / "reconstruction.py").read_text(encoding="utf-8")
        returns = re.findall(r'return \{[^}]*"exhausted": True[^}]*\}', src)
        assert returns, "exhausted の return が見つからない"
        for ret in returns:
            assert '"facts": [FACT_' in ret, ret

    @pytest.fixture
    def client_and_student(self):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient
        from api.main import app
        from dependencies import ROLE_STUDENT, _create_token

        tok = _create_token("11111111-1111-1111-1111-111111111111", "stu", "stu@x", ROLE_STUDENT)
        return TestClient(app), {"Authorization": "Bearer " + tok}

    def _fake_session(self):
        result = MagicMock()
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        session = MagicMock()
        session.execute.return_value = result
        return session

    def test_no_source_material_fact(self, client_and_student, monkeypatch):
        client, headers = client_and_student
        import routes.reconstruction as route_mod
        monkeypatch.setattr(route_mod, "get_accessible_course_data", lambda uid, cid: {"sources": []})
        monkeypatch.setattr(route_mod, "_pg_session", self._fake_session)
        r = client.get("/api/learning/courses/c1/topics/t1/reconstruction/next", headers=headers)
        assert r.status_code == 200
        assert r.json() == {"item": None, "exhausted": True, "facts": [FACT_NO_SOURCE_MATERIAL]}

    def test_no_deliverable_item_fact(self, client_and_student, monkeypatch):
        client, headers = client_and_student
        import routes.reconstruction as route_mod
        monkeypatch.setattr(
            route_mod, "get_accessible_course_data",
            lambda uid, cid: {"sources": [{"material_id": "m1"}], "topics": [{"id": "t1"}]},
        )
        monkeypatch.setattr(route_mod, "_pg_session", self._fake_session)
        r = client.get("/api/learning/courses/c1/topics/t1/reconstruction/next", headers=headers)
        assert r.status_code == 200
        assert r.json() == {"item": None, "exhausted": True, "facts": [FACT_NO_DELIVERABLE_ITEM]}
