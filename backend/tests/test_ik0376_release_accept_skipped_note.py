"""IK-0376: リリース前の確認「この配置で次へ」で、飛ばした教材の理由を事実文で返す。

ペルソナ通し受講で、教員が「確認済み 0・飛ばし 2」を見て失敗かどうか判断できなかった。
飛ばしたのは RR7（edit 権限の無い論文は 403 にせず静かに対象外）による正常な動作。
是正後の契約:

1. 閲覧できるが編集権限の無い教材を飛ばしたときだけ ``skipped_note`` に
   ``RELEASE_SKIPPED_NOT_EDITABLE_NOTE`` が入る
2. 閲覧できない／見つからない教材を飛ばしたときは ``RELEASE_SKIPPED_NOT_VISIBLE_NOTE``
3. 飛ばしが無ければキー自体を足さない（従来レスポンスと同じ形）
4. 既存のキー（confirmed / skipped_documents / decision_context）は変えない
5. 事実文に数字を入れない
"""

from __future__ import annotations

import re

import pytest

from tests.test_release_review import (  # noqa: F401 — fixtures
    _COURSE,
    _DOC,
    _HAS_FASTAPI,
    _placement_row,
    client_and_teacher,
    env,
)
from core import label_vocab

pytestmark_api = pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")


class TestNoteHelper:
    def test_none(self):
        from api.routes.landscape import _release_skipped_note

        assert _release_skipped_note(not_editable=0, hidden=0) == ""

    def test_not_editable_only(self):
        from api.routes.landscape import _release_skipped_note

        assert (_release_skipped_note(not_editable=2, hidden=0)
                == label_vocab.RELEASE_SKIPPED_NOT_EDITABLE_NOTE)

    def test_hidden_only(self):
        from api.routes.landscape import _release_skipped_note

        assert (_release_skipped_note(not_editable=0, hidden=1)
                == label_vocab.RELEASE_SKIPPED_NOT_VISIBLE_NOTE)

    def test_both(self):
        from api.routes.landscape import _release_skipped_note

        note = _release_skipped_note(not_editable=1, hidden=1)
        assert label_vocab.RELEASE_SKIPPED_NOT_EDITABLE_NOTE in note
        assert label_vocab.RELEASE_SKIPPED_NOT_VISIBLE_NOTE in note

    @pytest.mark.parametrize("text", [label_vocab.RELEASE_SKIPPED_NOT_EDITABLE_NOTE,
                                      label_vocab.RELEASE_SKIPPED_NOT_VISIBLE_NOTE])
    def test_no_numbers(self, text):
        assert not re.search(r"[0-9０-９]", text)


def _post_accept(client, teacher, env):
    routes = env["routes"]
    env["monkeypatch"].setattr(
        routes.landscape_store, "accept_inferred_for_documents",
        lambda *args, **kwargs: [],
    )
    env["monkeypatch"].setattr(
        routes.landscape_store, "list_for_documents",
        lambda _s, ids, statuses=(): [],
    )
    return client.post(
        f"/api/admin/landscape/courses/{_COURSE}/placements/accept",
        headers={"Authorization": "Bearer " + teacher},
    )


@pytestmark_api
class TestAcceptResponse:
    def test_not_editable_skip_carries_the_reason(self, client_and_teacher, env):
        """env の既定: _DOC は編集可、_DOC_LOCKED は閲覧のみ。"""
        client, teacher = client_and_teacher
        response = _post_accept(client, teacher, env)
        assert response.status_code == 200
        body = response.json()
        assert body["confirmed"] == 0
        assert body["skipped_documents"] == 1
        assert body["skipped_note"] == label_vocab.RELEASE_SKIPPED_NOT_EDITABLE_NOTE
        assert "decision_context" in body

    def test_no_skip_means_no_key(self, client_and_teacher, env):
        client, teacher = client_and_teacher
        env["monkeypatch"].setattr(
            env["services"], "list_course_source_document_ids",
            lambda course_data: [_DOC],
        )
        body = _post_accept(client, teacher, env).json()
        assert body["skipped_documents"] == 0
        assert "skipped_note" not in body

    def test_hidden_skip_uses_the_visibility_sentence(self, client_and_teacher, env):
        client, teacher = client_and_teacher
        services_module = env["services"]
        env["monkeypatch"].setattr(
            services_module, "list_course_source_document_ids",
            lambda course_data: [_DOC, "missing-ref"],
        )

        def _access(user_id, ref):
            if str(ref) == "missing-ref":
                return services_module.DocumentAccess(
                    document_id=None, source_path="", uploaded_by=None,
                    is_owner=False, can_view=False, can_edit=False,
                )
            return services_module.DocumentAccess(
                document_id=_DOC, source_path="mat-1", uploaded_by=user_id,
                is_owner=True, can_view=True, can_edit=True,
            )

        env["monkeypatch"].setattr(services_module, "resolve_document_access", _access)
        body = _post_accept(client, teacher, env).json()
        assert body["skipped_documents"] == 1
        assert body["skipped_note"] == label_vocab.RELEASE_SKIPPED_NOT_VISIBLE_NOTE
