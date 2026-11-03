"""第 25 周 面 A（share）第 7 波の是正（2026-10-28）。

- IK-1051 / IK-1012 再解析より前に凍結したコースの ⚓ を、出典の重なりで 1 対 1 に決まるときだけ
  読み時に後継へつなぐ（オーナー判断）。0 件は「見つからない」と言い、所有者に凍結し直しの案内。
  取り残された説明は読み取り専用で開ける。
- IK-1050 判断した後の行（リンクの見送りは理由必須・札の訳語・見送った概念の記録）
- IK-1052 記号 → 概念の候補の粒度（その記号の節・記号名の一部の別名・同じ論文の別の版）
- IK-0953 取り消しの行の日時と元の確定 / IK-1023 査読者の名前・日付 / IK-1024 足し直しの文
- IK-0681 学生の記号の吹き出しに TeX の label キーを出さない

流儀は既存の ``test_share_round25_*``（実 app + TestClient + monkeypatch / 静的検査 / 純関数）。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

ADMIN_JS = (ROOT / "frontend/public/js/admin.js").read_text(encoding="utf-8")
DELIB_JS = (ROOT / "frontend/public/js/deliberation.js").read_text(encoding="utf-8")
STUDIO_JS = (ROOT / "frontend/public/js/admin-lecture-studio.js").read_text(encoding="utf-8")
ADMIN_HTML = (ROOT / "frontend/public/admin.html").read_text(encoding="utf-8")

try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

_TEACHER_ID = "99999999-9999-9999-9999-999999999999"
_LINK_ID = "22222222-2222-2222-2222-222222222222"


def _function(src: str, name: str) -> str:
    start = src.index("function " + name + "(")
    return src[start:src.index("\n  function ", start + 10)]


# ===========================================================================
# IK-1051 — 読み時の付け替え（純関数の部分と分岐）
# ===========================================================================


class _ScriptedSession:
    """SQL の断片で返す行を切り替える最小セッション（読み時の付け替えの検査用）。"""

    def __init__(self, *, head, olds, news):
        self.head, self.olds, self.news = head, olds, news

    def execute(self, statement, params=None):
        sql = str(statement)

        class _R:
            def __init__(self, rows):
                self.rows = rows

            def fetchone(self):
                return self.rows[0] if self.rows else None

            def fetchall(self):
                return self.rows

        if "superseded_at IS NOT NULL" in sql and "superseded_by_run_id::text, superseded_at" in sql:
            return _R([self.head] if self.head else [])
        if "superseded_by_run_id::text = :run" in sql:
            return _R([(o,) for o in self.olds])
        if "produced_by_run_id::text = :run" in sql:
            return _R([(n,) for n in self.news])
        return _R([])


class TestReadTimeSuccessor:
    def _run(self, monkeypatch, blocks, *, olds=("old-a",), news=("new-1",), live=lambda s, c: c):
        from core.knowledge_objects import remap

        monkeypatch.setattr(remap, "component_source_blocks", lambda s, ids: {i: blocks.get(i, set()) for i in ids})
        monkeypatch.setattr(remap, "successor_component_id", live)
        session = _ScriptedSession(head=("doc", "run-1", "2026-10-20T00:00:00+00:00"), olds=list(olds), news=list(news))
        return remap.read_time_superseded_successor(session, "old-a")

    def test_one_to_one_overlap_connects(self, monkeypatch):
        from core.knowledge_objects import remap

        out = self._run(monkeypatch, {"old-a": {"b1", "b2"}, "new-1": {"b1", "b2"}})
        assert out == ("new-1", remap.READ_TIME_MATCHED)

    def test_no_overlap_is_not_found(self, monkeypatch):
        from core.knowledge_objects import remap

        out = self._run(monkeypatch, {"old-a": {"b1"}, "new-1": {"b9"}})
        assert out == (None, remap.READ_TIME_NOT_FOUND)

    def test_tie_is_ambiguous(self, monkeypatch):
        from core.knowledge_objects import remap

        out = self._run(
            monkeypatch,
            {"old-a": {"b1", "b2"}, "new-1": {"b1"}, "new-2": {"b2"}},
            news=("new-1", "new-2"),
        )
        assert out == (None, remap.READ_TIME_AMBIGUOUS)

    def test_no_superseded_row_is_not_found(self):
        from core.knowledge_objects import remap

        session = _ScriptedSession(head=None, olds=[], news=[])
        assert remap.read_time_superseded_successor(session, "old-a") == (None, remap.READ_TIME_NOT_FOUND)

    def test_does_not_write(self):
        from core.knowledge_objects import remap

        src = inspect.getsource(remap.read_time_superseded_successor)
        for verb in ("INSERT", "UPDATE", "DELETE"):
            assert verb not in src


class TestFrozenResolutionUsesReadTime:
    def test_read_time_match_is_remapped(self, monkeypatch):
        from core import frozen_component_refs as fcr
        from core.knowledge_objects import remap

        monkeypatch.setattr(remap, "superseded_component_by_agent_id", lambda s, **kw: ("uuid-old", "superseded"))
        monkeypatch.setattr(remap, "successor_component_id", lambda s, cid: None)
        monkeypatch.setattr(remap, "read_time_superseded_successor", lambda s, cid: ("uuid-new", "matched"))
        out = fcr.resolve_frozen_component(None, component_id="comp_003",
                                           frozen={"db_id": "", "label": "TOV network map"},
                                           document_ids=["doc-1"])
        assert out == {"status": "remapped", "id": "uuid-new", "read_time": True}

    def test_learner_note_distinguishes_zero_from_ambiguous(self):
        from routes import learning
        from core.knowledge_objects import remap
        from core import label_vocab

        assert learning._superseded_learner_note({"reason": "not_found"}) == remap.SUPERSEDED_COMPONENT_NOT_FOUND_LEARNER_NOTE
        assert learning._superseded_learner_note({"reason": "ambiguous"}) == remap.SUPERSEDED_COMPONENT_LEARNER_NOTE
        assert "見つから" in remap.SUPERSEDED_COMPONENT_NOT_FOUND_LEARNER_NOTE
        assert "一つに決まらない" not in remap.SUPERSEDED_COMPONENT_NOT_FOUND_LEARNER_NOTE
        line = label_vocab.frozen_component_superseded_line("TOV network map", "not_found")
        assert "『TOV network map』" in line and "見つかりません" in line
        assert "後継が決まっていません" in label_vocab.frozen_component_superseded_line("X", "ambiguous")

    def test_payload_carries_the_reason_line(self):
        from routes import learning

        out = learning._superseded_component_payload(
            {"frozen_label": "EOS network map", "reason": "not_found"}, "note"
        )
        assert out["available"] is False
        assert "見つかりません" in out["frozen_label_line"]


class TestOwnerNotice:
    def test_notice_lists_names_without_counts_and_never_refreezes(self):
        from core import frozen_component_refs as fcr

        text = fcr.frozen_anchor_stale_notice(["TOV network map", "EOS network map"], can_save=True)
        assert "「TOV network map」・「EOS network map」" in text
        assert "自動では凍結し直しません" in text
        assert "［コース内容生成］" in text
        assert fcr.frozen_anchor_stale_notice([], can_save=True) == ""
        assert fcr.FROZEN_ANCHOR_STALE_OWNER_ONLY in fcr.frozen_anchor_stale_notice(["A"], can_save=False)
        many = fcr.frozen_anchor_stale_notice([f"n{i}" for i in range(8)], can_save=True)
        assert "ほか" in many

    def test_stale_labels_use_the_learner_resolution(self, monkeypatch):
        from core import frozen_component_refs as fcr

        course = {"topics": [{"id": "t1", "content_blocks": [{"type": "components", "items": [
            {"component_id": "comp_001", "document_id": "doc", "label": "TOV network map"},
            {"component_id": "comp_002", "document_id": "doc", "label": "EOS network map"},
        ]}]}]}
        states = {"comp_001": {"status": "superseded", "id": "x", "reason": "not_found"},
                  "comp_002": {"status": "remapped", "id": "y"}}
        monkeypatch.setattr(fcr, "resolve_frozen_component", lambda s, **kw: states[kw["component_id"]])
        assert fcr.stale_frozen_component_labels(None, course, ["doc"]) == ["TOV network map"]

    def test_studio_route_and_ui_carry_the_notice(self):
        from routes.lecture_studio import topics

        assert "_frozen_anchor_stale_facts(" in inspect.getsource(topics.get_lecture_studio_course_structure)
        assert "s.frozen_anchor_notice" in STUDIO_JS
        # G層 To-Do は作らない（オーナー判断）。
        from core.admin_assistant import next_steps

        assert "frozen_anchor" not in inspect.getsource(next_steps)


# ===========================================================================
# IK-1012 — 取り残された説明を読み取り専用で開く
# ===========================================================================


class TestStrandedExplanationsReadOnly:
    def test_route_returns_read_only_items_and_rewrite_notes(self, monkeypatch):
        from routes import theory_components as tc
        from core.knowledge_objects import remap

        class _Comp:
            source_scope = type("S", (), {"document_id": "doc-1"})()

        class _S:
            def close(self):
                pass

            def rollback(self):
                pass

        monkeypatch.setattr(tc, "_get_component", lambda cid: _Comp())
        monkeypatch.setattr(tc, "_ensure_component_viewable", lambda c, u: None)
        monkeypatch.setattr(tc, "_pg_session", lambda: _S())
        monkeypatch.setattr(remap, "stranded_component_explanation_records", lambda s, d: [
            {"component_name": "TOV network map", "kind": "personal", "title": "TOV 方程式", "body": "本文",
             "author_id": _TEACHER_ID, "author_profile_name": "西川 智也", "review_status": "teacher_approved",
             "created_on": "2026-10-20"},
            {"component_name": "TOV network map", "kind": "personal", "title": "別", "body": "b",
             "author_id": "other", "author_profile_name": "岡村 千尋", "review_status": "", "created_on": ""},
            {"component_name": "EOS network map", "kind": "standard", "title": "", "body": "要約",
             "author_id": "", "author_profile_name": "", "review_status": "", "created_on": ""},
        ])
        out = tc.list_stranded_explanations("c1", {"id": _TEACHER_ID, "role": "teacher"})
        assert out["facts"] == [remap.STRANDED_EXPLANATIONS_READONLY_NOTE]
        assert [i["rewrite_note"] for i in out["items"]] == [
            remap.STRANDED_EXPLANATION_REWRITE_OWN,
            remap.STRANDED_EXPLANATION_REWRITE_OTHERS,
            remap.STRANDED_EXPLANATION_STANDARD,
        ]
        assert all(i["read_only"] for i in out["items"])
        assert "再解析前の部品「TOV network map」" in out["items"][0]["origin_line"]
        assert "西川 智也" in out["items"][0]["origin_line"]
        for item in out["items"]:
            assert "author_id" not in item and "id" not in item

    def test_records_select_never_returns_uuids(self):
        from core.knowledge_objects import remap

        src = inspect.getsource(remap.stranded_component_explanation_records)
        assert "e.id" not in src.split("FROM component_explanations")[0]
        assert "DELETE" not in src and "UPDATE" not in src

    def test_studio_opens_the_list_without_action_buttons(self):
        body = _function(STUDIO_JS, "lsBindStrandedExplanations")
        assert "/stranded-explanations" in body
        assert "data-exp-action" not in body
        assert 'data-ui-anchor="lecture-studio.explanation-stranded-open"' in STUDIO_JS


# ===========================================================================
# IK-1050 — 判断した後の行
# ===========================================================================


class TestDecidedRows:
    def test_decide_requires_reason_for_rejected(self):
        from core.deliberation import identity_links

        with pytest.raises(ValueError):
            identity_links.decide(_LINK_ID, status="rejected", decided_by=_TEACHER_ID, reason="  ")

    def test_status_labels_cover_all_states(self):
        from core.deliberation import identity_links
        from core.deliberation.schema import IDENTITY_LINK_STATUSES

        assert set(identity_links.IDENTITY_LINK_STATUS_LABELS) >= set(IDENTITY_LINK_STATUSES)
        assert identity_links.IDENTITY_LINK_STATUS_LABELS["rejected"] == "見送り"

    def test_reason_line_and_revoke_line(self):
        from core.deliberation import identity_links

        assert identity_links.decision_reason_line("rejected", "粒度が違う") == "見送りの理由: 粒度が違う"
        assert "記録なし" in identity_links.decision_reason_line("rejected", "")
        assert identity_links.decision_reason_line("confirmed", "x") == ""
        line = identity_links.revoke_line("西川 智也", "2026-10-27T10:00:00", "岡村 千尋", "2026-10-26T09:00:00")
        assert line == "〔取り消し済み〕 取り消し: 西川 智也・2026-10-27（元の確定: 岡村 千尋・2026-10-26）"

    def test_describe_actors_attaches_lines(self, monkeypatch):
        from core.deliberation import identity_links
        import core.atlas_review_display as ard

        monkeypatch.setattr(ard, "fetch_actor_labels", lambda s, ids: {"u1": "岡村 千尋", "u2": "西川 智也"})

        class _S:
            pass

        revoked = identity_links.describe_actors({
            "status": "revoked", "created_by": "u1", "decided_by": "u1", "decided_at": "2026-10-26T00:00:00",
            "revoked_by": "u2", "revoked_at": "2026-10-27T00:00:00", "revoke_reason": "粒度違い",
        }, session=_S())
        assert revoked["revoke_line"].startswith("〔取り消し済み〕 取り消し: 西川 智也・2026-10-27")
        assert "元の確定: 岡村 千尋・2026-10-26" in revoked["revoke_line"]
        assert revoked["status_label"] == "確定を取り消し"
        rejected = identity_links.describe_actors({
            "status": "rejected", "decided_by": "u2", "decided_at": "2026-10-27T00:00:00", "revoke_reason": "別物",
        }, session=_S())
        assert rejected["decision_line"] == "〔見送り〕 見送り: 西川 智也・2026-10-27"
        assert rejected["decision_reason_line"] == "見送りの理由: 別物"

    def test_dismissed_entry_decision_record(self):
        from routes import library as lib

        out = lib._dismissed_entry_decision(
            {"decided_by": "u1", "decided_at": "2026-10-27T01:00:00", "review_note": "既存と同じ"},
            {"u1": "西川 智也"},
        )
        assert out["decision_line"] == "〔見送り〕 見送り: 西川 智也・2026-10-27"
        assert out["decision_reason_line"] == "見送りの理由: 既存と同じ"
        assert "［候補に戻す］" in out["restore_note"]

    def test_partner_replaced_reason_survives_the_decision(self):
        from routes import library as lib

        items = [{"status": "rejected", "reason": "別の論文の記述と本文の意味が近いと測定されました。",
                  "instance": {"element_type": "theory_component"}, "_instance_label": "x",
                  "_format_label": "", "document_title": "t", "_decided_by": "", "_decided_at": None,
                  "_revoked_by": "", "_revoked_at": None}]
        lib._attach_identity_pair_facts(items, entry={"name": "E"}, unresolved=True)
        assert items[0]["reason"] == lib._identity_links.PARTNER_REPLACED_REASON
        assert items[0]["recorded_reason"].startswith("別の論文")

    def test_ui_prompts_reason_and_hides_buttons_on_decided(self):
        render = _function(ADMIN_JS, "renderLibraryIdentityCandidates")
        assert 'entry.review_status === "candidate"' in render
        assert "entry.decision_reason_line" in render
        assert "_libraryLinkStatusChipHtml(link)" in render
        assert "_libraryPromptDismissReason(" in render
        decide = _function(ADMIN_JS, "_libraryDecideIdentityLink")
        assert "JSON.stringify({ reason: reason })" in decide
        assert 'action === "reject"' in _function(DELIB_JS, "_decideIdentityLink")
        assert "share7" in ADMIN_HTML


@pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")
class TestRejectRoute:
    @pytest.fixture
    def client_and_teacher(self):
        from fastapi.testclient import TestClient
        from main import app
        from dependencies import ROLE_TEACHER, _create_token

        return TestClient(app), _create_token(_TEACHER_ID, "tea", "tea@x", ROLE_TEACHER)

    def test_reject_without_reason_is_422(self, client_and_teacher):
        client, token = client_and_teacher
        url = f"/api/admin/deliberation/identity-links/{_LINK_ID}/reject"
        headers = {"Authorization": "Bearer " + token}
        assert client.post(url, headers=headers).status_code == 422
        res = client.post(url, headers=headers, json={"reason": "   "})
        assert res.status_code == 422
        assert "理由" in res.json()["detail"]

    def test_reject_with_reason_reaches_decide(self, client_and_teacher, monkeypatch):
        from routes import deliberation as d

        seen = {}
        monkeypatch.setattr(d.identity_links, "get_by_id", lambda lid: {"id": lid, "status": "candidate",
                                                                         "instance_document_id": "doc"})
        monkeypatch.setattr(d, "_ensure_document_editable", lambda doc, user: None)

        def _decide(link_id, *, status, decided_by, reason=""):
            seen["reason"] = reason
            return {"id": link_id, "status": status, "decided_by": decided_by, "decided_at": None,
                    "revoke_reason": reason, "shared_part_id": ""}

        monkeypatch.setattr(d.identity_links, "decide", _decide)
        monkeypatch.setattr(d, "record_review_event", lambda *a, **k: None)
        monkeypatch.setattr(d, "_identity_link_response", lambda link: dict(link))
        client, token = client_and_teacher
        res = client.post(f"/api/admin/deliberation/identity-links/{_LINK_ID}/reject",
                          headers={"Authorization": "Bearer " + token}, json={"reason": "粒度が違う"})
        assert res.status_code == 200
        assert seen["reason"] == "粒度が違う"


# ===========================================================================
# IK-1052 — 記号 → 概念の候補の粒度
# ===========================================================================


class TestSymbolCandidateGranularity:
    SHARED = "The structure network N TOV maps the EOS feature vector F EOS = {P (kρ 0 )} to stellar profiles"
    ENTRY = {"id": "e", "name": "TOV residual loss at collocation points",
             "surfaces": ["TOV residual loss at collocation points", "TOV"]}

    def test_alias_inside_another_symbol_name_does_not_match(self):
        from core.library import symbol_concepts as sc

        own = sc._symbol_text_surfaces(r"F_{\mathrm{EOS}}")
        others = {"N_TOV": sc._symbol_text_surfaces("N_{TOV}")}
        assert sc._matched_entries([self.SHARED], [self.ENTRY], own_forms=own, other_forms=others) == []
        own2 = sc._symbol_text_surfaces("N_{TOV}")
        assert sc._matched_entries([self.SHARED], [self.ENTRY], own_forms=own2,
                                   other_forms={"F_EOS": own}) == []

    def test_clause_is_limited_to_the_symbol(self):
        from core.library import symbol_concepts as sc

        own = sc._symbol_text_surfaces("F_{EOS}")
        clause, others = sc._symbol_clause(self.SHARED, own, {"N_TOV": sc._symbol_text_surfaces("N_TOV")})
        assert "network" not in clause and "EOS feature vector" in clause
        assert "F EOS" not in clause
        assert others == ["N_TOV"]

    def test_full_name_still_matches_the_symbol_it_defines(self):
        from core.library import symbol_concepts as sc

        text = "We define the TOV residual loss L TOV at collocation points"
        hits = sc._matched_entries([text], [self.ENTRY], own_forms=sc._symbol_text_surfaces("L_{TOV}"),
                                   other_forms={"N_TOV": sc._symbol_text_surfaces("N_TOV")})
        assert [h[1] for h in hits] == ["TOV residual loss at collocation points"]

    def test_single_letter_symbol_unchanged(self):
        from core.library import symbol_concepts as sc

        entry = {"id": "e1", "name": "stellar radius", "surfaces": ["stellar radius"]}
        hits = sc._matched_entries(["where r is the stellar radius"], [entry],
                                   own_forms=sc._symbol_text_surfaces("r"), other_forms={})
        assert [h[0]["id"] for h in hits] == ["e1"]

    def test_same_paper_carry_over_creates_candidate_on_the_other_version(self, monkeypatch):
        from core.library import symbol_concepts as sc
        from core.deliberation import identity_links

        created = []

        def _create(ref, entry_id, **kw):
            created.append((ref.document_id, ref.element_id, entry_id, kw["reason"]))
            return {"outcome": identity_links.LINK_OUTCOME_CREATED}

        monkeypatch.setattr(sc._identity_links, "create_candidate", _create)

        class _S:
            def execute(self, statement, params=None):
                sql = str(statement)

                class _R:
                    def __init__(self, rows):
                        self.rows = rows

                    def fetchall(self):
                        return self.rows

                if "FROM documents" in sql:
                    return _R([("d-tex", "PINN TOV", "arXiv-2605.31198v1.tar.gz", "", ""),
                               ("d-pdf", "PINN TOV", "2605.31198v1.pdf", "", "")])
                if "FROM element_identity_links" in sql:
                    return _R([("d-pdf", "sym_7", "e", "confirmed")])
                return _R([])

        rows = [("d-tex", "sym_3", r"F_{\!EOS}", []), ("d-pdf", "sym_7", "F_{EOS}", [])]
        stats = {"links_created": 0, "truncated": False}
        sc._carry_over_same_paper_versions(_S(), rows, ["d-pdf", "d-tex"],
                                           [{"id": "e", "name": "EOS feature vector", "surfaces": []}], stats, 20)
        assert [(c[0], c[1], c[2]) for c in created] == [("d-tex", "sym_3", "e")]
        assert "同じ論文の別の版" in created[0][3] and "確定済み" in created[0][3]
        assert stats["links_created"] == 1


# ===========================================================================
# IK-1023 / IK-1024 / IK-0681
# ===========================================================================


class TestRemainders:
    def test_explanation_card_shows_review_line(self):
        from routes import theory_components as tc

        assert "review_line" in tc.ExplanationOut.model_fields
        assert "exp.review_line" in _function(STUDIO_JS, "lsRenderExplanationCard")
        src = inspect.getsource(tc._explanation_review_lines)
        assert "STANDARD_REVIEWERS_SQL" in src and "profile_name" in src

    def test_label_readd_points_to_restore(self):
        from routes import library as lib

        for kind in ("alternate", "hidden"):
            text = lib.LABEL_OUTCOME_FACTS[(kind, "dismissed")]
            assert "［見送りを取り消す］" in text and "見送り済みのラベル" in text

    def test_symbol_lookup_drops_tex_label_keys(self):
        from core import symbol_lookup as sl

        assert sl._printed_equation_label("eq:tov_residual") == ""
        assert sl._printed_equation_label("12") == "12"
        assert sl._equation_name("eq:tov_residual") == "番号の無い式"
        assert sl._equation_name("3") == "式 (3)"
