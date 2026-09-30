"""第 12 周のハーネス側の欠陥の是正（IK-0506 / IK-0507）。

- IK-0506: campaign が固定したコースと別の同名コースをペルソナが受講・表示したら、runner_note・meta・審判 A に残す
  （選択は差し替えない）。砂場準備 ``pin_course.py`` の選別は純関数で確かめる（API は叩かない）。
- IK-0507: 教材の投影が ``![[kind:id]]`` を画面（app.js renderMaterialChunk）と同じ規則で描く。
"""
import json

import httpx

from uxsim.oracles import dialogue
from uxsim.oracles.findings import FindingFactory
from uxsim.runner.actions_exec import PINNED_MISMATCH_MARK, execute, note_pinned_course_mismatch
from uxsim.runner.api import record_pinned_mismatch
from uxsim.runner.client import EpistemeClient
from uxsim.runner.state import PersonaSession, project_observation, render_material_text
from uxsim.sandbox.pin_course import owner_username_from_email, select_conflicts
from uxsim.schema import RunMeta, TranscriptStep

TITLE = "宇宙物理 論文紹介ゼミ"


def _client(routes: dict) -> EpistemeClient:
    def handler(req: httpx.Request) -> httpx.Response:
        key = f"{req.method} {req.url.path}"
        status, body = routes.get(key, (404, {"detail": "nf"}))
        return httpx.Response(status, json=body)

    return EpistemeClient("http://x", transport=httpx.MockTransport(handler))


def _pinned_session() -> PersonaSession:
    s = PersonaSession(persona_id="st-01")
    s.scratch["pinned_course_id"] = "1bad7d9f"
    return s


# ----------------------------------------------------------------------------
# IK-0506
# ----------------------------------------------------------------------------

def test_enroll_other_course_is_noted_but_not_overridden():
    s = _pinned_session()
    c = _client({"POST /api/learning/courses/6be229e1/enroll": (201, {"id": "6be229e1"})})
    execute("learning.course.enroll", {"course_id": "6be229e1"}, s, c)
    assert s.course_id == "6be229e1"  # ペルソナの選択はそのまま
    notes = s.scratch["runner_notes"]
    assert any(n.startswith(PINNED_MISMATCH_MARK) and "1bad7d9f" in n and "6be229e1" in n for n in notes)
    assert s.scratch["pinned_course_mismatch"] == [
        {"pinned": "1bad7d9f", "actual": "6be229e1", "action_id": "learning.course.enroll"}]


def test_open_pinned_course_is_not_noted_and_repeat_is_deduped():
    s = _pinned_session()
    c = _client({"GET /api/learning/courses/1bad7d9f": (200, {"id": "1bad7d9f", "topics": []}),
                 "GET /api/learning/courses/6be229e1": (200, {"id": "6be229e1", "topics": []})})
    execute("learning.course.open", {"course_id": "1bad7d9f"}, s, c)
    assert "pinned_course_mismatch" not in s.scratch
    execute("learning.course.open", {"course_id": "6be229e1"}, s, c)
    execute("learning.course.open", {"course_id": "6be229e1"}, s, c)
    assert len(s.scratch["pinned_course_mismatch"]) == 1


def test_no_pin_means_no_note():
    s = PersonaSession(persona_id="p")
    assert note_pinned_course_mismatch(s, "6be229e1", "learning.course.open") is False
    assert "runner_notes" not in s.scratch


def test_record_pinned_mismatch_moves_to_meta():
    s = _pinned_session()
    note_pinned_course_mismatch(s, "6be229e1", "learning.course.enroll")
    meta = RunMeta(run_id="r", campaign_id="c", domain="d", snapshot="", pinned_course_id="1bad7d9f")
    assert record_pinned_mismatch(meta, s) is True
    assert meta.pinned_course_mismatch[0]["persona_id"] == "st-01"
    assert meta.pinned_course_mismatch[0]["actual"] == "6be229e1"
    assert any(n.startswith(PINNED_MISMATCH_MARK) for n in meta.notes)
    assert record_pinned_mismatch(meta, s) is False  # 二重に移さない
    json.loads(meta.model_dump_json())  # JSON に落ちる


def test_dialogue_oracle_flags_pinned_mismatch_once_per_session():
    note = f"{PINNED_MISMATCH_MARK}: campaign が固定した course_id=1bad7d9f ではなく 6be229e1 を…"
    steps = [TranscriptStep(seq=1, persona_id="st-01", action_id="auth.login"),
             TranscriptStep(seq=4, persona_id="st-01", action_id="learning.course.enroll", runner_notes=[note]),
             TranscriptStep(seq=9, persona_id="st-01", action_id="learning.course.open", runner_notes=[note])]
    found = [f for f in dialogue.check(steps, FindingFactory()) if f.hypothesis == dialogue.HYP_PINNED_MISMATCH]
    assert len(found) == 1
    f = found[0]
    assert f.oracle == "A" and f.hypothesis.startswith("ハーネス:") and f.suspected_layer == ["cycle_verification"]
    assert f.evidence.transcript_steps == [4, 9]


def test_dialogue_oracle_silent_without_mismatch():
    steps = [TranscriptStep(seq=4, persona_id="st-01", action_id="learning.course.enroll",
                            runner_notes=["runner_default: campaign の course_id=1bad7d9f を受講可能な候補にした"])]
    assert not [f for f in dialogue.check(steps, FindingFactory()) if f.hypothesis == dialogue.HYP_PINNED_MISMATCH]


def test_report_warns_on_mismatch(tmp_path):
    from uxsim.report.campaign_report import build_report

    meta = RunMeta(run_id="r", campaign_id="c", domain="d", snapshot="", pinned_course_id="1bad7d9f",
                   pinned_course_mismatch=[{"pinned": "1bad7d9f", "actual": "6be229e1", "persona_id": "st-01"}])
    (tmp_path / "meta.json").write_text(meta.model_dump_json(), encoding="utf-8")
    (tmp_path / "transcript.jsonl").write_text("", encoding="utf-8")
    out = build_report(tmp_path)
    assert "固定したコース: 1bad7d9f" in out and "st-01→6be229e1" in out


def test_pin_course_selects_same_title_public_only():
    rows = [{"id": "1bad7d9f", "title": TITLE, "visibility": "public", "is_enrollable": True},
            {"id": "6be229e1", "title": f" {TITLE} ", "visibility": "public", "is_enrollable": True},
            {"id": "aaaa0000", "title": TITLE, "visibility": "private", "is_enrollable": False},
            {"id": "bbbb0000", "title": TITLE + "（別）", "visibility": "public", "is_enrollable": True},
            {"id": "6be229e1", "title": TITLE, "visibility": "public", "is_enrollable": False}]
    target, conflicts = select_conflicts(rows, "1bad7d9f")
    assert target["id"] == "1bad7d9f"
    assert [c["id"] for c in conflicts] == ["6be229e1"]
    assert select_conflicts(rows, "zzzz") == (None, [])


def test_pin_course_owner_username():
    assert owner_username_from_email("uxsim_te_01_x@uxsim.invalid", "Administrator") == "uxsim_te_01_x"
    assert owner_username_from_email("administrator@example.com", "Administrator") == "Administrator"
    assert owner_username_from_email("someone@example.com", "Administrator") == ""


def test_pin_course_defaults_to_dry_run():
    import inspect

    from uxsim.sandbox import pin_course

    src = inspect.getsource(pin_course.main)
    assert '"--yes"' in src and "if not a.yes" in src
    assert "INSERT" not in inspect.getsource(pin_course) and "UPDATE " not in inspect.getsource(pin_course)


# ----------------------------------------------------------------------------
# IK-0507
# ----------------------------------------------------------------------------

def _chunk(text: str, **kw) -> dict:
    return {"text": text, **kw}


def test_claim_and_component_embeds_render_as_anchor_chips():
    ev = [{"kind": "claim", "id": "c1", "title": "磁場は乱流優勢"},
          {"kind": "component", "id": "comp_a", "label": "偏光モデル"}]
    out = render_material_text(_chunk("A ![[claim:c1]] B [[component:comp_a]]", evidence_items=ev))
    assert out == "A 〔⚓ 磁場は乱流優勢〕 B 〔⚓ 偏光モデル〕"


def test_kind_mismatch_falls_back_to_same_id():
    ev = [{"kind": "component", "id": "x1", "title": "部品"}]
    assert render_material_text(_chunk("![[claim:x1]]", evidence_items=ev)) == "〔⚓ 部品〕"


def test_source_embed_renders_card_or_missing_card():
    ev = [{"kind": "source", "id": "topic_summary", "title": "要旨", "summary": "  本文   の要約  "}]
    assert render_material_text(_chunk("![[source:topic_summary]]", evidence_items=ev)) == "〔出典: 要旨〕本文 の要約"
    # 引けない埋め込みは画面と同じく未解決カード（kind:id を出す）— 隠さない
    out = render_material_text(_chunk("前 ![[source:topic_summary]] 後"))
    assert "未解決 source:topic_summary" in out and "取得できませんでした" in out and "![[" not in out


def test_equation_embed_uses_formulas_or_pending():
    f = [{"id": "eq_3", "latex": "E=mc^2"}]
    assert render_material_text(_chunk("![[equation:eq_eq_3]]", formulas=f)) == "$$E=mc^2$$"
    assert render_material_text(_chunk("![[equation:[[eq_3]]]]", formulas=f)) == "$$E=mc^2$$"
    assert render_material_text(_chunk("![[equation:eq_9]]", formulas=f)) == "〔数式は準備中です〕"
    ev = [{"kind": "equation", "id": "eq_9", "plain_text": "E equals m c squared"}]
    assert render_material_text(_chunk("![[equation:eq_9]]", evidence_items=ev)) == "E equals m c squared"


def test_figure_placeholder_and_unserved_figure_embed():
    figs = [{"id": "[[FIGURE_1]]", "figure_id": "f1", "caption": "図1 偏光", "image_url": "/api/x"}]
    assert render_material_text(_chunk("[[FIGURE_1]]", figures=figs)) == "〔図〕 図1 偏光"
    nofig = [{"figure_id": "f2", "caption": "c"}]
    assert "画像を取得できませんでした" in render_material_text(_chunk("[[FIGURE_1]]", figures=nofig))
    ev = [{"kind": "figure", "id": "f9", "caption": "未配信の図"}]
    assert render_material_text(_chunk("![[figure:f9]]", evidence_items=ev)) == "〔図: 図: 未配信の図〕未配信の図"
    assert "未解決 figure:f8" in render_material_text(_chunk("![[figure:f8]]"))


def test_unresolvable_placeholders_left_and_drop_flag_hides_missing():
    assert render_material_text(_chunk("式 [[FORMULA_3]]", formulas=[{"latex": "x"}])) == "式 [[FORMULA_3]]"
    assert render_material_text(_chunk("a ![[claim:nope]] b", drop_unresolved_embeds=True)) == "a  b"


def test_topic_open_projection_renders_embeds():
    body = {"chunks": [{"text": "要点 ![[claim:c1]] と ![[source:topic_summary]] [[FORMULA_0]]",
                        "formulas": [{"latex": "B"}], "evidence_items": [{"kind": "claim", "id": "c1", "title": "T"}]}]}
    out = project_observation("learning.topic.open", 200, body)
    assert "〔⚓ T〕" in out and "$B$" in out and "未解決 source:topic_summary" in out
    assert "![[" not in out
