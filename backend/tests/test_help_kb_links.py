"""help_kb — マニュアル内部リンク実在検査（``validator.check_manual_links``）のテスト。

正本: `docs/architecture/feature_consolidation_proposals_2026-08-13.md` §3-5
（リンク検査の常設化）。過去に `10-admin-materials.md` への番号ズレリンクが
validator の検査範囲外で残留した再発防止。

検査はテキスト辞書ベース（ファイルシステムを見ない）で実装されているため、
①files 配信の起動時検証（`validate_manual()` 経由）②DB draft の freeze 凍結ゲート
（`store.freeze()` 経由）の両方に同一検査が効く。本ファイルはその両経路を検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.help_kb import store as kb_store  # noqa: E402
from core.help_kb import validator as kb_validator  # noqa: E402


def _md(audience: str, body: str) -> str:
    return f"---\naudience: {audience}\n---\n\n{body}"


# ===========================================================================
# 1. 単体（合成テキスト辞書）
# ===========================================================================


class TestCheckManualLinks:
    def test_valid_links_have_no_violations(self):
        texts = {
            "teacher": {
                "10-a.md": _md(
                    "teacher",
                    "## 概要 {#overview}\n"
                    "同一ディレクトリ: [x](11-b.md#save-btn)\n"
                    "同一ファイル: [y](#overview)\n"
                    "他 audience: [z](../student/02-s.md#login)\n",
                ),
                "11-b.md": _md("teacher", "## 保存 {#save-btn}\n本文\n"),
            },
            "student": {"02-s.md": _md("student", "## ログイン {#login}\n本文\n")},
        }
        assert kb_validator.check_manual_links(texts) == []

    def test_broken_same_directory_link_is_reported(self):
        """番号ズレ（存在しない 10-admin-materials.md）を検出する — §3-5 の再発防止対象。"""
        texts = {
            "teacher": {
                "11-admin-materials.md": _md("teacher", "## 概要 {#overview}\n本文\n"),
                "13-c.md": _md(
                    "teacher", "## 概要 {#overview}\n[教材管理](10-admin-materials.md#overview)\n"
                ),
            }
        }
        violations = kb_validator.check_manual_links(texts)
        assert len(violations) == 1
        assert violations[0].startswith(
            "teacher/13-c.md: リンク先ファイルが存在しない teacher/10-admin-materials.md"
        )

    def test_broken_cross_audience_link_is_reported(self):
        texts = {
            "system_admin": {
                "04-s.md": _md("system_admin", "## 概要 {#overview}\n[共通](../teacher/99-x.md)\n")
            },
            "teacher": {"10-a.md": _md("teacher", "## 概要 {#overview}\n本文\n")},
        }
        violations = kb_validator.check_manual_links(texts)
        assert any("teacher/99-x.md" in v for v in violations)

    def test_missing_anchor_in_other_file_is_reported(self):
        texts = {
            "teacher": {
                "10-a.md": _md("teacher", "## 概要 {#overview}\n[保存](11-b.md#no-such)\n"),
                "11-b.md": _md("teacher", "## 保存 {#save-btn}\n本文\n"),
            }
        }
        violations = kb_validator.check_manual_links(texts)
        assert violations == [
            "teacher/10-a.md: リンク先の anchor が存在しない teacher/11-b.md#no-such"
        ]

    def test_missing_same_file_fragment_is_reported(self):
        texts = {
            "teacher": {"10-a.md": _md("teacher", "## 概要 {#overview}\n[ここ](#no-such)\n")}
        }
        violations = kb_validator.check_manual_links(texts)
        assert violations == [
            "teacher/10-a.md: リンク先の anchor が存在しない teacher/10-a.md#no-such"
        ]

    def test_anchor_without_explicit_marker_is_not_accepted(self):
        """明示 ``{#anchor}`` の無い見出しはリンク先として認めない（自動 slug に依存しない）。"""
        texts = {
            "teacher": {
                "10-a.md": _md("teacher", "## 概要 {#overview}\n[保存](11-b.md#hozon)\n"),
                "11-b.md": _md("teacher", "## hozon\n本文\n"),
            }
        }
        violations = kb_validator.check_manual_links(texts)
        assert any("teacher/11-b.md#hozon" in v for v in violations)

    def test_links_inside_code_fence_are_ignored(self):
        texts = {
            "teacher": {
                "10-a.md": _md(
                    "teacher",
                    "## 概要 {#overview}\n"
                    "```markdown\n[例](99-nonexistent.md#zzz)\n```\n"
                    "~~~\n[例2](98-nonexistent.md)\n~~~\n",
                )
            }
        }
        assert kb_validator.check_manual_links(texts) == []

    def test_links_inside_inline_code_are_ignored(self):
        texts = {
            "teacher": {
                "10-a.md": _md(
                    "teacher",
                    "## 概要 {#overview}\n記法の説明: `[例](99-nonexistent.md#zzz)` を使う\n",
                )
            }
        }
        assert kb_validator.check_manual_links(texts) == []

    def test_external_urls_are_skipped(self):
        texts = {
            "teacher": {
                "10-a.md": _md(
                    "teacher",
                    "## 概要 {#overview}\n"
                    "[外部](https://example.com/x.md#frag)\n"
                    "[平文](http://example.com/y.md)\n"
                    "[連絡](mailto:someone@example.com)\n",
                )
            }
        }
        assert kb_validator.check_manual_links(texts) == []

    def test_out_of_manual_targets_are_skipped(self):
        """audience ディレクトリの外へ出る参照は本検査の対象外（KB 非対象・別検査の担当）。"""
        texts = {
            "teacher": {
                "10-a.md": _md(
                    "teacher",
                    "## 概要 {#overview}\n"
                    "[索引](../README.md)\n"
                    "[設計書](../../features/learning.md)\n"
                    "[運用手順](../../admin_operations/materials.md#upload)\n"
                    "[ディレクトリ](../student/)\n"
                    "![図](../assets/x.png)\n",
                )
            }
        }
        assert kb_validator.check_manual_links(texts) == []

    def test_audience_prefixed_path_from_inside_audience_dir_is_reported(self):
        """audience 名から書き始めた誤形（マニュアルはフラット構成）を実在しない扱いにする。"""
        texts = {
            "teacher": {
                "10-a.md": _md("teacher", "## 概要 {#overview}\n[x](teacher/03-teacher.md)\n"),
                "03-teacher.md": _md("teacher", "## 概要 {#overview}\n本文\n"),
            }
        }
        violations = kb_validator.check_manual_links(texts)
        assert len(violations) == 1
        assert violations[0].startswith(
            "teacher/10-a.md: リンク先ファイルが存在しない teacher/teacher/03-teacher.md"
        )

    def test_link_with_title_attribute_is_resolved(self):
        texts = {
            "teacher": {
                "10-a.md": _md("teacher", '## 概要 {#overview}\n[x](11-b.md#s "タイトル")\n'),
                "11-b.md": _md("teacher", "## 保存 {#s}\n本文\n"),
            }
        }
        assert kb_validator.check_manual_links(texts) == []

    def test_empty_input_is_safe(self):
        assert kb_validator.check_manual_links({}) == []


# ===========================================================================
# 2. validate_manual_texts への組み込み
# ===========================================================================


class TestValidateManualTextsIncludesLinkCheck:
    def test_link_violation_surfaces_from_validate_manual_texts(self):
        texts = {
            "teacher": {"10-a.md": _md("teacher", "## 概要 {#overview}\n[x](99-none.md)\n")}
        }
        violations = kb_validator.validate_manual_texts(texts)
        assert any("リンク先ファイルが存在しない teacher/99-none.md" in v for v in violations)

    def test_other_checks_still_reported_alongside(self):
        """既存検査（audience 不一致）とリンク検査は同時に全件報告される（P4）。"""
        texts = {
            "teacher": {"10-a.md": _md("student", "## 概要 {#overview}\n[x](99-none.md)\n")}
        }
        violations = kb_validator.validate_manual_texts(texts)
        assert any("不一致" in v for v in violations)
        assert any("リンク先ファイルが存在しない" in v for v in violations)


# ===========================================================================
# 3. 実ファイル統合（docs/manual に壊れた内部リンクが無い）
# ===========================================================================


class TestRealDocsManualLinks:
    def test_real_docs_have_no_internal_link_violations(self):
        violations = kb_validator.validate_manual()
        broken = [v for v in violations if "リンク先" in v]
        assert broken == [], f"docs/manual の内部リンク違反: {broken}"

    def test_real_docs_validate_manual_is_clean(self):
        assert kb_validator.validate_manual() == []

    def test_real_docs_check_is_not_vacuous(self):
        """実 docs に内部リンクが実在し、壊すと検出される（検査が空振りしていない）。"""
        manual_root = ROOT / "docs" / "manual"
        texts = {
            audience: {
                md.name: md.read_text(encoding="utf-8")
                for md in sorted((manual_root / audience).glob("*.md"))
            }
            for audience in ("student", "teacher", "system_admin")
        }
        assert kb_validator.check_manual_links(texts) == []

        target = sorted(texts["teacher"].keys())[0]
        texts["teacher"][target] += "\n[番号ズレ](10-admin-materials.md#overview)\n"
        violations = kb_validator.check_manual_links(texts)
        assert any("teacher/10-admin-materials.md" in v for v in violations)


# ===========================================================================
# 4. freeze 凍結ゲート経由（DB draft スナップショットでも同一検査が効く）
# ===========================================================================


class _FreezeDraftSession:
    """``store.freeze()`` の draft 読み出しだけを満たす最小フェイクセッション。

    版の INSERT へ進んだ場合は明示的に失敗させる（検証違反時に部分適用しないこと）。
    """

    def __init__(self, rows: list[tuple[str, str, str]]):
        self._rows = rows
        self.write_attempted = False

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        if sql.startswith("SELECT audience, file, content FROM manual_kb_drafts"):
            return self
        self.write_attempted = True
        raise AssertionError(f"検証違反後に SQL が実行された: {sql}")

    def fetchall(self):
        return list(self._rows)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class TestFreezeGateIncludesLinkCheck:
    def test_freeze_rejects_broken_internal_link(self, monkeypatch):
        session = _FreezeDraftSession(
            [
                ("teacher", "10-a.md", _md("teacher", "## 概要 {#overview}\n[x](99-none.md)\n")),
            ]
        )
        monkeypatch.setattr(kb_store, "get_session", lambda: session)
        with pytest.raises(kb_store.FreezeValidationError) as exc:
            kb_store.freeze()
        assert any(
            "リンク先ファイルが存在しない teacher/99-none.md" in v for v in exc.value.violations
        )
        assert session.write_attempted is False

    def test_freeze_rejects_broken_anchor_link(self, monkeypatch):
        session = _FreezeDraftSession(
            [
                (
                    "teacher",
                    "10-a.md",
                    _md("teacher", "## 概要 {#overview}\n[x](11-b.md#no-such)\n"),
                ),
                ("teacher", "11-b.md", _md("teacher", "## 保存 {#save-btn}\n本文\n")),
            ]
        )
        monkeypatch.setattr(kb_store, "get_session", lambda: session)
        with pytest.raises(kb_store.FreezeValidationError) as exc:
            kb_store.freeze()
        assert any(
            "リンク先の anchor が存在しない teacher/11-b.md#no-such" in v
            for v in exc.value.violations
        )
