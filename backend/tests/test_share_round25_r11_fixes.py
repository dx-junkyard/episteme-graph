"""第 25 周 第 11 波 面 A（share）の是正 — IK-1151〜IK-1156 と IK-1123 の残り。

DB も LLM も使わない（純関数・monkeypatch・ソースの契約）。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

ADMIN_JS = (ROOT / "frontend/public/js/admin.js").read_text(encoding="utf-8")
ADMIN_HTML = (ROOT / "frontend/public/admin.html").read_text(encoding="utf-8")


class _S:
    def close(self):
        pass


# ===========================================================================
# IK-1151 — 関係の一覧の 2 行目以降で相手・向き・札が取り違わない（第 10 波の退行）
# ===========================================================================


class TestDescribeRelationsMultipleRows:
    def _describe(self, monkeypatch, rels, *, viewer_user_id="u1"):
        from core import atlas_review_display as ard
        from routes import library as lib

        monkeypatch.setattr(ard, "fetch_actor_labels", lambda session, ids: {})
        monkeypatch.setattr(lib, "get_session", lambda: _S())
        lib._describe_relations(rels, "e1", viewer_user_id=viewer_user_id)
        return rels

    def test_every_row_uses_the_viewed_entry(self, monkeypatch):
        rels = [
            {"kind": "broader", "status": "confirmed", "decided_by": "u1", "review_note": "",
             "subject_entry_id": "e1", "object_entry_id": "e2",
             "subject_name": "TOV residual", "object_name": "TOV方程式"},
            {"kind": "related", "status": "confirmed", "decided_by": "u2", "review_note": "",
             "subject_entry_id": "e1", "object_entry_id": "e3",
             "subject_name": "TOV residual", "object_name": "状態方程式"},
            {"kind": "broader", "status": "candidate",
             "subject_entry_id": "e4", "object_entry_id": "e1",
             "subject_name": "下位の概念", "object_name": "TOV residual"},
        ]
        self._describe(monkeypatch, rels)
        assert [r["counterpart_name"] for r in rels] == ["TOV方程式", "状態方程式", "下位の概念"]
        assert rels[0]["direction_line"].startswith("「TOV方程式」がこの概念の上位です")
        assert rels[1]["direction_line"] == "この概念と「状態方程式」は関連します（向きはありません）。"
        assert rels[2]["kind_label"] == "下位"
        assert rels[2]["direction_line"].startswith("「下位の概念」はこの概念の下位です")
        # 2 行目以降でも理由の書き足しの判定は閲覧者のユーザー ID で行う。
        assert [bool(r.get("can_add_decision_reason")) for r in rels] == [True, False, False]

    def test_viewer_user_id_never_rebinds_viewer(self):
        from routes import library as lib

        src = inspect.getsource(lib._describe_relations)
        assert "viewer = str(viewer_user_id" not in src
        assert "viewer_uid" in src


# ===========================================================================
# IK-1152 — 記号の結び付きの確定の知らせは記号の届き先で言う
# ===========================================================================


class TestSymbolConfirmFact:
    def test_symbol_wording(self):
        from core.deliberation import identity_links as il
        from core.label_vocab import SYMBOL_LINK_CONFIRMED_FACT

        fact = il.decision_outcome_fact("confirmed", "〔確定〕 確定: 岡村", instance_element_type="symbol")
        assert fact == SYMBOL_LINK_CONFIRMED_FACT.format(line="〔確定〕 確定: 岡村")
        assert "記号の吹き出し" in fact and "共通部品として" not in fact

    def test_part_wording_unchanged(self):
        from core.deliberation import identity_links as il

        fact = il.decision_outcome_fact("confirmed", "x", instance_element_type="theory_component")
        assert "共通部品として" in fact
        assert il.decision_outcome_fact("confirmed", "x") == fact

    def test_route_passes_element_type(self):
        from routes import deliberation as d

        src = inspect.getsource(d._decide_identity_link)
        assert "instance_element_type=existing.get(\"instance_element_type\")" in src


# ===========================================================================
# IK-1153 — 「現れませんでした」と判断済みの一致を言い分ける
# ===========================================================================


class TestSymbolDeriveFact:
    def test_none(self):
        from routes import library as lib

        assert lib._symbol_derive_fact(0, []) == lib.SYMBOL_CANDIDATES_NONE_FACT

    def test_created(self):
        from routes import library as lib

        assert lib._symbol_derive_fact(1, [{"symbol": "L_TOV", "status": "revoked"}]) == lib.SYMBOL_CANDIDATES_CREATED_FACT

    def test_decided_matches_named(self):
        from routes import library as lib

        fact = lib._symbol_derive_fact(0, [
            {"symbol": "L_TOV", "status": "revoked"},
            {"symbol": "L_TOV", "status": "revoked"},
            {"symbol": "f_TOV", "status": "rejected"},
        ])
        assert fact.startswith("判断済みの一致: L_TOV（取り消し済み）・f_TOV（見送り）。")
        assert "現れませんでした" not in fact
        assert "［候補に戻す］" in fact

    def test_existing_candidates(self):
        from routes import library as lib

        fact = lib._symbol_derive_fact(0, [{"symbol": "r", "status": "candidate"}])
        assert "r（候補）" in fact and "現れませんでした" not in fact

    def test_derive_reports_existing_outcomes(self):
        from core.library import symbol_concepts as sc

        src = inspect.getsource(sc.derive_symbol_concept_candidates)
        assert "existing_matches" in src
        assert set(sc._EXISTING_OUTCOME_STATUS.values()) == {"candidate", "confirmed", "rejected", "revoked"}


# ===========================================================================
# IK-1154 — 支持の付け直し・同じ引用の繰り返しを言い分け、引用の行を二重にしない
# ===========================================================================


class TestRepeatEndorseAndCite:
    def test_endorsement_outcome_fact(self):
        from routes import theory_components as tc

        assert tc.endorsement_outcome_fact(True, None, "endorsed") == ""
        again = tc.endorsement_outcome_fact(False, False, "provisional")
        assert "既に支持しています" in again and "暫定の支持" in again and "人数は変わりません" in again
        restored = tc.endorsement_outcome_fact(False, True, "strong")
        assert restored.startswith("取り消していた支持を付け直しました") and "強い支持" in restored

    def test_cite_checks_existing_row_before_insert(self):
        from routes import theory_components as tc

        src = inspect.getsource(tc.cite_explanation)
        assert "IS NOT DISTINCT FROM :intent" in src
        assert "CITATION_ALREADY_RECORDED_FACT" in src
        assert src.index("SELECT id FROM component_citations") < src.index("_insert_citation(")
        assert "DELETE" not in src

    def test_citation_list_collapses_duplicates(self, monkeypatch):
        from routes import theory_components as tc

        rows = [
            ("x1", "宇宙論入門", "uses", "堀", "2026-10-30T01:00:00", "c1", "u1"),
            ("x1", "宇宙論入門", "uses", "堀", "2026-10-31T01:00:00", "c1", "u1"),
            ("x1", "宇宙論入門", "extends", "堀", "2026-10-31T02:00:00", "c1", "u1"),
        ]

        class _Res:
            def fetchall(self):
                return rows

        class _Sess(_S):
            def execute(self, *a, **k):
                return _Res()

        monkeypatch.setattr(tc, "_pg_session", lambda: _Sess())
        out = tc._citations_for_explanations(["x1"])["x1"]
        assert len(out) == 2


# ===========================================================================
# IK-1123 の残り — 取り消しの行も印の無い形
# ===========================================================================


class TestRevokedRowMarkFree:
    def test_revoke_line_without_mark(self):
        from core.deliberation import identity_links as il

        line = il.revoke_line("西川", "2026-10-27", "岡村", "2026-10-26", with_mark=False)
        assert line == "取り消し: 西川・2026-10-27（元の確定: 岡村・2026-10-26）"
        assert il.revoke_line("西川", "2026-10-27", "岡村", "2026-10-26").startswith("〔取り消し済み〕 ")

    def test_describe_actors_revoked(self, monkeypatch):
        from core import atlas_review_display as ard
        from core.deliberation import identity_links as il

        monkeypatch.setattr(ard, "fetch_actor_labels", lambda session, ids: {"u1": "岡村", "u2": "西川"})
        monkeypatch.setattr(il, "get_session", lambda: _S())
        out = il.describe_actors({
            "status": "revoked", "decided_by": "u1", "decided_at": "2026-10-26",
            "revoked_by": "u2", "revoked_at": "2026-10-27",
        })
        assert "〔" not in out["decision_actor_line"]
        assert out["revoke_line"].startswith("〔取り消し済み〕")

    def test_admin_row_prefers_mark_free(self):
        i = ADMIN_JS.index("function _libraryBoundLinksHtml")
        body = ADMIN_JS[i:i + 4000]
        assert "link.decision_actor_line || link.revoke_line" in body


# ===========================================================================
# IK-1155 — 「（原文）」は逐語にだけ・言い換えには別の印
# ===========================================================================


class TestOriginalMarkByOrigin:
    def test_vocab(self):
        from core import label_vocab as lv

        assert lv.original_text_mark_for_origin("atomic_rewrite") == lv.REWRITTEN_TEXT_MARK
        assert lv.original_text_mark_for_origin("equation_synthesis") == lv.SYNTHESIZED_TEXT_MARK
        assert lv.original_text_mark_for_origin("span") == lv.ORIGINAL_TEXT_MARK
        assert lv.original_text_mark_for_origin("") == lv.ORIGINAL_TEXT_MARK

    def test_infer_origin(self):
        from core.schema import infer_claim_origin

        assert infer_claim_origin({"origin": "atomic_rewrite"}) == "atomic_rewrite"
        assert infer_claim_origin({"claim_id": "c1", "parent_claim_id": "c0"}) == "atomic_rewrite"
        assert infer_claim_origin({"claim_id": "synth_claim_3"}) == "equation_synthesis"
        assert infer_claim_origin({"claim_id": "c1"}) == ""

    def test_evidence_item_mark_follows_link_origin(self):
        from core.course_content_builder import build_topic_evidence_items
        from core.label_vocab import ORIGINAL_TEXT_MARK, REWRITTEN_TEXT_MARK

        topic = {"evidence_links": [
            {"kind": "claim", "target_id": "c-rw", "summary": "The field is bow-like.", "origin": "atomic_rewrite"},
            {"kind": "claim", "target_id": "c-sp", "summary": "The field curves near the head."},
        ]}
        items = {i["id"]: i for i in build_topic_evidence_items(topic)}
        assert items["c-rw"]["title_original_mark"] == REWRITTEN_TEXT_MARK
        assert items["c-sp"]["title_original_mark"] == ORIGINAL_TEXT_MARK
        assert all("_claim_origin" not in i for i in items.values())

    def test_component_context_claims(self):
        from core import component_context as cc
        from core.label_vocab import REWRITTEN_TEXT_MARK

        out = cc._build_claims(
            {"linked_claim_ids": ["c2"]},
            {"c2": {"claim_id": "c2", "parent_claim_id": "c1", "text": "The magnetic field is bow-like."}},
        )
        assert out[0]["original_mark"] == REWRITTEN_TEXT_MARK

    def test_delivery_routes_fill_origin_for_old_snapshots(self):
        from routes import lecture, learning

        assert "topic_with_claim_origins(topic)" in inspect.getsource(learning.get_topic_material)
        assert "topic_with_claim_origins(" in inspect.getsource(lecture)

    def test_topic_with_claim_origins_without_uuid_skips_db(self, monkeypatch):
        from core import course_content_builder as ccb

        def _boom():
            raise AssertionError("DB must not be touched")

        monkeypatch.setattr(ccb, "_pg_session", _boom)
        topic = {"evidence_links": [{"kind": "claim", "target_id": "blk:c1"}]}
        assert ccb.topic_with_claim_origins(topic) is topic


# ===========================================================================
# IK-1156 — 埋め込みを囲むバッククォートを読み時で外す
# ===========================================================================


class TestBacktickedEmbeds:
    def test_unwrap(self):
        from core.course_content_builder import unwrap_backticked_embeds as u

        assert u("前 `![[claim:f619b79c-aa]]` 後") == "前 ![[claim:f619b79c-aa]] 後"
        assert u("``![[component:c1]]``") == "![[component:c1]]"
        assert u("`code` は残す") == "`code` は残す"
        assert u("") == ""

    def test_read_path(self):
        from core.lecture import topic_student_material

        topic = {"student_material": {"source_text": "磁場は `![[claim:abc]]` のように曲がる。"}}
        assert topic_student_material(topic) == "磁場は ![[claim:abc]] のように曲がる。"

    def test_prompts_forbid_backticks(self):
        from core import course_content_builder as ccb
        from routes.lecture_studio import topics

        rule = "本文に書く埋め込みはバッククォート（`）で囲まない"
        assert rule in ccb._COURSE_CONTENT_DRAFT_PROMPT
        assert rule in inspect.getsource(topics)


def test_cache_buster_bumped():
    assert "share11" in ADMIN_HTML.split("admin.js?v=")[1].split('"')[0]
