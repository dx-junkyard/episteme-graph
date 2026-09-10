"""確定文脈の記帳 — 構造的ガードレール。

正本: ``docs/features/decision_context_design.md``（DC1〜DC4）。
上位の根拠は ``docs/vision.md`` §4 改訂原則1（2026-09-04）。

検査するのは「一括確定が確定文脈**なしに**記帳できない」こと（DC1）と、プリミティブの
純粋性・語彙の固定・提示/適用の分離（DC2）・代替必須（DC3）・来歴申告の隔離（DC4）。
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import decision_context as dc  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
)

CORE_SRC = (BACKEND / "core" / "decision_context.py").read_text(encoding="utf-8")
LANDSCAPE_SRC = (BACKEND / "api" / "routes" / "landscape.py").read_text(encoding="utf-8")
EXPLANATION_SRC = (
    BACKEND / "api" / "routes" / "element_explanations.py"
).read_text(encoding="utf-8")
ATLAS_SRC = (BACKEND / "api" / "routes" / "atlas.py").read_text(encoding="utf-8")
ADMIN_SRC = (BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
DISCOVERY_SRC = (
    BACKEND / "api" / "routes" / "paper_discovery.py"
).read_text(encoding="utf-8")
COMPONENTS_SRC = (
    BACKEND / "api" / "routes" / "theory_components.py"
).read_text(encoding="utf-8")
TOPICS_SRC = (
    BACKEND / "api" / "routes" / "lecture_studio" / "topics.py"
).read_text(encoding="utf-8")
VERSIONING_SUBSCRIPTIONS_SRC = (
    BACKEND / "core" / "versioning" / "subscriptions.py"
).read_text(encoding="utf-8")
RELEASE_JS = (
    ROOT / "frontend" / "public" / "js" / "admin-release-review.js"
).read_text(encoding="utf-8")


class TestCoreIsPure:
    def test_core_does_not_import_web_or_db_or_llm(self):
        assert_source_does_not_import(
            CORE_SRC,
            ["fastapi", "sqlalchemy", "pydantic", "openai"],
            context="core/decision_context.py",
        )

    def test_core_has_no_sql_or_delete(self):
        assert_source_forbids(
            CORE_SRC,
            ["DELETE FROM", "session.execute", "sa_text"],
            context="core/decision_context.py",
        )

    def test_module_docstring_declares_invariants(self):
        doc = dc.__doc__ or ""
        for term in ("DC1", "DC2", "DC3", "DC4"):
            assert term in doc
        assert "test_decision_context_guardrails.py" in doc


class TestVocabulary:
    def test_key_and_basis_constants_are_fixed(self):
        assert dc.DECISION_CONTEXT_KEY == "decision_context"
        assert dc.BASIS_RELEASE_REVIEW_PLACEMENTS == "release_review.placements"
        assert dc.BASIS_EXPLANATION_REVIEW_BULK == "explanation_review.bulk"
        assert dc.BASIS_ATLAS_BINDING_SAVE == "atlas_binding.save"
        # 2026-09-10（是正 A-04 / F-18）— 段階適用の第2波。
        assert dc.BASIS_DISCOVERY_INGEST_BATCH == "discovery.ingest_batch"
        assert dc.BASIS_ATLAS_SKELETON_FREEZE == "atlas_skeleton.freeze"
        assert dc.BASIS_COURSE_VISIBILITY_PUBLISH == "course_visibility.publish"
        assert dc.BASIS_COMPONENT_REVIEW_SINGLE == "component_review.single"
        assert dc.BASIS_CLAIM_REVIEW_SINGLE == "claim_review.single"

    def test_basis_catalog_is_complete_and_follows_the_naming_convention(self):
        """basis の正本はカタログ（件数を文書に書き写さない — 開発規約 §5）。"""
        constants = {
            getattr(dc, name) for name in dir(dc) if name.startswith("BASIS_")
        } - {dc.BASIS_VALUES}
        assert set(dc.BASIS_VALUES) == constants
        assert dc.BASIS_VALUES == tuple(sorted(dc.BASIS_VALUES))
        for value in dc.BASIS_VALUES:
            subject, _, operation = value.partition(".")
            assert subject and operation, value
            assert value == value.lower()

    def test_basis_catalog_is_not_a_validation_gate(self):
        """カタログは検査用（任意の basis を弾かない — 既存呼び出しを壊さない）。"""
        assert dc.build_decision_context(
            basis="not_in_catalog.x",
            presented_ids=["a"],
            applied_ids=["a"],
            alternatives=[dc.ALT_REJECT],
            reopen_path="POST /x",
        )["basis"] == "not_in_catalog.x"

    def test_alternative_vocabulary_is_fixed(self):
        assert dc.ALTERNATIVES == (
            "deselect",
            "dismiss",
            "edit",
            "reconsider",
            "reject",
            "skip_step",
        )
        assert dc.PRESENTED_IDS_MAX == 200

    def test_exports_are_sorted(self):
        assert dc.__all__ == sorted(dc.__all__)


class TestDeclineIsDerived:
    def test_decline_possible_is_not_a_parameter(self):
        """DC3: 「断れなかった」を申告できる口を作らない。"""
        params = inspect.signature(dc.build_decision_context).parameters
        assert "decline_possible" not in params
        # 呼び出し側が上書きできないよう、キーワード専用引数だけで構成する。
        assert all(
            p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values()
        )

    def test_presented_matches_applied_is_not_a_parameter(self):
        """DC2: 一致は導出。呼び出し側が「一致した」と申告できない。"""
        assert "presented_matches_applied" not in inspect.signature(
            dc.build_decision_context
        ).parameters


class TestBulkRoutesRecordContext:
    """DC1: 一括確定の記帳が確定文脈を必ず含む。"""

    def test_release_review_accept_builds_and_attaches_context(self):
        src = extract_function_source(
            LANDSCAPE_SRC, "accept_course_landscape_placements"
        )
        assert "decision_context.build_decision_context(" in src
        assert "decision_context.attach_decision_context(" in src
        assert "BASIS_RELEASE_REVIEW_PLACEMENTS" in src
        # 提示集合はサーバが更新前に取り直す（クライアント申告に依存しない）。
        assert "list_for_documents" in src
        assert "STATUS_INFERRED" in src

    def test_explanation_bulk_review_builds_and_attaches_context(self):
        src = extract_function_source(
            EXPLANATION_SRC, "bulk_review_element_explanations"
        )
        assert "decision_context.build_decision_context(" in src
        assert "decision_context.attach_decision_context(" in src
        assert "BASIS_EXPLANATION_REVIEW_BULK" in src
        # 既存の来歴申告（TT3）の作法は保つ。
        assert "teacher_triage.sort_metadata(" in src
        assert '"bulk": True' in src

    def test_atlas_binding_save_builds_and_attaches_context(self):
        """コース⇄地図バインディングの一括保存（第3経路）。

        リリース前確認ウィザードのステップ1「この対応で次へ」も同じ保存 API を通る
        （フロントはラベルを差し替えるだけ）ので、記帳は必ず確定文脈を伴う。
        """
        src = extract_function_source(ATLAS_SRC, "save_course_atlas_binding")
        assert "decision_context.build_decision_context(" in src
        assert "decision_context.attach_decision_context(" in src
        assert "decision_context.BASIS_ATLAS_BINDING_SAVE" in src
        # 提示集合はサーバが course_data から取り直す（クライアント申告に依存しない）。
        assert "presented_ids=presented_topic_ids" in src
        assert "applied_ids=applied_topic_ids" in src
        # 根拠提示の有無は検証できないので True と申告しない（DC2）。
        assert "evidence_shown=None" in src
        assert "evidence_shown=True" not in src

    def test_atlas_binding_basis_follows_the_naming_convention(self):
        import routes.atlas as atlas_routes

        assert dc.BASIS_ATLAS_BINDING_SAVE == "atlas_binding.save"
        assert atlas_routes._BINDING_REOPEN_PATH.startswith("PUT /api/admin/")
        # 代替が空なら core 側が ValueError を投げる（DC3）。ここでは語彙が
        # ALTERNATIVES の範囲であることだけを確かめる。
        for alt in ("deselect", "skip_step"):
            assert alt in dc.ALTERNATIVES

    def test_client_reported_is_isolated_in_both_routes(self):
        """DC4: 来歴申告は専用引数へ渡す（トップレベルのサーバ導出値に混ぜない）。"""
        for src, fn in (
            (LANDSCAPE_SRC, "accept_course_landscape_placements"),
            (EXPLANATION_SRC, "bulk_review_element_explanations"),
        ):
            fn_src = extract_function_source(src, fn)
            assert "client_reported=" in fn_src


class TestEvidenceShownIsNeverAssertedByTheServer:
    """是正 F7b（2026-09-10・六つのレンズ §4 第1波 #5 / 02_teacher.md 提案7）。

    「根拠が画面に出ていたか」はサーバが検証できない。以前は説明の一括承認が
    ``evidence_shown=True`` をハードコードで断言し、リリース前の確認は
    クライアント申告をそのままサーバ導出値の位置に載せていた。どちらも
    「描画するコードがある」ことしか意味しないので、申告は ``client_reported``
    にだけ隔離する（DC4）。
    """

    def test_no_bulk_route_asserts_evidence_shown_true(self):
        for src, fn in (
            (LANDSCAPE_SRC, "accept_course_landscape_placements"),
            (EXPLANATION_SRC, "bulk_review_element_explanations"),
            (ATLAS_SRC, "save_course_atlas_binding"),
            # 2026-09-10 の第2波（是正 A-04 / F-18）も同じ規律に従う。
            (ATLAS_SRC, "freeze_atlas_skeleton"),
            (DISCOVERY_SRC, "ingest_batch"),
            (ADMIN_SRC, "update_course_visibility"),
            (COMPONENTS_SRC, "_single_review_audit_metadata"),
        ):
            fn_src = extract_function_source(src, fn)
            assert "evidence_shown=None" in fn_src, fn
            assert "evidence_shown=True" not in fn_src, fn

    def test_explanation_bulk_isolates_the_client_evidence_claim(self):
        fn_src = extract_function_source(
            EXPLANATION_SRC, "bulk_review_element_explanations"
        )
        # 申告は専用キーへ（サーバの断言に戻さない）。
        assert "evidence_rendered_ids" in fn_src
        assert 'client_reported["evidence_rendered_ids"]' in fn_src

    def test_release_review_accept_isolates_the_expansion_facts(self):
        fn_src = extract_function_source(
            LANDSCAPE_SRC, "accept_course_landscape_placements"
        )
        assert 'reported["evidence_expanded_placement_ids"]' in fn_src


class TestReleaseReviewFrontend:
    def test_accept_reports_presented_ids_and_expanded_evidence(self):
        accept = RELEASE_JS[
            RELEASE_JS.index("function acceptPlacements") :
            RELEASE_JS.index("function renderPublishStep")
        ]
        assert "presented_placement_ids" in accept
        # 是正 F7b: 固定の `evidence_shown: true` ではなく、実際に開いた行の id を送る。
        assert "evidence_expanded_placement_ids" in accept
        assert "evidence_shown" not in accept

    def test_expansion_facts_come_from_dom_toggle_events_only(self):
        """開いた/開かなかったの2値のみ。滞在時間・回数は測らない（原則5）。"""
        assert 'addEventListener("toggle"' in RELEASE_JS
        assert "state.evidenceExpanded" in RELEASE_JS
        assert "function expandedEvidencePlacementIds" in RELEASE_JS
        for banned in ("dwell", "Date.now()", "setInterval("):
            assert banned not in RELEASE_JS

    def test_evidence_affordance_is_rendered_with_its_anchor(self):
        assert 'data-ui-anchor="release-review.evidence"' in RELEASE_JS
        assert "<details" in RELEASE_JS
        assert "根拠を見る" in RELEASE_JS

    def test_reopen_fact_is_shown(self):
        assert (
            "確認後も、教材管理の「位置づけ（分野マップ）」から個別に再検討・却下へ戻せます。"
            in RELEASE_JS
        )
        # 「次へ」の意味の明示（RR2）は残す。
        assert "確認したものとして記録" in RELEASE_JS

    def test_match_facts_do_not_carry_numbers(self):
        for fact in (
            "表示されていた配置と確認した配置は一致しています",
            "表示と確認した配置に差がありました（画面を再読み込みしてください）",
        ):
            assert fact in RELEASE_JS
            assert "%" not in fact


class TestStagedRoutesRecordContext:
    """段階適用の第2波（2026-09-10・是正 A-04 / F-18 / 六つのレンズ §4 第1波 #6）。

    一括取り込み・骨格の凍結・コースの公開・単発の承認まで DC1 を広げた。適用先の
    正本は ``core/decision_context.py`` の ``BASIS_*``（件数はここに書き写さない —
    :meth:`TestVocabulary.test_basis_catalog_is_complete_and_follows_the_naming_convention`
    がカタログと定数の一致を固定する）。
    """

    def test_discovery_ingest_batch_builds_and_attaches_context(self):
        src = extract_function_source(DISCOVERY_SRC, "ingest_batch")
        assert "decision_context.build_decision_context(" in src
        assert "decision_context.attach_decision_context(" in src
        assert "decision_context.BASIS_DISCOVERY_INGEST_BATCH" in src
        # 提示集合はリクエストの候補集合、適用集合は実際にキューへ積まれた行。
        assert "presented_ids=[item.arxiv_id for item in items]" in src
        assert 'applied_ids=[entry["arxiv_id"] for entry in result["queued"]]' in src
        # 覆す経路は「教材の削除」（キューの retry は取り消しではない — DC2）。
        assert "reopen_path=_INGEST_REOPEN_PATH" in src

    def test_atlas_freeze_builds_and_attaches_context(self):
        src = extract_function_source(ATLAS_SRC, "freeze_atlas_skeleton")
        assert "decision_context.build_decision_context(" in src
        assert "decision_context.attach_decision_context(" in src
        assert "decision_context.BASIS_ATLAS_SKELETON_FREEZE" in src
        # 提示 = プレビューが計算対象にした draft の node / 適用 = 版へ入った node。
        assert "draft.region_ids()" in src and "draft.concept_ids()" in src
        assert "frozen.region_ids()" in src and "frozen.concept_ids()" in src
        # 凍結版は不変（AB3）なので「戻せる status」を書かない。
        assert "reopen_path=_FREEZE_REOPEN_PATH" in src
        assert "reopen_statuses" not in src
        # プレビューで見せた影響そのものは隣接キーに事実として残す。
        assert "impact_removed_node_ids" in src
        assert "impact_affected_course_ids" in src

    def test_atlas_freeze_reopen_path_is_the_next_draft(self):
        import routes.atlas as atlas_routes

        assert atlas_routes._FREEZE_REOPEN_PATH.startswith("POST /api/admin/")
        assert "draft/from-frozen" in atlas_routes._FREEZE_REOPEN_PATH

    def test_course_publish_builds_and_attaches_context(self):
        src = extract_function_source(ADMIN_SRC, "update_course_visibility")
        assert "decision_context.build_decision_context(" in src
        assert "decision_context.attach_decision_context(" in src
        assert "decision_context.BASIS_COURSE_VISIBILITY_PUBLISH" in src
        # 公開（取り消しの効かない開示）だけが確定文脈を伴う。
        assert 'if body.visibility == "public":' in src
        # 非公開へ戻せる（visibility の語彙がそのまま再審の status になる）。
        assert 'reopen_statuses=("group", "private")' in src

    def test_single_component_approval_builds_context(self):
        src = extract_function_source(COMPONENTS_SRC, "approve_theory_component")
        assert "_single_review_audit_metadata(" in src
        assert "decision_context.BASIS_COMPONENT_REVIEW_SINGLE" in src
        # 承認可能性の判定と遷移実体は非改変（audit_metadata は加算のみ）。
        assert "_component_approval_problems(existing)" in src
        assert 'status="teacher_reviewed", review_status="teacher_approved"' in src
        # 承認画面が並置する面（根拠 claim・退避した警告）を id で残す。
        assert "backing_claim_ids" in src
        assert "retained_validation_warning_fields" in src

    def test_single_claim_review_builds_context(self):
        src = extract_function_source(COMPONENTS_SRC, "review_claim")
        assert "_single_review_audit_metadata(" in src
        assert "decision_context.BASIS_CLAIM_REVIEW_SINGLE" in src
        # 却下のときは画面に出ていない `reject` を代替として書かない（DC2）。
        assert 'if review_status == "rejected"' in src

    def test_single_confirmation_keeps_the_match_flag_meaningful(self):
        """単発の確定は presented = applied = そのオブジェクト（DC2 の差の検出を守る）。

        「画面に出ていた根拠」を `presented` に入れると
        `presented_matches_applied` が構造的に常に False になり、差の検出が
        意味の無い定数になる。根拠は隣接キー `grounds` に置く。
        """
        src = extract_function_source(COMPONENTS_SRC, "_single_review_audit_metadata")
        assert "presented_ids=[entity_id]" in src
        assert "applied_ids=[entity_id]" in src
        assert '"grounds": grounds' in src

    def test_single_confirmation_metadata_carries_no_numbers(self):
        import routes.theory_components as tc

        meta = tc._single_review_audit_metadata(
            dc.BASIS_COMPONENT_REVIEW_SINGLE,
            entity_id="c1",
            reopen_path=tc._COMPONENT_REOPEN_PATH,
            reopen_statuses=("rejected",),
            grounds={"backing_claim_ids": ["cl1"]},
            alternatives=(dc.ALT_REJECT, dc.ALT_SKIP_STEP),
        )
        ctx = meta[dc.DECISION_CONTEXT_KEY]
        assert ctx["presented_matches_applied"] is True
        assert ctx["decline_possible"] is True
        assert ctx["evidence_shown"] is None
        assert meta["bulk"] is False
        for banned in ("confidence", "weight", "score"):
            assert banned not in json.dumps(meta, ensure_ascii=False)


class TestNoStateChangeWithoutAudit:
    """原則14（是正 F11）— 記帳の無い状態変更を塞いだ3経路の回帰検出。

    「監査を書かずに状態を変える」経路は、後から再構成しようとしたときに存在しない。
    とくに教材の物理削除は不可逆で、学習者に届いている教材と解析成果をまとめて消す。
    """

    def test_material_deletion_is_audited(self):
        src = extract_function_source(ADMIN_SRC, "delete_material")
        assert "record_review_event(" in src
        assert "AUDIT_ENTITY_MATERIAL" in src
        assert '"deleted"' in src
        # 巻き添えで消えたコースも残す（何が消えたかを後から追えるように）。
        assert "deleted_course_ids" in src
        # 監査は DB 削除の commit 後（ロールバックした削除を「消した」と書かない）。
        assert src.index("session.commit()") < src.index("record_review_event(")
        # 資料本文・タイトルは監査に載せない。
        assert "doc_title" not in src.split("record_review_event(")[1]

    def test_topic_save_is_audited_with_field_names_only(self):
        src = extract_function_source(TOPICS_SRC, "save_lecture_studio_course_topic")
        assert "record_review_event(" in src
        assert "AUDIT_ENTITY_COURSE_TOPIC" in src
        assert '"action": "topic_draft_saved"' in src
        assert "changed_fields" in src
        # 音声キャッシュ無効化という副作用も事実として残す。
        assert "topic_audio_cache_invalidated" in src
        # 本文そのものは載せない（フィールド名の列挙だけ）。
        call = src.split("record_review_event(")[1].split('return {"course_id"')[0]
        for banned in ("source_text", "target[", "body.get("):
            assert banned not in call

    def test_release_adoption_is_audited_in_core(self):
        """版の adopt は core 側（`core/versioning/audit.py`）で記帳済み。

        六つのレンズ F11 はルートファイルの `record_review_event` を grep して
        「記帳が無い」と読んだが、V層は CLAUDE.md が認めた core 直記帳の経路で
        （`versioning.py` の route は `subscriptions.adopt_latest` へ委譲する）
        既に監査している。**route 側に足すと1操作2行になる**ので足さない。
        """
        src = extract_function_source(VERSIONING_SUBSCRIPTIONS_SRC, "adopt_latest")
        assert "audit.record_event(" in src
        assert "schema.AUDIT_SUBSCRIPTION" in src
        assert '"action": "adopt"' in src
