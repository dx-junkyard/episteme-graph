"""help_kb 共通基盤（core/help_kb/）の基本動作ユニットテスト。

構成:
  - Group A: 汎用索引エンジン（index.py）の純粋ロジック。
  - Group B: docs/manual audience 別索引（manual.py）。tmp_path フィクスチャで
    実 docs に依存せず検証する（docs/manual は audience 分離の移行中のため）。
  - Group C: validator.py（front-matter / anchor / リンク整合性）。

ガードレールテスト（audience 越境禁止・denylist・fail-closed 等）は
test_help_kb_guardrails.py（別担当）に譲り、本ファイルは「動く」ことの確認に限定する。
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

from core.help_kb import index as kb_index  # noqa: E402
from core.help_kb import manual as kb_manual  # noqa: E402
from core.help_kb import validator as kb_validator  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_caches():
    kb_manual.clear_manual_cache()
    yield
    kb_manual.clear_manual_cache()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ===========================================================================
# Group A: index.py（汎用索引エンジン）
# ===========================================================================


class TestIndexEngine:
    def test_build_section_index_basic(self, tmp_path):
        _write(
            tmp_path / "a.md",
            "## 最初の節 {#first}\n本文いち\n\n## 二番目の節 {#second}\n本文に\n",
        )
        index, excluded = kb_index.build_section_index(tmp_path)
        assert excluded == []
        assert set(index.keys()) == {"a.md#first", "a.md#second"}
        assert index["a.md#first"]["title"] == "最初の節"
        assert index["a.md#first"]["body"] == "本文いち"
        assert index["a.md#second"]["anchor"] == "second"

    def test_build_section_index_missing_dir_is_safe(self, tmp_path):
        index, excluded = kb_index.build_section_index(tmp_path / "does-not-exist")
        assert index == {}
        assert excluded == []

    def test_slugify_prefers_explicit_anchor(self):
        assert kb_index.slugify("見出し {#my-anchor}") == "my-anchor"

    def test_slugify_generates_from_text_when_no_explicit_anchor(self):
        slug = kb_index.slugify("これは 見出し です")
        assert slug and "{" not in slug and "#" not in slug

    def test_tokenize_handles_japanese_and_ascii(self):
        tokens = kb_index.tokenize("Upload PDF 教材をアップロード")
        assert "upload" in tokens
        assert "pdf" in tokens
        assert "教" in tokens  # 個別文字トークン化（_WORD_RE の仕様）

    def test_strip_front_matter_skips_without_parsing(self):
        lines = ["---", "audience: student", "---", "# body"]
        result = kb_index.strip_front_matter(lines)
        assert result == ["# body"]

    def test_parse_front_matter_basic(self):
        lines = [
            "---",
            "audience: student",
            "screen: learning",
            "capabilities: [materials.upload, course.publish]",
            "---",
            "# body",
        ]
        meta, rest = kb_index.parse_front_matter(lines)
        assert meta["audience"] == "student"
        assert meta["screen"] == "learning"
        assert meta["capabilities"] == ["materials.upload", "course.publish"]
        assert rest == ["# body"]

    def test_parse_front_matter_absent_returns_empty_meta(self):
        lines = ["## heading {#h}", "body"]
        meta, rest = kb_index.parse_front_matter(lines)
        assert meta == {}
        assert rest == lines

    def test_manual_transforms_strip_non_todo_html_comment(self, tmp_path):
        _write(
            tmp_path / "a.md",
            "## 節 {#sec}\n本文<!-- ただの補足コメント -->続き\n",
        )
        index, excluded = kb_index.build_section_index(tmp_path, manual_transforms=True)
        assert excluded == []
        assert "<!--" not in index["a.md#sec"]["body"]
        assert "本文" in index["a.md#sec"]["body"]

    def test_manual_transforms_excludes_todo_marked_chunk(self, tmp_path):
        _write(
            tmp_path / "a.md",
            "## 節1 {#ok}\n通常の本文\n\n"
            "## 節2 {#todo}\n"
            "| 可否 | 備考 |\n|---|---|\n"
            "| 可 | <!-- TODO: 要確認 --> |\n",
        )
        index, excluded = kb_index.build_section_index(tmp_path, manual_transforms=True, audience_tag="student")
        assert "a.md#ok" in index
        assert "a.md#todo" not in index
        assert excluded == [{"file": "a.md", "anchor": "todo", "title": "節2", "audience": "student"}]

    def test_flatten_tables_produces_label_value_lines(self):
        table = "| 操作 | 可否 |\n|---|---|\n| 公開 | 可 |\n"
        flattened = kb_index.flatten_tables(table)
        assert "|" not in flattened  # 生テーブル記法が残らない
        assert "操作: 公開" in flattened
        assert "可否: 可" in flattened

    def test_score_sections_orders_by_overlap(self):
        sections = [
            {"title": "教材のアップロード", "body": "PDFをアップロードする手順"},
            {"title": "全く関係ない話題", "body": "無関係な内容"},
        ]
        result = kb_index.score_sections("教材 アップロード", sections, limit=3)
        assert result and result[0]["title"] == "教材のアップロード"


# ===========================================================================
# Group B: manual.py（docs/manual audience 別索引）
# ===========================================================================


class TestManualIndex:
    def _setup_tree(self, tmp_path, monkeypatch):
        root = tmp_path / "docs" / "manual"
        _write(
            root / "student" / "s1.md",
            "---\naudience: student\n---\n## 学習の始め方 {#start}\n学生向けの説明\n",
        )
        _write(
            root / "teacher" / "t1.md",
            "---\naudience: teacher\n---\n## 教材アップロード {#upload}\n教員向けの説明\n",
        )
        _write(
            root / "system_admin" / "sa1.md",
            "---\naudience: system_admin\n---\n## ユーザー管理 {#users}\n管理者向けの説明\n",
        )
        monkeypatch.setattr(kb_manual, "manual_root", lambda: root)
        return root

    def test_no_manual_dir_is_safe_and_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kb_manual, "manual_root", lambda: tmp_path / "nope")
        assert kb_manual.manual_available("student") is False
        assert kb_manual.search_manual("何か", audience="student") == []
        assert kb_manual.excluded_sections() == []

    def test_search_manual_requires_audience_keyword(self, tmp_path, monkeypatch):
        self._setup_tree(tmp_path, monkeypatch)
        with pytest.raises(TypeError):
            kb_manual.search_manual("学習", "student")  # type: ignore[misc]

    def test_search_manual_rejects_unknown_audience(self, tmp_path, monkeypatch):
        self._setup_tree(tmp_path, monkeypatch)
        with pytest.raises(ValueError):
            kb_manual.search_manual("学習", audience="nobody")

    def test_student_scope_excludes_teacher_and_admin_sections(self, tmp_path, monkeypatch):
        self._setup_tree(tmp_path, monkeypatch)
        res = kb_manual.search_manual("説明", audience="student", limit=5)
        assert res
        assert all(r["audience"] == "student" for r in res)
        assert all(r["citation"].startswith("manual/student/") for r in res)

    def test_teacher_scope_inherits_student_sections(self, tmp_path, monkeypatch):
        self._setup_tree(tmp_path, monkeypatch)
        res = kb_manual.search_manual("説明", audience="teacher", limit=5)
        audiences = {r["audience"] for r in res}
        assert audiences == {"student", "teacher"}

    def test_system_admin_scope_sees_all_three(self, tmp_path, monkeypatch):
        self._setup_tree(tmp_path, monkeypatch)
        res = kb_manual.search_manual("説明", audience="system_admin", limit=5)
        audiences = {r["audience"] for r in res}
        assert audiences == {"student", "teacher", "system_admin"}

    def test_citation_format(self, tmp_path, monkeypatch):
        self._setup_tree(tmp_path, monkeypatch)
        res = kb_manual.search_manual("学習の始め方", audience="student", limit=1)
        assert res[0]["citation"] == "manual/student/s1.md#start"

    def test_result_has_no_score_or_confidence_keys(self, tmp_path, monkeypatch):
        self._setup_tree(tmp_path, monkeypatch)
        res = kb_manual.search_manual("説明", audience="student", limit=5)
        for item in res:
            assert "score" not in item
            assert "confidence" not in item

    def test_manual_available_true_and_false(self, tmp_path, monkeypatch):
        self._setup_tree(tmp_path, monkeypatch)
        assert kb_manual.manual_available("student") is True
        # teacher-only な語で student 検索は空にはならなくても available 自体は
        # student/ にセクションがあれば True。
        assert kb_manual.manual_available("teacher") is True

    def test_documented_false_for_empty_body(self, tmp_path, monkeypatch):
        root = tmp_path / "docs" / "manual"
        _write(
            root / "student" / "s.md",
            "---\naudience: student\n---\n## 空の節 {#empty}\n## 次の節 {#next}\n本文\n",
        )
        monkeypatch.setattr(kb_manual, "manual_root", lambda: root)
        res = kb_manual.search_manual("空の節", audience="student", limit=1)
        assert res and res[0]["documented"] is False

    def test_excluded_sections_aggregates_all_audiences(self, tmp_path, monkeypatch):
        root = tmp_path / "docs" / "manual"
        _write(
            root / "student" / "s.md",
            "---\naudience: student\n---\n## 未確認 {#unverified}\n<!-- TODO: 確認する -->\n",
        )
        monkeypatch.setattr(kb_manual, "manual_root", lambda: root)
        excluded = kb_manual.excluded_sections()
        assert excluded == [{"file": "s.md", "anchor": "unverified", "title": "未確認", "audience": "student"}]
        # 除外チャンクは検索結果にも現れない
        assert kb_manual.search_manual("未確認", audience="student") == []

    def test_clear_manual_cache_reflects_file_changes(self, tmp_path, monkeypatch):
        # 語彙重なりスコアは文字単位トークン化のため、漢字を共有する文言だと
        # 新旧どちらの索引でも overlap が出てしまう。ASCII の無関係な語で検証する。
        root = tmp_path / "docs" / "manual"
        _write(root / "student" / "s.md", "---\naudience: student\n---\n## alphabravo {#a}\nalpha bravo charlie\n")
        monkeypatch.setattr(kb_manual, "manual_root", lambda: root)
        first = kb_manual.search_manual("alpha bravo charlie", audience="student", limit=1)
        assert first and "alpha bravo charlie" in first[0]["body"]

        _write(root / "student" / "s.md", "---\naudience: student\n---\n## deltaecho {#a}\ndelta echo foxtrot\n")
        # キャッシュクリア前は古い内容のまま（クエリ語彙が新内容と一切重ならない）
        stale = kb_manual.search_manual("delta echo foxtrot", audience="student", limit=1)
        assert stale == []

        kb_manual.clear_manual_cache()
        fresh = kb_manual.search_manual("delta echo foxtrot", audience="student", limit=1)
        assert fresh and "delta echo foxtrot" in fresh[0]["body"]

    def test_student_index_builder_source_has_no_other_audience_literal(self):
        """学生索引ビルダーはコード構造として student/ 以外を参照しない。"""
        import inspect

        src = inspect.getsource(kb_manual._student_index)
        assert "teacher" not in src
        assert "system_admin" not in src


# ===========================================================================
# Group C: validator.py
# ===========================================================================


class TestValidator:
    def test_no_manual_dir_returns_no_violations(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kb_validator, "manual_root", lambda: tmp_path / "nope")
        assert kb_validator.validate_manual() == []

    def test_audience_mismatch_is_reported(self, tmp_path, monkeypatch):
        root = tmp_path / "docs" / "manual"
        _write(root / "student" / "s.md", "---\naudience: teacher\n---\n## 節 {#sec}\n本文\n")
        monkeypatch.setattr(kb_validator, "manual_root", lambda: root)
        violations = kb_validator.validate_manual()
        assert any("不一致" in v for v in violations)

    def test_missing_explicit_anchor_is_reported(self, tmp_path, monkeypatch):
        root = tmp_path / "docs" / "manual"
        _write(root / "student" / "s.md", "---\naudience: student\n---\n## 見出しだけ\n本文\n")
        monkeypatch.setattr(kb_validator, "manual_root", lambda: root)
        violations = kb_validator.validate_manual()
        assert any("anchor" in v for v in violations)

    def test_duplicate_anchor_is_reported(self, tmp_path, monkeypatch):
        root = tmp_path / "docs" / "manual"
        _write(
            root / "student" / "s.md",
            "---\naudience: student\n---\n## 節1 {#dup}\n本文1\n\n## 節2 {#dup}\n本文2\n",
        )
        monkeypatch.setattr(kb_validator, "manual_root", lambda: root)
        violations = kb_validator.validate_manual()
        assert any("重複" in v for v in violations)

    def test_broken_relative_link_is_reported(self, tmp_path, monkeypatch):
        root = tmp_path / "docs" / "manual"
        _write(
            root / "student" / "s.md",
            "---\naudience: student\n---\n## 節 {#sec}\n"
            "詳細は[こちら](../../admin_operations/materials.md#nonexistent)を参照。\n",
        )
        _write(
            tmp_path / "docs" / "admin_operations" / "materials.md",
            "## アップロード {#upload}\n本文\n",
        )
        monkeypatch.setattr(kb_validator, "manual_root", lambda: root)
        violations = kb_validator.validate_manual()
        assert any("リンク先が存在しない" in v for v in violations)

    def test_valid_manual_tree_has_no_violations(self, tmp_path, monkeypatch):
        root = tmp_path / "docs" / "manual"
        _write(
            root / "student" / "s.md",
            "---\naudience: student\n---\n## 節 {#sec}\n"
            "詳細は[こちら](../../admin_operations/materials.md#upload)を参照。\n",
        )
        _write(
            tmp_path / "docs" / "admin_operations" / "materials.md",
            "## アップロード {#upload}\n本文\n",
        )
        monkeypatch.setattr(kb_validator, "manual_root", lambda: root)
        assert kb_validator.validate_manual() == []
