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

    def test_manual_transforms_captures_screen_front_matter(self, tmp_path):
        _write(
            tmp_path / "a.md",
            "---\naudience: student\nscreen: lecture\n---\n## 節 {#sec}\n本文\n",
        )
        index, _excluded = kb_index.build_section_index(tmp_path, manual_transforms=True)
        assert index["a.md#sec"]["screen"] == "lecture"

    def test_manual_transforms_screen_defaults_to_none_when_absent(self, tmp_path):
        _write(tmp_path / "a.md", "---\naudience: student\n---\n## 節 {#sec}\n本文\n")
        index, _excluded = kb_index.build_section_index(tmp_path, manual_transforms=True)
        assert index["a.md#sec"]["screen"] is None

    def test_non_manual_transforms_has_no_screen_key(self, tmp_path):
        _write(tmp_path / "a.md", "## 節 {#sec}\n本文\n")
        index, _excluded = kb_index.build_section_index(tmp_path)
        assert "screen" not in index["a.md#sec"]

    def test_score_sections_screen_prioritizes_matching_section(self):
        """スコアが正である限り、screen 一致節が高スコアの不一致節より優先される。"""
        sections = [
            {"title": "alpha", "body": "alpha bravo charlie delta", "screen": "screenA"},
            {"title": "other", "body": "alpha bravo echo foxtrot golf", "screen": "screenB"},
        ]
        # screen 無指定なら title 一致ボーナスで sections[0] が先頭（従来挙動）
        baseline = kb_index.score_sections("alpha bravo", sections, limit=2)
        assert baseline[0]["screen"] == "screenA"

        prioritized = kb_index.score_sections("alpha bravo", sections, limit=2, screen="screenB")
        assert prioritized[0]["screen"] == "screenB"

    def test_score_sections_screen_none_matches_baseline_ranking(self):
        sections = [
            {"title": "alpha", "body": "alpha bravo charlie delta", "screen": "screenA"},
            {"title": "other", "body": "alpha bravo echo foxtrot golf", "screen": "screenB"},
        ]
        without_screen = kb_index.score_sections("alpha bravo", sections, limit=2)
        explicit_none = kb_index.score_sections("alpha bravo", sections, limit=2, screen=None)
        assert without_screen == explicit_none

    def test_score_sections_zero_score_section_not_surfaced_by_screen_alone(self):
        sections = [
            {"title": "unrelated", "body": "zzz yyy xxx", "screen": "target"},
        ]
        result = kb_index.score_sections("totally different words", sections, limit=3, screen="target")
        assert result == []


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

    def _setup_screen_tree(self, tmp_path, monkeypatch):
        root = tmp_path / "docs" / "manual"
        _write(
            root / "student" / "a.md",
            "---\naudience: student\nscreen: lecture\n---\n## alpha topic {#alpha}\nalpha bravo charlie delta\n",
        )
        _write(
            root / "student" / "b.md",
            "---\naudience: student\nscreen: chat\n---\n## other topic {#other}\nalpha bravo echo foxtrot\n",
        )
        monkeypatch.setattr(kb_manual, "manual_root", lambda: root)
        return root

    def test_search_manual_screen_none_keeps_previous_ranking(self, tmp_path, monkeypatch):
        self._setup_screen_tree(tmp_path, monkeypatch)
        res = kb_manual.search_manual("alpha bravo", audience="student", limit=5)
        assert res[0]["anchor"] == "alpha"

    def test_search_manual_screen_match_is_prioritized(self, tmp_path, monkeypatch):
        self._setup_screen_tree(tmp_path, monkeypatch)
        res = kb_manual.search_manual("alpha bravo", audience="student", limit=5, screen="chat")
        assert res[0]["anchor"] == "other"

    def test_search_manual_screen_does_not_surface_zero_score_section(self, tmp_path, monkeypatch):
        root = self._setup_screen_tree(tmp_path, monkeypatch)
        _write(
            root / "student" / "c.md",
            "---\naudience: student\nscreen: chat\n---\n## unrelated topic {#zzz}\nzzz yyy xxx\n",
        )
        kb_manual.clear_manual_cache()
        res = kb_manual.search_manual("alpha bravo", audience="student", limit=5, screen="chat")
        anchors = {r["anchor"] for r in res}
        assert "zzz" not in anchors

    def test_search_manual_screen_does_not_grow_result_keys(self, tmp_path, monkeypatch):
        self._setup_screen_tree(tmp_path, monkeypatch)
        expected_keys = {"file", "anchor", "title", "body", "audience", "citation", "documented"}
        res_no_screen = kb_manual.search_manual("alpha bravo", audience="student", limit=5)
        res_with_screen = kb_manual.search_manual("alpha bravo", audience="student", limit=5, screen="chat")
        for item in res_no_screen + res_with_screen:
            assert set(item.keys()) == expected_keys
            assert "score" not in item
            assert "confidence" not in item
            assert "screen" not in item

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


# ---------------------------------------------------------------------------
# 第 22 周 A2（IK-0611 派生）: 目次が答えにならない・日本語の語の切り方
# ---------------------------------------------------------------------------


class TestManualSearchRanking:
    def test_navigation_section_detected_by_structure(self):
        toc = {"body": "1. [ログイン](#login)\n   - [音声](#voice)\n2. [地図](#atlas)"}
        prose = {"body": "1. [ログイン](#login) から入ります。"}
        assert kb_index.is_navigation_section(toc) is True
        assert kb_index.is_navigation_section(prose) is False
        assert kb_index.is_navigation_section({"body": ""}) is False

    def test_toc_never_returned_by_search_manual(self, tmp_path, monkeypatch):
        root = tmp_path / "manual"
        _write(root / "student" / "a.md", (
            "---\naudience: student\n---\n"
            "## 目次 {#toc}\n\n1. [共有する](#share)\n2. [見え方](#seen)\n\n"
            "## 共有する {#share}\n\n共有すると担当の教員に見えます。\n"
        ))
        monkeypatch.setattr(kb_manual, "manual_root", lambda: root)
        hits = kb_manual.search_manual("共有すると誰に見えますか", audience="student", limit=3)
        assert [h["anchor"] for h in hits] == ["share"]

    def test_core_term_beats_function_characters(self, tmp_path, monkeypatch):
        """「と・は・何・で・す・か」の字が多い長い節より、核心の語を持つ節が先に来る。"""
        root = tmp_path / "manual"
        long_body = "これは何ですかとはどういうことですか。" * 20
        _write(root / "student" / "a.md", (
            "---\naudience: student\n---\n"
            f"## 概要 {{#overview}}\n\n{long_body}\n\n"
            "## 段階の言葉 {#levels}\n\n未測定は、比べる材料が無かったという意味です。\n"
        ))
        monkeypatch.setattr(kb_manual, "manual_root", lambda: root)
        hits = kb_manual.search_manual("未測定とは何ですか", audience="student", limit=2)
        assert hits[0]["anchor"] == "levels"

    def test_prefer_anchor_raises_panel_family_only_when_scored(self, tmp_path, monkeypatch):
        root = tmp_path / "manual"
        _write(root / "student" / "a.md", (
            "---\naudience: student\n---\n"
            "## 誰に見えるか {#disclosure-audience}\n\n共有した記録は教員に見えます。誰に見えるかの表。\n\n"
            "## 動かす {#simulation-lab}\n\nパネルを開きます。\n\n"
            "### 共有する {#simulation-lab-share}\n\n共有すると教員に見えます。\n\n"
            "## 無関係 {#simulation-labx}\n\n地図。\n"
        ))
        monkeypatch.setattr(kb_manual, "manual_root", lambda: root)
        hits = kb_manual.search_manual(
            "誰に見えるか", audience="student", limit=3, prefer_anchor="simulation-lab",
        )
        anchors = [h["anchor"] for h in hits]
        assert anchors[0] == "simulation-lab-share"
        assert "simulation-labx" not in anchors  # 一族は anchor と "{anchor}-" 始まりだけ

    def test_copilot_tokenize_unchanged(self):
        """Copilot KB（admin_operations）が使う ``tokenize`` は従来どおり 1 字単位。"""
        assert kb_index.tokenize("未測定") == {"未", "測", "定"}
        assert "未測" in kb_index.tokenize_cjk_ngrams("未測定")
        assert "と" not in kb_index.tokenize_cjk_ngrams("未測定とは")

    def test_real_student_manual_answers_simulation_level_questions(self):
        """実マニュアル: SB の結果の言葉は定義の節が、共有は共有の節が先頭に来る（目次は出ない）。"""
        cases = {
            "未測定とは何ですか": "simulation-lab-levels",
            "未測定って何": "simulation-lab-levels",
            "一致と分離は何と何を比べた言葉ですか": "simulation-lab-levels",
            "予想は点数になるか": "simulation-lab-prediction",
            "共有すると誰に見えますか": "simulation-lab-share",
        }
        for query, anchor in cases.items():
            hits = kb_manual.search_manual(query, audience="student", limit=3)
            assert hits and hits[0]["anchor"] == anchor, (query, [h["anchor"] for h in hits])
            assert all(h["anchor"] != "toc" for h in hits)


class TestSimulationResultTerms:
    def test_terms_derive_from_label_vocab(self):
        from core import label_vocab
        from core.simulation_bridge.schema import simulation_result_terms_ja

        terms = simulation_result_terms_ja()
        for value in label_vocab.SIMULATION_AGREEMENT_LABELS.values():
            assert value in terms
        assert "記載値" in terms

    @pytest.mark.parametrize("text,expected", [
        ("未測定って何", True),
        ("What does unmeasured mean?", True),
        ("Is the agreement good?", True),
        ("I disagree with this", False),
        ("共有すると誰に見えますか", True),  # 第 22 周 (j): 開いている実行の共有の意味は実行の事実文で答える
        ("予想は点数になりますか", False),
        ("", False),
    ])
    def test_mentions_simulation_result(self, text, expected):
        from core.simulation_bridge.schema import mentions_simulation_result

        assert mentions_simulation_result(text) is expected
