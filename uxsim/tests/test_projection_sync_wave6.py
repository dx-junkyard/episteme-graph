"""第 25 周 第 6 波（第 6 周の是正）の後の投影の同期を固定する。

写すキーの一覧は ``uxsim/runs/round25/sync_keys_wave6.md``、同期の記録は ``projection_sync_wave6.md``。
純関数と JS の逐語の照合だけ（外部呼び出し・DB なし）。
"""
from __future__ import annotations

from uxsim.actions.registry import REGISTRY as ACTIONS_BY_ID
from uxsim.config import REPO_ROOT
from uxsim.oracles import doubt as doubt_oracle
from uxsim.runner import atlas_exec as ax, doubt_exec as dx, share_exec as sx, studio_exec as stx
from uxsim.runner.state import PersonaSession, project_observation
from uxsim.schema import HttpTrace

_JS = REPO_ROOT / "frontend" / "public" / "js"


def _js(name: str) -> str:
    return (_JS / name).read_text(encoding="utf-8")


# --- 面 C（atlas）---------------------------------------------------------------------------------
def test_published_view_with_draft_heading_mirrors_admin_js():
    assert ax.PUBLISHED_VIEW_WITH_DRAFT_TEXT in _js("admin.js")
    frozen = {"version": "2026.3", "regions": [], "edges": []}
    closed = ax.published_view_lines(frozen, False, with_draft=True)
    assert closed and "下で編集している次版の下書きとは別です" in closed[0] and "2026.3" in closed[0]
    assert "の項目と関係を見る（読み取り専用）" in ax.published_view_lines(frozen, False)[0]


def test_overview_shows_published_view_even_with_draft():
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["atlas_state"] = {"frozen": {"version": "2026.3", "skeleton": {"version": "2026.3", "regions": []}},
                                "draft": {"skeleton": {"regions": []}, "validation": {}}}
    text = "\n".join(ax.overview_lines(s))
    assert "下で編集している次版の下書きとは別です" in text
    assert text.index("公開中の版（") < text.index("次版の下書き（プレビュー）")


def test_freeze_blocker_goes_to_first_card_without_showing_target_key():
    impact = {"readiness": {"ready": False, "pending_items": [
        {"kind": "edge", "label": "偏光 — 重力波", "target_key": "edge|d|a|b"}]}}
    blocker = ax.first_freeze_blocker(impact)
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["atlas_edges"] = {"candidates": [{"edge_key": "edge|d|a|b"}]}
    line = ax.freeze_blocker_goto_line(s, blocker)
    assert "「偏光 — 重力波」のカードへ移った" in line and "edge|d|a|b" not in line
    s.scratch["atlas_edges"] = {"candidates": []}
    assert "グループへ移った" in ax.freeze_blocker_goto_line(s, blocker)
    assert "候補の区画へ移った" in ax.freeze_blocker_goto_line(s, None)
    assert "\"freeze_blocked\"" not in line and "target_key" in _js("admin.js")


# --- 面 B（doubt）---------------------------------------------------------------------------------
def test_assumption_cards_use_server_labels_and_ledger_button():
    data = {"mining_note": None, "items": [
        {"id": "a1", "statement": "前提A", "origin": "manual", "status": "confirmed",
         "origin_label": "教員が手で記帳", "status_label": "確定済み", "ledger_document_id": "d1"},
        {"id": "a2", "statement": "前提B", "origin": "corpus_audit", "status": "confirmed",
         "origin_label": "コーパス監査", "status_label": "確定済み", "ledger_note": "この前提はどの論文にも結ばれていないため…"}]}
    text = "\n".join(dx.assumption_lines(data, "confirmed"))
    assert "前提A［教員が手で記帳 / 確定済み］" in text and "manual" not in text
    assert "［台帳を開く］" in text and "この前提はどの論文にも結ばれていないため…" in text
    assert "None" not in text  # mining_note が null なら行を出さない
    assert "doubt-atlas.assumption-open-ledger" in _js("doubt-atlas.js")
    assert "admin.doubt.assumption_open_ledger" in ACTIONS_BY_ID
    assert "admin.doubt.assumption_open_ledger" in dx.HANDLERS


def test_ledger_write_403_english_detail_is_flagged():
    from uxsim.oracles.findings import FindingFactory
    t = HttpTrace(method="POST", path="/api/admin/doubt/ledger/claim/x/scopes", status=404)
    assert doubt_oracle._is_ledger_write(t)
    assert not doubt_oracle._is_ledger_write(HttpTrace(method="GET", path="/api/admin/doubt/ledger/claim/x", status=404))
    del FindingFactory  # import が通ること（検査の本体は既存の _check_detail）


# --- 面 A（share）---------------------------------------------------------------------------------
def test_identity_candidates_show_partner_note_decision_line_and_outcome():
    body = {"candidates": [{"entry": {"name": "状態方程式", "review_status": "candidate",
                                      "partner_replaced_note": "この候補は、解析のやり直しで片側の部品が置き換わった…"},
                            "links": [{"status": "confirmed", "document_title": "論文A",
                                       "decision_line": "〔確定〕 確定: 堀・2026-10-27"}]}]}
    text = sx.identity_candidates_view(body, ["結び付きを確定しました（〔確定〕 確定: 堀・2026-10-27）。"])
    assert "片側の部品が置き換わった" in text and "〔確定〕 確定: 堀・2026-10-27" in text
    assert text.index("（一覧の直前に表示: 結び付きを確定しました") < text.index("1. 状態方程式")


def test_dismissed_labels_fold_and_restore():
    labels = [{"label": "EOS", "kind": "alternate", "status": "confirmed"},
              {"label": "旧称", "kind": "alternate", "status": "dismissed", "decision_line": "見送り: 誤記（堀・2026-10-27）"}]
    closed = sx._labels_lines(labels)
    assert any(l.startswith("▸ 見送り済みのラベル") for l in closed) and "旧称" not in "\n".join(closed)
    opened = "\n".join(sx._labels_lines(labels, dismissed_open=True))
    assert "旧称" in opened and "［見送りを取り消す］" in opened and "見送り: 誤記" in opened
    assert sx.LABELS_DISMISSED_SUMMARY in _js("admin.js")
    for aid in ("admin.library.labels_dismissed_open", "admin.library.label_restore"):
        assert aid in ACTIONS_BY_ID and aid in sx.HANDLERS


def test_bound_parts_stranded_note_and_note_text():
    body = {"identity_links": [{"status": "confirmed", "instance_label": "部品", "decided_by_label": "堀",
                                "decided_on": "2026-10-27", "instance_stranded_note": "この記録は再解析前の部品に付いています。"}]}
    text = "\n".join(sx.bound_parts_lines(body))
    assert "確定: 堀・2026-10-27" in text and "この記録は再解析前の部品に付いています。" in text
    assert sx.BOUND_PARTS_NOTE in _js("admin.js")


def test_explanation_share_toggle_only_for_author():
    exp = {"kind": "personal", "author_name": "堀", "review_status": "teacher_approved", "shared": True,
           "can_edit": False, "can_review": True, "second_review_available": True,
           "edit_note": "公開・共有の設定は著者だけが変えられます。"}
    text = "\n".join(sx.explanation_card_lines(1, exp))
    assert "［もう一人の査読者として査読する］" in text and "共有OFF" not in text
    assert "公開・共有の設定は著者だけが変えられます。" in text


def test_component_context_superseded_and_version_note():
    gone = {"available": False, "note": "この部品は解析のやり直しで置き換わりました。対応する…"}
    assert project_observation("learning.component.context", 200, gone).endswith("対応する…")
    remapped = {"version_note": "この部品は解析のやり直しで置き換わりました。出典の重なる…",
                "instance": {"component": {"label": "部品"}}}
    text = project_observation("learning.component.context", 200, remapped)
    assert text.index("出典の重なる") < text.index("この論文での位置づけ")


# --- 面 D（studio）--------------------------------------------------------------------------------
def test_owner_only_audio_and_appendix_stale_lines():
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch.update({"studio_course_id": "c", "studio_loaded": True,
                      "studio_structure": {"can_save": False, "audio_generate_notice": "音声生成は所有者だけ"}})
    assert stx.audio_readiness_state(s) == "owner_only"
    lines = stx._readiness_lines("owner_only", s.scratch["studio_structure"])
    # 第 8 波（IK-1071）: 押せない「→ 所有者に依頼する」は無くなった（audio_request が無ければ理由の文だけ）
    assert "  音声生成は所有者だけ" in lines and "所有者に依頼する" not in "\n".join(lines)
    st = {"appendix_stale_topics": [{"id": "t1", "title": "状態方程式"}],
          "appendix_stale_course_notice": "次のトピックの末尾の付録は…", "appendix_reattach_all_notice": "所有者だけ"}
    out = stx.appendix_stale_lines(st)
    # 第 7 波（IK-1054）: 題名は文の括弧に連結せず、折りたたみの一覧に 1 行 1 題名（test_projection_sync_wave7 も参照）
    assert out[0] == "次のトピックの末尾の付録は…"
    assert "  - 状態方程式" in out
    assert "［付録をまとめて付け直す］（押せない）" in out
    assert out[-1] == "所有者だけ"
    assert stx.REATTACH_ALL_BUTTON in _js("admin-lecture-studio.js")
    assert "admin.lecture_studio.reattach_appendix_all" in ACTIONS_BY_ID
    assert "admin.lecture_studio.reattach_appendix_all" in stx.HANDLERS
