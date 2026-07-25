"""Admin Copilot guidance の manual フォールバック（docs/manual §1-2）— 検証。

`core/help_kb` は別タスクで並行実装中のため、実 docs/manual の内容には依存しない
モックのみで検証する（設計書 docs/features/manual_help_kb_design.md §1-2）:

  - capability KB が documented のとき: 応答本文は変えず、manual ヒットは
    citations に出所別で併記するだけ。
  - capability KB が未整備 / capability 未解決のとき: manual ヒットがあれば
    「未整備」固定文の代わりに manual 節本文を素通しし、citation を付ける。
  - manual 側にもヒットが無ければ現状の挙動（未整備固定文 / 操作例メッセージ）のまま。
  - `core.help_kb` が import できない環境（未実装・並行実装中）でも例外を出さず、
    現状と完全に同じ挙動に縮退する（fail-closed）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "api"))

import routes.admin_assistant as route_mod  # noqa: E402
from core.admin_assistant import capabilities as caps  # noqa: E402


def _fake_manual_hit(anchor="usage", title="使い方", body="マニュアル本文です。", audience="teacher"):
    return {
        "file": "02-teacher.md",
        "anchor": anchor,
        "title": title,
        "body": body,
        "audience": audience,
        "citation": f"manual/{audience}/02-teacher.md#{anchor}",
        "documented": True,
    }


class TestManualFallbackDisabled:
    """core.help_kb が無い/呼び出しが失敗する環境では現状と完全に同じ挙動。"""

    def test_module_absent_falls_back_to_current_behavior(self, monkeypatch):
        monkeypatch.setattr(route_mod, "_search_manual", None)
        resp = route_mod._guidance_response("教材のアップロード方法を教えて", "TEACHER", None)
        # 既存の「できる操作の例」/ capability KB 応答のいずれかであり、manual citation は無い
        assert all(not c["doc"].startswith("manual/") for c in resp.citations)

    def test_search_manual_raising_is_swallowed(self, monkeypatch):
        def _boom(*a, **kw):
            raise RuntimeError("index unavailable")

        monkeypatch.setattr(route_mod, "_search_manual", _boom)
        # 例外を投げずに現状の未整備/例示メッセージへ縮退する。
        resp = route_mod._guidance_response("ぜんぜん関係ない質問です", "TEACHER", None)
        assert resp.answer  # 何らかの応答が返る（クラッシュしない）
        assert all(not c["doc"].startswith("manual/") for c in resp.citations)


class TestManualFallbackWhenPrimaryUndocumented:
    def test_undocumented_capability_uses_manual_body(self, monkeypatch):
        """capability に howto_doc セクションが無い（documented=False）とき、
        manual ヒットがあれば本文をそのまま使う。"""
        cap = caps.get_capability("materials.upload")
        assert cap is not None

        # capability KB 側を「未整備」に強制する。
        monkeypatch.setattr(route_mod.kb, "section_for_howto", lambda howto: None)
        monkeypatch.setattr(route_mod.kb, "search", lambda *a, **kw: [])

        hit = _fake_manual_hit(body="教材のアップロードは画面左のボタンから行います。")
        monkeypatch.setattr(route_mod, "_search_manual", lambda *a, **kw: [hit])

        resp = route_mod._guidance_response("教材のアップロード方法を教えて", "TEACHER", cap)

        assert "教材のアップロードは画面左のボタンから行います。" in resp.answer
        assert "まだ整備されていません" not in resp.answer
        assert {"doc": hit["citation"]} in resp.citations

    def test_no_manual_hit_keeps_existing_undocumented_text(self, monkeypatch):
        """manual にもヒットが無ければ、従来どおりの未整備固定文のまま。"""
        cap = caps.get_capability("materials.upload")
        monkeypatch.setattr(route_mod.kb, "section_for_howto", lambda howto: None)
        monkeypatch.setattr(route_mod.kb, "search", lambda *a, **kw: [])
        monkeypatch.setattr(route_mod, "_search_manual", lambda *a, **kw: [])

        resp = route_mod._guidance_response("教材のアップロード方法を教えて", "TEACHER", cap)

        assert "まだ整備されていません" in resp.answer
        assert resp.citations == []

    def test_no_capability_resolved_and_no_kb_hit_uses_manual(self, monkeypatch):
        """cap が None（未解決） かつ capability KB 検索もヒット無しのとき、
        manual ヒットがあれば操作例メッセージの代わりにそれを使う。"""
        monkeypatch.setattr(route_mod.kb, "search", lambda *a, **kw: [])
        hit = _fake_manual_hit(body="この画面では次のことができます。")
        monkeypatch.setattr(route_mod, "_search_manual", lambda *a, **kw: [hit])

        resp = route_mod._guidance_response("この画面って何ができるの", "TEACHER", None)

        assert "この画面では次のことができます。" in resp.answer
        assert {"doc": hit["citation"]} in resp.citations
        assert "できる操作の例" not in resp.answer


class TestManualCitationAlongsidePrimary:
    def test_documented_primary_keeps_answer_and_adds_manual_citation(self, monkeypatch):
        """capability KB が documented のときは応答本文は変えず、
        manual ヒットは citation としてのみ併記する。"""
        cap = caps.get_capability("materials.upload")
        section = {
            "title": "教材のアップロード",
            "body": "アップロードタブからファイルを選んでください。",
            "file": "materials.md",
            "anchor": "upload",
        }
        monkeypatch.setattr(route_mod.kb, "section_for_howto", lambda howto: dict(section))
        monkeypatch.setattr(route_mod.kb, "search", lambda *a, **kw: [])

        hit = _fake_manual_hit(body="この本文は citation にのみ使われるはず。")
        monkeypatch.setattr(route_mod, "_search_manual", lambda *a, **kw: [hit])

        resp = route_mod._guidance_response("教材のアップロード方法を教えて", "TEACHER", cap)

        assert resp.answer == "アップロードタブからファイルを選んでください。"
        assert "この本文は citation にのみ使われるはず。" not in resp.answer
        assert {"doc": "admin_operations/materials.md#upload"} in resp.citations
        assert {"doc": hit["citation"]} in resp.citations


class TestAudienceResolution:
    def test_system_admin_uses_system_admin_audience(self, monkeypatch):
        captured = {}

        def _capture(message, *, audience, limit=3):
            captured["audience"] = audience
            return []

        monkeypatch.setattr(route_mod, "_search_manual", _capture)
        route_mod._manual_hits("何か質問", "SYSTEM_ADMIN")
        assert captured["audience"] == "system_admin"

    def test_teacher_uses_teacher_audience(self, monkeypatch):
        captured = {}

        def _capture(message, *, audience, limit=3):
            captured["audience"] = audience
            return []

        monkeypatch.setattr(route_mod, "_search_manual", _capture)
        route_mod._manual_hits("何か質問", "TEACHER")
        assert captured["audience"] == "teacher"


class TestNoConfidenceLeak:
    def test_manual_hit_confidence_like_keys_not_surfaced(self, monkeypatch):
        """W8 同型: manual ヒットに confidence/score が混ざっていても citations に漏れない
        （AssistantChatResponse.citations は {"doc": ...} 形式のみ）。"""
        cap = caps.get_capability("materials.upload")
        monkeypatch.setattr(route_mod.kb, "section_for_howto", lambda howto: None)
        monkeypatch.setattr(route_mod.kb, "search", lambda *a, **kw: [])

        hit = _fake_manual_hit()
        hit["confidence"] = 0.87
        hit["score"] = 12
        monkeypatch.setattr(route_mod, "_search_manual", lambda *a, **kw: [hit])

        resp = route_mod._guidance_response("教材のアップロード方法を教えて", "TEACHER", cap)
        for c in resp.citations:
            assert set(c.keys()) == {"doc"}


class TestScreenHintPropagation:
    """§4-3: Copilot の現在タブ（screen_context.tab）を manual 検索のヒントに渡す。

    front-matter ``screen:`` 一致節を優先する ``search_manual(..., screen=...)`` へ
    そのまま伝播させるだけで、フロント側の変更は不要（screen_context は既に
    collectScreenContext() が送っている）。
    """

    def test_screen_hint_propagates_to_search_manual(self, monkeypatch):
        captured = {}

        def _capture(message, *, audience, limit=3, screen=None):
            captured["screen"] = screen
            return []

        monkeypatch.setattr(route_mod, "_search_manual", _capture)
        route_mod._manual_hits("原稿の書き換え方法", "TEACHER", "lecture-studio")
        assert captured["screen"] == "lecture-studio"

    def test_no_screen_hint_keeps_existing_call_shape(self, monkeypatch):
        """screen 未指定時は screen kwarg 自体を渡さない（旧シグネチャとの後方互換）。"""
        captured = {}

        def _capture(message, *, audience, limit=3):
            captured["called"] = True
            captured["audience"] = audience
            return []

        monkeypatch.setattr(route_mod, "_search_manual", _capture)
        route_mod._manual_hits("原稿の書き換え方法", "TEACHER")
        assert captured["called"] is True
        assert captured["audience"] == "teacher"

    def test_empty_screen_string_is_treated_as_no_hint(self, monkeypatch):
        captured = {}

        def _capture(message, *, audience, limit=3):
            captured["called"] = True
            return []

        monkeypatch.setattr(route_mod, "_search_manual", _capture)
        route_mod._manual_hits("原稿の書き換え方法", "TEACHER", "")
        assert captured["called"] is True

    def test_guidance_response_extracts_tab_from_screen_context(self, monkeypatch):
        """_guidance_response が screen_context["tab"] を screen ヒントとして使う。"""
        cap = caps.get_capability("materials.upload")
        monkeypatch.setattr(route_mod.kb, "section_for_howto", lambda howto: None)
        monkeypatch.setattr(route_mod.kb, "search", lambda *a, **kw: [])

        captured = {}

        def _capture(message, role, screen=None):
            captured["screen"] = screen
            return []

        monkeypatch.setattr(route_mod, "_manual_hits", _capture)

        route_mod._guidance_response(
            "教材のアップロード方法を教えて", "TEACHER", cap,
            {"tab": "materials", "selection": {}},
        )
        assert captured["screen"] == "materials"

    def test_guidance_response_without_screen_context_passes_no_hint(self, monkeypatch):
        cap = caps.get_capability("materials.upload")
        monkeypatch.setattr(route_mod.kb, "section_for_howto", lambda howto: None)
        monkeypatch.setattr(route_mod.kb, "search", lambda *a, **kw: [])

        captured = {}

        def _capture(message, role, screen=None):
            captured["screen"] = screen
            return []

        monkeypatch.setattr(route_mod, "_manual_hits", _capture)

        route_mod._guidance_response("教材のアップロード方法を教えて", "TEACHER", cap)
        assert captured["screen"] is None
