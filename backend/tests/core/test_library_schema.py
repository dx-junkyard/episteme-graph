"""core/library/schema.py（L層ライブラリの dataclass・語彙）の単体テスト。

DB / MinIO への実接続は行わない。draft/凍結の dataclass ⇄ dict 変換ヘルパと、
凍結時 embedding 対象テキストの組み立て（``embedding_source_text``）の純ロジックを検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.library import schema as lib_schema  # noqa: E402


class TestEntryTypeAndStatusVocabulary:
    def test_is_valid_entry_type(self):
        assert lib_schema.is_valid_entry_type("apparatus")
        assert lib_schema.is_valid_entry_type("theory_component")
        assert not lib_schema.is_valid_entry_type("bogus")

    def test_is_valid_status(self):
        assert lib_schema.is_valid_status("active")
        assert lib_schema.is_valid_status("retired")
        assert not lib_schema.is_valid_status("deleted")

    def test_apparatus_body_keys_include_connections(self):
        """N28: 装置候補の connections の受け皿キー。昇格モーダルが候補から転記する
        （キーが無いと接続情報が昇格で構造的に落ちる = P4 違反）。"""
        assert lib_schema.APPARATUS_BODY_KEY_CONNECTIONS == "connections"
        assert "connections" in lib_schema.APPARATUS_BODY_KEYS
        # 既存キーの互換（並びの先頭4つは従来どおり）。
        for key in ("typical_parts", "visual_cues", "typical_configurations", "measurement_targets"):
            assert key in lib_schema.APPARATUS_BODY_KEYS


class TestLibraryEntryToDictRoundTrip:
    def test_to_dict_contains_all_expected_keys(self):
        entry = lib_schema.LibraryEntry(
            id="e1", domain_key="particle_physics", entry_type="apparatus",
            name="Vacuum chamber", aliases=["chamber"], summary="A sealed chamber.",
            body={"typical_parts": ["flange", "gauge"]}, exemplar_images=[],
            source_component_ids=["c1"], source_document_ids=["d1"],
            status="active", revision=2, latest_version_no=1,
            created_by="u1", updated_by="u2", created_at="2026-01-01T00:00:00",
            updated_at="2026-01-02T00:00:00",
        )
        d = entry.to_dict()
        for key in (
            "id", "domain_key", "entry_type", "name", "aliases", "summary", "body",
            "exemplar_images", "source_component_ids", "source_document_ids",
            "status", "revision", "latest_version_no", "created_by", "updated_by",
            "created_at", "updated_at",
        ):
            assert key in d
        assert d["name"] == "Vacuum chamber"
        assert d["aliases"] == ["chamber"]
        assert d["revision"] == 2

    def test_to_dict_returns_copies_not_references(self):
        """呼び出し側が dict を変更しても dataclass の内部状態が汚染されない。"""
        entry = lib_schema.LibraryEntry(aliases=["a"], body={"k": "v"})
        d = entry.to_dict()
        d["aliases"].append("b")
        d["body"]["k2"] = "v2"
        assert entry.aliases == ["a"]
        assert entry.body == {"k": "v"}

    def test_defaults(self):
        entry = lib_schema.LibraryEntry()
        assert entry.entry_type == lib_schema.ENTRY_TYPE_APPARATUS
        assert entry.status == lib_schema.STATUS_ACTIVE
        assert entry.aliases == []
        assert entry.body == {}
        assert entry.revision == 1
        assert entry.latest_version_no == 0


class TestLibraryEntryVersionToDict:
    def test_to_dict_excludes_embedding(self):
        """embedding はベクトルであり検索専用のため to_dict() には含めない。"""
        version = lib_schema.LibraryEntryVersion(
            id="v1", entry_id="e1", version_no=1, content={"name": "n"},
            embedding=[0.1, 0.2, 0.3], note="froze it", published_by="u1",
            created_at="2026-01-01T00:00:00",
        )
        d = version.to_dict()
        assert "embedding" not in d
        assert d["content"] == {"name": "n"}
        assert d["version_no"] == 1


class TestAsListAsDictHelpers:
    def test_as_list_passes_through_list(self):
        assert lib_schema.as_list(["a", "b"]) == ["a", "b"]

    def test_as_list_parses_json_string(self):
        assert lib_schema.as_list('["a", "b"]') == ["a", "b"]

    def test_as_list_defaults_to_empty_for_invalid_input(self):
        assert lib_schema.as_list("not json") == []
        assert lib_schema.as_list(None) == []
        assert lib_schema.as_list('{"a": 1}') == []  # dict, not list

    def test_as_dict_passes_through_dict(self):
        assert lib_schema.as_dict({"a": 1}) == {"a": 1}

    def test_as_dict_parses_json_string(self):
        assert lib_schema.as_dict('{"a": 1}') == {"a": 1}

    def test_as_dict_defaults_to_empty_for_invalid_input(self):
        assert lib_schema.as_dict("not json") == {}
        assert lib_schema.as_dict(None) == {}
        assert lib_schema.as_dict("[1, 2]") == {}  # list, not dict


class TestEmbeddingSourceText:
    def test_combines_name_aliases_summary_and_visual_cues(self):
        entry = {
            "name": "Vacuum chamber",
            "aliases": ["chamber", "bell jar"],
            "summary": "A sealed enclosure for low-pressure experiments.",
            "body": {"visual_cues": ["cylindrical", "radial flanges"]},
        }
        text = lib_schema.embedding_source_text(entry)
        assert "Vacuum chamber" in text
        assert "chamber" in text
        assert "bell jar" in text
        assert "sealed enclosure" in text
        assert "cylindrical" in text
        assert "radial flanges" in text

    def test_visual_cues_as_single_string_is_included(self):
        entry = {"name": "n", "body": {"visual_cues": "a single description"}}
        assert "a single description" in lib_schema.embedding_source_text(entry)

    def test_empty_entry_yields_empty_text(self):
        assert lib_schema.embedding_source_text({}) == ""

    def test_missing_body_does_not_raise(self):
        entry = {"name": "n", "summary": "s"}
        text = lib_schema.embedding_source_text(entry)
        assert text == "n\ns"

    def test_blank_fields_are_skipped_not_left_as_empty_lines(self):
        entry = {"name": "n", "aliases": [], "summary": "", "body": {}}
        assert lib_schema.embedding_source_text(entry) == "n"


class TestUpdatableAndJsonFieldWhitelists:
    def test_updatable_fields_matches_json_fields_subset(self):
        # JSON_FIELDS はすべて UPDATABLE_FIELDS に含まれる（store.update_entry が
        # JSONB キャストを判定する対象は常に更新可能フィールドの一部であるべき）。
        assert set(lib_schema.JSON_FIELDS) <= set(lib_schema.UPDATABLE_FIELDS)

    def test_name_is_updatable_but_not_a_json_field(self):
        assert "name" in lib_schema.UPDATABLE_FIELDS
        assert "name" not in lib_schema.JSON_FIELDS
