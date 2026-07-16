"""D層 UI 配線の静的ガードレール（vision_ux_gap_survey_2026-07.md の G2-D / D層疑義ライフサイクル）。

対象: frontend/public/js/doubt-atlas.js。既存の test_doubt_guardrails.py（禁止語彙・
k-匿名・行保持等の横断ガードレール）を補完し、本調査で指摘された2つの未配線を
固定する:

- T1（G2-D）: `PUT /api/admin/doubt/ledger/{target_type}/{target_id}/verification-status`
  の記帳 UI。directly_verified への昇格はスコープ1件以上が前提（サーバ422）で
  あることを UI 側でも尊重し、選択肢の無効化 + reason 必須の client 側検証を持つこと。
- T2（D層）: 疑義ライフサイクルの完結（`POST /challenges/{id}/withdraw` /
  `POST /challenges/{id}/proposals`）の導線。

このファイルはソースの静的検証で受け入れ条件を固定する（test_deliberation_ui_static.py
と同型のアプローチ）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOUBT_ATLAS_JS = ROOT / "frontend" / "public" / "js" / "doubt-atlas.js"


def _read() -> str:
    return DOUBT_ATLAS_JS.read_text(encoding="utf-8")


class TestVerificationStatusWiring:
    """T1(G2-D): 検証状態の記帳 UI。"""

    def test_file_exists(self):
        assert DOUBT_ATLAS_JS.exists()

    def test_verification_status_form_function_present(self):
        src = _read()
        assert "function bindVerificationStatusForm" in src

    def test_calls_verification_status_endpoint_with_put(self):
        """API は PUT（doubt.py:445）。POST 等に変えていないこと。"""
        src = _read()
        block = re.search(r"function bindVerificationStatusForm[\s\S]+?\n  \}\n", src).group(0)
        assert "/verification-status" in block
        assert 'method: "PUT"' in block or "method: 'PUT'" in block

    def test_status_options_come_from_status_labels(self):
        """段階ラベル（STATUS_LABELS）から選択肢を作る。生の英語語彙を直接ユーザーに見せない。"""
        src = _read()
        block = re.search(r"function bindVerificationStatusForm[\s\S]+?\n  \}\n", src).group(0)
        assert "STATUS_LABELS" in block

    def test_directly_verified_option_disabled_without_scope(self):
        """スコープ0件時は directly_verified の選択肢を無効化する（サーバ422の事前防止）。"""
        src = _read()
        block = re.search(r"function bindVerificationStatusForm[\s\S]+?\n  \}\n", src).group(0)
        assert '"directly_verified"' in block
        assert "scopesCount < 1" in block
        assert "disabled" in block

    def test_reason_required_client_side(self):
        """reason は必須（client 側でも空なら送信しない）。"""
        src = _read()
        block = re.search(r"function bindVerificationStatusForm[\s\S]+?\n  \}\n", src).group(0)
        assert "if (!reason)" in block

    def test_directly_verified_blocked_client_side_even_if_selected(self):
        """空欄スコープで directly_verified を選ぼうとしても送信前に止める（多重防御）。"""
        src = _read()
        block = re.search(r"function bindVerificationStatusForm[\s\S]+?\n  \}\n", src).group(0)
        assert 'status === "directly_verified" && scopesCount < 1' in block

    def test_server_422_message_is_surfaced_not_swallowed(self):
        """422 応答を握りつぶさずメッセージ表示する。"""
        src = _read()
        block = re.search(r"function bindVerificationStatusForm[\s\S]+?\n  \}\n", src).group(0)
        assert "res.status === 422" in block
        assert "data.detail" in block

    def test_attribution_note_present(self):
        """記帳は人間の行為として帰属される旨の説明一行。"""
        src = _read()
        assert "あなたの操作として" in src

    def test_empty_scope_is_framed_as_discovery_not_error(self):
        """空欄スコープは異常ではなく発見であるという D層のトーンを維持する。"""
        src = _read()
        block = re.search(r"function bindVerificationStatusForm[\s\S]+?\n  \}\n", src).group(0)
        assert "空欄は異常ではなく発見です" in block

    def test_ledger_render_wires_the_new_form(self):
        """renderLedgerInto の両分岐（台帳行あり/なし）から bindVerificationStatusForm を呼ぶ。"""
        src = _read()
        assert src.count("bindVerificationStatusForm(container, targetType, targetId") >= 2


class TestChallengeLifecycleWiring:
    """T2: 疑義の取り下げ・検証提案への昇格。"""

    def test_challenge_actions_function_present(self):
        src = _read()
        assert "function bindChallengeActions" in src
        assert "bindChallengeActions(container, targetType, targetId)" in src

    def test_withdraw_calls_existing_api(self):
        src = _read()
        block = re.search(r"function bindChallengeActions[\s\S]+?\n  \}\n", src).group(0)
        assert "/withdraw" in block
        assert 'method: "POST"' in block

    def test_withdraw_forbidden_is_surfaced_as_fact(self):
        """本人以外は 403。フロントはこの事実をメッセージとして表示し、握りつぶさない。"""
        src = _read()
        block = re.search(r"function bindChallengeActions[\s\S]+?\n  \}\n", src).group(0)
        assert "res.status === 403" in block
        assert "本人のみ" in block

    def test_proposal_calls_existing_api(self):
        src = _read()
        block = re.search(r"function bindChallengeActions[\s\S]+?\n  \}\n", src).group(0)
        assert "/proposals" in block
        assert "proposal:" in block

    def test_proposal_requires_non_empty_text(self):
        src = _read()
        block = re.search(r"function bindChallengeActions[\s\S]+?\n  \}\n", src).group(0)
        assert "if (!proposal)" in block

    def test_challenge_status_labels_present(self):
        """疑義の状態（未対応/対応済み/取り下げ済み/検証提案へ昇格済み）を事実として表示する。"""
        src = _read()
        assert "CHALLENGE_STATUS_LABELS" in src
        for status in ("open", "answered", "withdrawn", "led_to_verification"):
            assert f"{status}:" in src

    def test_actions_only_offered_for_open_or_answered(self):
        """withdrawn / led_to_verification の疑義には取り下げ・提案ボタンを出さない。"""
        src = _read()
        assert 'c.status === "open" || c.status === "answered"' in src

    def test_subject_of_challenge_stays_typed_not_personal(self):
        """疑義カードの主語は常に型（人格対立の文面にしない, §8-3）— 既存 CHALLENGE_TYPE_LABELS を維持。"""
        src = _read()
        assert "CHALLENGE_TYPE_LABELS" in src
        assert "という疑義" in src


class TestNoHypeOrRawNumbers:
    """D層不変条項: 数値を見せない・煽り語彙を使わない（既存 test_doubt_guardrails.py と重複検査）。"""

    BANNED = ("疑え", "ノーベル賞", "危険地帯", "要注意ゾーン", "崩壊させよ")

    def test_no_banned_words_in_new_wiring(self):
        src = _read()
        for word in self.BANNED:
            assert word not in src

    def test_no_raw_confidence_in_verification_status_form(self):
        """検証状態記帳フォームは confidence の生値を扱わない。"""
        src = _read()
        block = re.search(r"function bindVerificationStatusForm[\s\S]+?\n  \}\n", src).group(0)
        assert "confidence" not in block
