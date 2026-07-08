"""機能3: 学習チャットのメッセージ書き直し・削除（以降を削除して再処理）。

対象:
  - `backend/api/schemas.py` の `LearningChatRequest.replace_message_id`
  - `backend/api/services.py` の `truncate_chat_and_supersede` / `_split_history_at`
    / `_TRACE_STATUSES`(superseded) / get_interest_traces・anchor digest の除外
  - `backend/api/routes/learning.py` の learning_chat での truncate と DELETE メッセージ API
  - `backend/core/tension/worker.py` の superseded 除外
  - `frontend/public/js/app.js` の書き直し/削除 UI と replace_message_id 送信
  - `frontend/public/css/styles.css` のアクションボタン/バナー

静的解析が中心。`_split_history_at` は純粋関数の挙動テストも行う（backend 依存が
入らない環境では import 失敗を skip）。外部 API/DB は呼び出さない。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "frontend" / "public"

SCHEMAS_PY = BACKEND_DIR / "api" / "schemas.py"
SERVICES_PY = BACKEND_DIR / "api" / "services.py"
LEARNING_PY = BACKEND_DIR / "api" / "routes" / "learning.py"
TENSION_WORKER = BACKEND_DIR / "core" / "tension" / "worker.py"
APP_JS = FRONTEND_DIR / "js" / "app.js"
STYLES_CSS = FRONTEND_DIR / "css" / "styles.css"


# ---------------------------------------------------------------------------
# schemas.py
# ---------------------------------------------------------------------------


def test_request_has_replace_message_id():
    src = SCHEMAS_PY.read_text(encoding="utf-8")
    block = re.search(r"class LearningChatRequest\(BaseModel\):(.+?)\nclass ", src, re.DOTALL).group(1)
    assert re.search(r"replace_message_id\s*:\s*str\s*\|\s*None\s*=\s*None", block)


# ---------------------------------------------------------------------------
# services.py — 静的
# ---------------------------------------------------------------------------


class TestServicesStatic:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = SERVICES_PY.read_text(encoding="utf-8")

    def test_superseded_in_trace_statuses(self):
        block = re.search(r"_TRACE_STATUSES\s*=\s*\((.+?)\)", self.src, re.DOTALL).group(1)
        assert '"superseded"' in block

    def test_defines_truncate_and_split(self):
        assert "def truncate_chat_and_supersede" in self.src
        assert "def _split_history_at" in self.src

    def test_truncate_supersedes_not_deletes_traces(self):
        body = self.src.split("def truncate_chat_and_supersede")[1].split("\ndef ")[0]
        # 痕跡は削除ではなく status='superseded' へ（P4）
        assert "status = 'superseded'" in body
        assert "payload->>'message_id'" in body
        # 履歴が空なら行削除、残れば UPDATE
        assert "DELETE FROM learning_chat_history" in body
        assert "UPDATE learning_chat_history" in body

    def test_get_interest_traces_excludes_superseded(self):
        body = self.src.split("def get_interest_traces")[1].split("\ndef ")[0]
        assert "status <> 'superseded'" in body

    def test_anchor_digest_excludes_superseded(self):
        body = self.src.split("def get_anchor_digest")[1].split("\ndef ")[0]
        assert "status <> 'superseded'" in body


# ---------------------------------------------------------------------------
# services.py — _split_history_at の純粋関数テスト（backend 依存が無ければ skip）
# ---------------------------------------------------------------------------


class TestSplitHistoryBehavior:
    @pytest.fixture(autouse=True)
    def _import(self):
        import importlib
        import sys
        sys.path.insert(0, str(BACKEND_DIR / "api"))
        sys.path.insert(0, str(BACKEND_DIR))
        try:
            services = importlib.import_module("services")
        except Exception:  # pragma: no cover - ローカルに backend 依存が無い環境
            pytest.skip("backend deps unavailable (services import failed)")
        self.split = services._split_history_at

    def _hist(self):
        return [
            {"role": "user", "content": "q1", "id": "u1"},
            {"role": "assistant", "content": "a1", "id": "a1"},
            {"role": "user", "content": "q2", "id": "u2"},
            {"role": "assistant", "content": "a2", "id": "a2"},
        ]

    def test_split_at_middle_user_message(self):
        truncated, removed, removed_ids = self.split(self._hist(), "u2")
        assert [m["id"] for m in truncated] == ["u1", "a1"]
        assert [m["id"] for m in removed] == ["u2", "a2"]
        assert removed_ids == ["u2", "a2"]

    def test_split_at_first_message_empties_history(self):
        truncated, removed, removed_ids = self.split(self._hist(), "u1")
        assert truncated == []
        assert removed_ids == ["u1", "a1", "u2", "a2"]

    def test_split_missing_id_returns_none(self):
        truncated, removed, removed_ids = self.split(self._hist(), "nope")
        assert truncated is None and removed is None and removed_ids is None


# ---------------------------------------------------------------------------
# learning.py
# ---------------------------------------------------------------------------


class TestLearningRoutes:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = LEARNING_PY.read_text(encoding="utf-8")

    def test_imports_truncate(self):
        assert "truncate_chat_and_supersede" in self.src

    def test_learning_chat_handles_replace(self):
        body = self.src.split("def learning_chat")[1].split("\n@router")[0]
        assert "body.replace_message_id" in body
        assert "truncate_chat_and_supersede" in body
        # サーバ正本の切り詰め結果で body.history を上書きする
        assert "body.history" in body and "truncated_history" in body

    def test_delete_message_endpoint(self):
        assert re.search(
            r'@router\.delete\(\s*\n\s*"/courses/\{course_id\}/topics/\{topic_id\}/chat/messages/\{message_id\}"',
            self.src,
        )
        assert "def delete_chat_message_from" in self.src
        body = self.src.split("def delete_chat_message_from")[1].split("\n@router")[0]
        assert "truncate_chat_and_supersede" in body
        assert "status_code=404" in body  # message not found


# ---------------------------------------------------------------------------
# tension worker — superseded 除外
# ---------------------------------------------------------------------------


def test_tension_worker_excludes_superseded():
    src = TENSION_WORKER.read_text(encoding="utf-8")
    body = src.split("def _fetch_pending_hints")[1].split("\ndef ")[0]
    assert "status <> 'superseded'" in body


# ---------------------------------------------------------------------------
# frontend app.js
# ---------------------------------------------------------------------------


class TestAppJs:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = APP_JS.read_text(encoding="utf-8")

    def test_state_has_editing(self):
        assert "editingMessageId" in self.src

    @pytest.mark.parametrize(
        "fn",
        [
            "function startEditMessage",
            "function cancelEditMessage",
            "function deleteMessageFrom",
            "function showEditIndicator",
            "function _findMessageIndexById",
        ],
    )
    def test_defines_functions(self, fn):
        assert fn in self.src

    def test_user_bubble_has_action_buttons(self):
        assert "data-edit-msg" in self.src
        assert "data-del-msg" in self.src

    def test_send_includes_replace_message_id(self):
        assert "_replace_message_id" in self.src
        assert "replace_message_id: replaceMessageId" in self.src

    def test_delete_calls_message_endpoint(self):
        assert "/chat/messages/" in self.src
        # slice で以降をクライアントからも取り除く
        assert re.search(r"state\.chatMessages\s*=\s*state\.chatMessages\.slice\(0,\s*ri\)", self.src)
        assert re.search(r"state\.chatMessages\s*=\s*state\.chatMessages\.slice\(0,\s*idx\)", self.src)


# ---------------------------------------------------------------------------
# styles.css
# ---------------------------------------------------------------------------


def test_css_defines_action_and_banner():
    src = STYLES_CSS.read_text(encoding="utf-8")
    for sel in (".mg-actions", ".mg-act-btn", ".chat-edit-indicator", ".chat-edit-cancel"):
        assert sel in src, f"{sel} が無い"
