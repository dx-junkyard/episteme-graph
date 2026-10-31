"""TRIAGE14（第 14 周）: 確認問題の照合・今日の言葉の空・音声未生成・壊れた復元式・分野名。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "src"))

import routes.learning as learning_routes  # noqa: E402


def _topic():
    return {"check_questions": [
        {"question": "Q1 とは？", "answer_requirements": ["要件1"], "model_answer": "A1"},
        {"question": "Q2 とは？", "answer_requirements": ["要件2"], "model_answer": "A2"},
        {"question": "Q3 とは？", "answer_requirements": ["要件3"], "model_answer": "A3"},
    ]}


def test_check_question_follows_answered_question_not_stale_item():
    stale = {"question": "Q1 とは？", "answer_requirements": ["要件1"], "model_answer": "A1"}
    item = learning_routes._select_check_question(_topic(), "Q3 とは？", stale)
    assert item["answer_requirements"] == ["要件3"] and item["model_answer"] == "A3"


def test_check_question_matches_ignoring_whitespace():
    item = learning_routes._select_check_question(_topic(), " Q2  とは？ ")
    assert item["model_answer"] == "A2"


def test_check_question_request_item_used_when_consistent():
    custom = {"question": "独自の問い", "answer_requirements": ["x"]}
    assert learning_routes._select_check_question(_topic(), "独自の問い", custom)["answer_requirements"] == ["x"]
    assert learning_routes._select_check_question(_topic(), "", custom)["answer_requirements"] == ["x"]


def test_todays_words_empty_has_note():
    from core.cycle.derive import build_todays_words
    from core.cycle.schema import EMPTY_TODAYS_WORDS_FACT

    out = build_todays_words([])
    assert out["words"] == [] and out["note"] == EMPTY_TODAYS_WORDS_FACT
    out2 = build_todays_words([{"role": "user", "text": "赤方偏移の定義を知りたい", "topic_id": "t"}])
    assert "note" not in out2


def test_broken_reconstructed_formula_is_withheld():
    from core.label_vocab import RECONSTRUCTED_EQUATION_NOTE
    from core.lecture import annotate_reconstructed_formulas

    out = annotate_reconstructed_formulas([
        {"id": "f1", "latex": "; \\tag{11}", "reconstructed": True},
        {"id": "f2", "latex": "a�b", "reconstructed": True},
        {"id": "f3", "latex": "E = mc^2 \\tag{3}", "reconstructed": True},
        {"id": "f4", "latex": "; \\tag{2}"},
    ])
    assert out[0]["latex"] == "" and out[0]["latex_withheld"] is True
    assert out[0]["reconstructed_note"] == RECONSTRUCTED_EQUATION_NOTE
    assert out[1]["latex_withheld"] is True
    assert "latex_withheld" not in out[2] and out[2]["latex"].startswith("E = mc^2")
    assert out[3] == {"id": "f4", "latex": "; \\tag{2}"}  # 抽出由来は触らない


def test_audio_status_note_when_no_audio(monkeypatch):
    import routes.lecture as lecture_routes

    monkeypatch.setattr(lecture_routes, "_topic_audio_status_payload",
                        lambda c, t, u: {"has_audio": False, "stale_language": False})
    assert lecture_routes.get_topic_audio_status("c", "t", {"id": "u"})["note"] == lecture_routes.AUDIO_NOT_GENERATED_NOTE
    monkeypatch.setattr(lecture_routes, "_topic_audio_status_payload",
                        lambda c, t, u: {"has_audio": True, "stale_language": False})
    assert "note" not in lecture_routes.get_topic_audio_status("c", "t", {"id": "u"})
    for text in (lecture_routes.AUDIO_NOT_GENERATED_NOTE, lecture_routes.AUDIO_STALE_LANGUAGE_NOTE):
        assert not any(ch.isdigit() for ch in text)


def test_particle_physics_bundled_domain_name():
    from core import atlas_store

    meta = atlas_store._bundled_domain_meta("particle_physics")
    assert meta and meta["name"] and meta["name"] != "particle_physics"
