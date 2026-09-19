"""概念レジストリの store / registry ユニットテスト（concept_registry_design.md §5）。

実 DB を使わない（fake session / fake row で組む）。検証するのは:

1. ``store.create_entry`` の新 kwargs（候補・正当化・candidate_key）と
   aliases → labels の**同一トランザクション**ミラー
2. ``store.freeze_entry`` が未確定の概念を拒否すること（KR2）
3. ``store.list_entries`` の既定が confirmed のみ（後方互換）
4. キー導出（``relation_key`` の無向性・``link_key`` の版非依存・``candidate_key``）
5. ``registry`` の遷移（帰属必須・見送り理由必須・node リンクの kind 制限）
6. ``annotate_node_links``（現行凍結版に無い node の事実付与・骨格不明は None）
"""

from __future__ import annotations

import pytest

from core.library import registry, schema, store
from core.library.store import LibraryRetiredError


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """``execute`` を記録するだけの duck-typed セッション（実 DB なし）。"""

    #: P3-R6 の在籍確認（``_require_not_retired``）が引く行。既定は active で、
    #: retired の挙動を見たいテストだけ ``rows_by_marker`` で上書きする。
    STATUS_MARKER = "SELECT status FROM library_entries"

    def __init__(self, rows_by_marker=None):
        self.rows_by_marker = {self.STATUS_MARKER: [("active",)]}
        self.rows_by_marker.update(dict(rows_by_marker or {}))
        self.statements: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, dict(params or {})))
        for marker, rows in self.rows_by_marker.items():
            if marker in sql:
                return _FakeResult(rows)
        return _FakeResult([])

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


_ENTRY_ID = "11111111-1111-1111-1111-111111111111"
_OTHER_ID = "22222222-2222-2222-2222-222222222222"
_LABEL_ID = "33333333-3333-3333-3333-333333333333"
_ACTOR = "44444444-4444-4444-4444-444444444444"


def _entry_row(*, review_status="confirmed", aliases=None, latest_version_no=0):
    """``store._ENTRY_COLUMNS_SQL`` の並びに合わせた行タプル。"""
    return (
        _ENTRY_ID,              # 0 id
        "astro",                # 1 domain_key
        "concept",              # 2 entry_type
        "Cosmic web",           # 3 name
        list(aliases or []),    # 4 aliases
        "summary",              # 5 summary
        {},                     # 6 body
        [],                     # 7 exemplar_images
        [],                     # 8 source_component_ids
        [],                     # 9 source_document_ids
        "active",               # 10 status
        "unknown",              # 11 standardization_status
        1,                      # 12 revision
        latest_version_no,      # 13 latest_version_no
        None,                   # 14 created_by
        None,                   # 15 updated_by
        None,                   # 16 created_at
        None,                   # 17 updated_at
        review_status,          # 18 review_status
        "",                     # 19 review_note
        None,                   # 20 mapping_justification
        None,                   # 21 candidate_key
        None,                   # 22 decided_by
        None,                   # 23 decided_at
    )


def _label_row(*, status="confirmed", kind="alternate"):
    return (
        _LABEL_ID, _ENTRY_ID, kind, "cosmic web", "cosmic web", "",
        status, "manual_curation", [], "", None, None, None, None,
    )


def _relation_row(*, status="candidate", kind="exact_match"):
    return (
        "55555555-5555-5555-5555-555555555555",
        schema.build_relation_key(kind, _ENTRY_ID, _OTHER_ID),
        _ENTRY_ID, _OTHER_ID, kind, status,
        "lexical_match", "", [], "", None, None, None, None, None,
    )


def _node_link_row(*, status="candidate", node_id="cosmic_web"):
    return (
        "66666666-6666-6666-6666-666666666666",
        schema.build_node_link_key(_ENTRY_ID, "astro", node_id),
        _ENTRY_ID, "astro", node_id, "concept", "exact_match", status,
        "lexical_match", "", [], "", None, None, None, None, None,
    )


@pytest.fixture
def fake_session(monkeypatch):
    """``get_session()`` を差し替えて store / registry に fake を配る。"""
    holder: dict = {}

    def _install(session):
        holder["session"] = session
        monkeypatch.setattr(store, "get_session", lambda: session)
        monkeypatch.setattr(registry, "get_session", lambda: session)
        return session

    return _install


# ---------------------------------------------------------------------------
# 1. キー導出（§4.3〜4.5 / KR9）
# ---------------------------------------------------------------------------


class TestKeyDerivation:
    @pytest.mark.parametrize("kind", ["related", "exact_match", "close_match"])
    def test_symmetric_kinds_fold_both_directions_into_one_key(self, kind):
        assert schema.build_relation_key(kind, "b", "a") == schema.build_relation_key(
            kind, "a", "b"
        )

    def test_broader_keeps_direction(self):
        assert schema.build_relation_key("broader", "narrow", "broad") != schema.build_relation_key(
            "broader", "broad", "narrow"
        )

    def test_node_link_key_has_no_skeleton_version(self):
        key = schema.build_node_link_key(_ENTRY_ID, "astro", "cosmic_web")
        assert key == f"anode|{_ENTRY_ID}|astro|cosmic_web"

    def test_candidate_key_uses_the_shared_normalizer(self):
        from core.atlas_gaps.schema import normalize_label

        assert schema.build_candidate_key("astro", "  Cosmic  WEB ") == (
            f"cand|astro|{normalize_label('Cosmic WEB')}"
        )

    def test_entry_type_mapping_is_domain_neutral(self):
        assert schema.entry_type_for_component_type("DomainMethodComponent") == "method"
        assert schema.entry_type_for_component_type("apparatus") == "apparatus"
        assert schema.entry_type_for_component_type("observation") == "observable"
        # 語彙外・論文固有の型は concept に倒す（情報を落とさず一般の受け皿へ）。
        assert schema.entry_type_for_component_type("PaperClaimComponent") == "concept"
        assert schema.entry_type_for_component_type("なにか") == "concept"

    def test_every_mapped_entry_type_is_in_the_vocabulary(self):
        for value in schema._ENTRY_TYPE_BY_COMPONENT_TYPE.values():
            assert value in schema.ENTRY_TYPES


# ---------------------------------------------------------------------------
# 2. store.create_entry（候補・正当化・ミラー）
# ---------------------------------------------------------------------------


class TestCreateEntry:
    def test_defaults_to_a_human_confirmed_entry(self, fake_session):
        session = fake_session(_FakeSession({"INSERT INTO library_entries": [_entry_row()]}))
        entry = store.create_entry(domain_key="astro", entry_type="concept", name="Cosmic web")
        assert entry["review_status"] == "confirmed"
        insert = [p for sql, p in session.statements if "INSERT INTO library_entries" in sql][0]
        assert insert["review_status"] == "confirmed"
        assert insert["mapping_justification"] == "manual_curation"
        assert insert["candidate_key"] is None

    def test_candidate_entries_carry_their_justification_and_key(self, fake_session):
        session = fake_session(
            _FakeSession({"INSERT INTO library_entries": [_entry_row(review_status="candidate")]})
        )
        candidate_key = schema.build_candidate_key("astro", "Cosmic web")
        store.create_entry(
            domain_key="astro",
            entry_type="concept",
            name="Cosmic web",
            review_status="candidate",
            mapping_justification="lexical_match",
            candidate_key=candidate_key,
        )
        insert = [p for sql, p in session.statements if "INSERT INTO library_entries" in sql][0]
        assert insert["review_status"] == "candidate"
        assert insert["mapping_justification"] == "lexical_match"
        assert insert["candidate_key"] == candidate_key

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"review_status": "approved"},
            {"mapping_justification": "because_i_said_so"},
        ],
    )
    def test_invalid_vocabulary_is_rejected_before_touching_the_db(self, fake_session, kwargs):
        session = fake_session(_FakeSession())
        with pytest.raises(ValueError):
            store.create_entry(
                domain_key="astro", entry_type="concept", name="Cosmic web", **kwargs
            )
        assert session.statements == []

    def test_aliases_are_mirrored_into_label_rows_in_the_same_transaction(self, fake_session):
        session = fake_session(
            _FakeSession(
                {"INSERT INTO library_entries": [_entry_row(aliases=["Cosmic Web", "large-scale structure"])]}
            )
        )
        store.create_entry(
            domain_key="astro",
            entry_type="concept",
            name="Cosmic web",
            aliases=["Cosmic Web", "large-scale structure"],
        )
        mirrors = [p for sql, p in session.statements if "INSERT INTO library_entry_labels" in sql]
        assert [m["normalized_label"] for m in mirrors] == ["cosmic web", "large-scale structure"]
        assert {m["kind"] for m in mirrors} == {"alternate"}
        assert {m["justification"] for m in mirrors} == {"manual_curation"}
        # ミラーは commit の前（= 同一トランザクション）で実行されている。
        order = [sql for sql, _ in session.statements]
        assert any("library_entry_labels" in sql for sql in order)
        assert session.commits == 1

    def test_mirror_does_not_revive_dismissed_labels(self):
        """``ON CONFLICT ... DO UPDATE ... WHERE status = confirmed``（KR7）。"""
        session = _FakeSession()
        store.mirror_aliases_to_labels(session, _ENTRY_ID, ["Cosmic Web"])
        sql, params = session.statements[0]
        assert "ON CONFLICT (entry_id, kind, normalized_label) DO UPDATE" in sql
        assert "WHERE library_entry_labels.status = :confirmed" in sql
        assert params["confirmed"] == "confirmed"

    def test_mirror_skips_blank_and_duplicate_aliases_without_sql(self):
        session = _FakeSession()
        assert store.mirror_aliases_to_labels(session, _ENTRY_ID, ["   ", ""]) == 0
        assert session.statements == []
        assert store.mirror_aliases_to_labels(session, _ENTRY_ID, ["Web", "web"]) == 1


# ---------------------------------------------------------------------------
# 3. freeze / list（KR2 / 後方互換）
# ---------------------------------------------------------------------------


class TestFreezeAndList:
    @pytest.mark.parametrize("review_status", ["candidate", "dismissed"])
    def test_unconfirmed_entries_cannot_be_frozen(self, fake_session, review_status):
        session = fake_session(
            _FakeSession({"SELECT": [_entry_row(review_status=review_status)]})
        )
        with pytest.raises(store.LibraryConflictError):
            store.freeze_entry(_ENTRY_ID)
        assert not any("INSERT INTO library_entry_versions" in sql for sql, _ in session.statements)

    def test_confirmed_entries_still_freeze(self, fake_session):
        session = fake_session(
            _FakeSession(
                {
                    "FROM library_entries": [_entry_row()],
                    "INSERT INTO library_entry_versions": [("vid", 1, None)],
                }
            )
        )
        version = store.freeze_entry(_ENTRY_ID, embed_fn=lambda _t: [0.1])
        assert version["version_no"] == 1
        assert session.commits == 1

    def test_list_entries_hides_candidates_by_default(self, fake_session):
        session = fake_session(_FakeSession())
        store.list_entries()
        sql, params = session.statements[0]
        assert "review_status = :confirmed_review" in sql
        assert params["confirmed_review"] == "confirmed"

    def test_list_entries_can_include_candidates(self, fake_session):
        session = fake_session(_FakeSession())
        store.list_entries(include_candidates=True)
        sql, _params = session.statements[0]
        assert "review_status = :confirmed_review" not in sql

    def test_search_also_looks_at_the_label_table(self, fake_session):
        """P3-R7: 別名・隠しラベルでも引ける（ラベル側は**正規化の完全一致**）。"""
        session = fake_session(_FakeSession())
        store.list_entries(q="Cosmic WEB")
        sql, params = session.statements[0]
        assert "library_entry_labels" in sql
        assert "lbl.normalized_label = :q_normalized" in sql
        # 部分一致は使わない（`SM` が `cosmological` に当たる F-7 の再発源）。
        assert "lbl.normalized_label ILIKE" not in sql
        assert "lbl.label ILIKE" not in sql
        from core.atlas_gaps.schema import normalize_label

        assert params["q_normalized"] == normalize_label("Cosmic WEB")
        # 見送り済みラベルでは引けない（教員の判断を検索が黙って戻さない）。
        assert params["label_confirmed"] == "confirmed"

    def test_hidden_labels_are_searchable_but_never_displayed(self, fake_session):
        """SKOS hiddenLabel: 検索には当たり、表示テキストには現れない（KR7）。

        ラベル表の条件は ``kind`` を絞らない（``hidden`` も当たる）。一方で一覧が返す
        のはエントリ行（``name`` / ``aliases``）だけで、``library_entry_labels`` の
        表示テキストを SELECT していないので hidden が画面に出ることはない。
        """
        session = fake_session(_FakeSession())
        store.list_entries(q="cosmic web")
        sql, _params = session.statements[0]
        assert "lbl.kind" not in sql  # hidden を検索から外さない
        assert "lbl.label" not in sql.split("EXISTS")[0]  # 表示は取らない
        assert "SELECT 1 FROM library_entry_labels" in sql


# ---------------------------------------------------------------------------
# 4. registry — ラベル
# ---------------------------------------------------------------------------


class TestLabels:
    def test_preferred_labels_are_not_rows(self, fake_session):
        fake_session(_FakeSession({"FROM library_entries": [(1,)]}))
        with pytest.raises(ValueError):
            registry.add_label(_ENTRY_ID, kind="preferred", label="Cosmic web", actor_id=_ACTOR)

    def test_label_requires_an_actor(self, fake_session):
        session = fake_session(_FakeSession())
        with pytest.raises(ValueError, match="actor_id"):
            registry.add_label(_ENTRY_ID, kind="alternate", label="web", actor_id="")
        assert session.statements == []

    def test_label_requires_a_valid_justification(self, fake_session):
        session = fake_session(_FakeSession())
        with pytest.raises(ValueError, match="mapping_justification"):
            registry.add_label(
                _ENTRY_ID,
                kind="alternate",
                label="web",
                mapping_justification="guesswork",
                actor_id=_ACTOR,
            )
        assert session.statements == []

    def test_hidden_labels_are_allowed(self, fake_session):
        fake_session(
            _FakeSession(
                {
                    "FROM library_entries": [(1,)],
                    "INSERT INTO library_entry_labels": [_label_row(kind="hidden")],
                }
            )
        )
        label = registry.add_label(
            _ENTRY_ID, kind="hidden", label="c0smic web", actor_id=_ACTOR
        )
        assert label["kind"] == "hidden"

    def test_dismiss_requires_a_reason(self, fake_session):
        session = fake_session(_FakeSession())
        with pytest.raises(ValueError, match="review_note"):
            registry.dismiss_label(_LABEL_ID, actor_id=_ACTOR, review_note="  ")
        assert session.statements == []

    def test_dismiss_is_a_status_transition_not_a_delete(self, fake_session):
        session = fake_session(
            _FakeSession({"UPDATE library_entry_labels": [_label_row(status="dismissed")]})
        )
        label = registry.dismiss_label(_LABEL_ID, actor_id=_ACTOR, review_note="旧表記のため")
        assert label["status"] == "dismissed"
        sql = session.statements[0][0]
        assert "UPDATE library_entry_labels" in sql
        assert "DELETE" not in sql.upper()


# ---------------------------------------------------------------------------
# 5. registry — 関係と node リンク
# ---------------------------------------------------------------------------


class TestRelations:
    def test_relations_are_created_as_candidates(self, fake_session):
        session = fake_session(
            _FakeSession({"INSERT INTO library_entry_relations": [_relation_row()]})
        )
        relation = registry.create_relation(
            subject_entry_id=_ENTRY_ID,
            object_entry_id=_OTHER_ID,
            kind="exact_match",
            mapping_justification="manual_curation",
            actor_id=_ACTOR,
        )
        assert relation["status"] == "candidate"
        insert = next(
            params
            for sql, params in session.statements
            if "INSERT INTO library_entry_relations" in sql
        )
        assert insert["status"] == "candidate"

    def test_confirmed_relations_cannot_be_created_directly(self, fake_session):
        session = fake_session(_FakeSession())
        with pytest.raises(ValueError):
            registry.create_relation(
                subject_entry_id=_ENTRY_ID,
                object_entry_id=_OTHER_ID,
                kind="exact_match",
                mapping_justification="manual_curation",
                status="confirmed",
            )
        assert session.statements == []

    def test_self_relations_are_rejected(self, fake_session):
        fake_session(_FakeSession())
        with pytest.raises(ValueError):
            registry.create_relation(
                subject_entry_id=_ENTRY_ID,
                object_entry_id=_ENTRY_ID,
                kind="related",
                mapping_justification="manual_curation",
            )

    def test_relation_response_never_carries_confidence(self, fake_session):
        fake_session(
            _FakeSession({"INSERT INTO library_entry_relations": [_relation_row()]})
        )
        relation = registry.create_relation(
            subject_entry_id=_ENTRY_ID,
            object_entry_id=_OTHER_ID,
            kind="close_match",
            mapping_justification="vector_similarity",
            confidence=0.91,
        )
        assert "confidence" not in relation

    def test_dismiss_requires_a_reason(self, fake_session):
        fake_session(
            _FakeSession({"FROM library_entry_relations": [_relation_row()]})
        )
        with pytest.raises(Exception) as exc:
            registry.decide_relation(
                "55555555-5555-5555-5555-555555555555",
                status="dismissed",
                actor_id=_ACTOR,
                review_note="",
                record_audit=lambda **kwargs: None,
            )
        assert "reason" in str(exc.value) or "理由" in str(exc.value)

    def test_confirm_records_an_audit_event(self, fake_session):
        fake_session(
            _FakeSession(
                {
                    "FROM library_entry_relations": [_relation_row()],
                    "UPDATE library_entry_relations": [_relation_row(status="confirmed")],
                }
            )
        )
        events: list[dict] = []
        registry.decide_relation(
            "55555555-5555-5555-5555-555555555555",
            status="confirmed",
            actor_id=_ACTOR,
            record_audit=lambda **kwargs: events.append(kwargs),
        )
        assert len(events) == 1
        assert events[0]["action"] == "relation_confirm"
        assert events[0]["entity_type"] == "library_entry"
        assert events[0]["actor_id"] == _ACTOR

    def test_unknown_status_is_rejected(self):
        with pytest.raises(ValueError):
            registry.action_for_status("approved")


class TestNodeLinks:
    @pytest.mark.parametrize("kind", ["broader", "related"])
    def test_only_match_kinds_are_allowed(self, fake_session, kind):
        session = fake_session(_FakeSession())
        with pytest.raises(ValueError):
            registry.create_node_link(
                entry_id=_ENTRY_ID,
                domain_key="astro",
                node_id="cosmic_web",
                kind=kind,
                mapping_justification="lexical_match",
            )
        assert session.statements == []

    def test_link_is_created_as_candidate_and_never_touches_the_skeleton(self, fake_session):
        session = fake_session(
            _FakeSession({"INSERT INTO library_atlas_node_links": [_node_link_row()]})
        )
        link = registry.create_node_link(
            entry_id=_ENTRY_ID,
            domain_key="astro",
            node_id="cosmic_web",
            kind="exact_match",
            mapping_justification="lexical_match",
        )
        assert link["status"] == "candidate"
        assert not any("atlas_skeletons" in sql for sql, _ in session.statements)

    def test_decide_maps_to_node_link_audit_actions(self, fake_session):
        fake_session(
            _FakeSession(
                {
                    "FROM library_atlas_node_links": [_node_link_row()],
                    "UPDATE library_atlas_node_links": [_node_link_row(status="dismissed")],
                }
            )
        )
        events: list[dict] = []
        registry.decide_node_link(
            "66666666-6666-6666-6666-666666666666",
            status="dismissed",
            actor_id=_ACTOR,
            review_note="別の概念の方が近いため",
            record_audit=lambda **kwargs: events.append(kwargs),
        )
        assert events[0]["action"] == "node_link_dismiss"


# ---------------------------------------------------------------------------
# 6. annotate_node_links（KR9）
# ---------------------------------------------------------------------------


class _FakeConcept:
    def __init__(self, node_id, label):
        self.id = node_id
        self.label = label


class TestRetiredIsReadOnly:
    """P3-R6: retired（公開を止めた）エントリは読み取り専用。

    ``store.update_entry`` / ``store.freeze_entry`` が既に ``LibraryRetiredError``
    （route は 409）を出すのに、レジストリ側の書き込み（ラベル追加・関係作成・
    node リンク作成・エントリ確定）だけが素通りしていた。retired を「編集できない」
    ではなく「編集できるが見えない」にすると、公開を止めた概念が裏で育ち続ける。
    """

    def _retired(self, rows_by_marker=None):
        session = _FakeSession(rows_by_marker)
        session.rows_by_marker[_FakeSession.STATUS_MARKER] = [("retired",)]
        return session

    def test_add_label_on_a_retired_entry_is_refused(self, fake_session):
        session = fake_session(self._retired())
        with pytest.raises(LibraryRetiredError):
            registry.add_label(
                _ENTRY_ID, kind="alternate", label="cosmic web", actor_id=_ACTOR
            )
        assert not any("INSERT INTO library_entry_labels" in sql for sql, _ in session.statements)

    def test_create_relation_on_a_retired_entry_is_refused(self, fake_session):
        session = fake_session(self._retired())
        with pytest.raises(LibraryRetiredError):
            registry.create_relation(
                subject_entry_id=_ENTRY_ID,
                object_entry_id=_OTHER_ID,
                kind="exact_match",
                mapping_justification="manual_curation",
                actor_id=_ACTOR,
            )
        assert not any(
            "INSERT INTO library_entry_relations" in sql for sql, _ in session.statements
        )

    def test_create_node_link_on_a_retired_entry_is_refused(self, fake_session):
        session = fake_session(self._retired())
        with pytest.raises(LibraryRetiredError):
            registry.create_node_link(
                entry_id=_ENTRY_ID,
                domain_key="astro",
                node_id="cosmic_web",
                kind="exact_match",
                mapping_justification="lexical_match",
            )
        assert not any(
            "INSERT INTO library_atlas_node_links" in sql for sql, _ in session.statements
        )

    def test_decide_entry_review_on_a_retired_entry_is_refused(self, fake_session, monkeypatch):
        session = fake_session(_FakeSession())
        monkeypatch.setattr(
            store, "get_entry", lambda entry_id: store._row_to_entry(_entry_row()) | {"status": "retired"}
        )
        with pytest.raises(LibraryRetiredError):
            registry.decide_entry_review(
                _ENTRY_ID,
                status="confirmed",
                actor_id=_ACTOR,
                record_audit=lambda **kwargs: None,
            )
        assert not any("UPDATE library_entries" in sql for sql, _ in session.statements)


class _FakeRegion:
    def __init__(self, node_id, label, concepts):
        self.id = node_id
        self.label = label
        self.concepts = concepts


class _FakeSkeleton:
    def __init__(self, regions):
        self.regions = regions
        self.version = "2026.1"


class TestAnnotateNodeLinks:
    def _skeleton(self):
        return _FakeSkeleton(
            [_FakeRegion("cosmology", "宇宙論", [_FakeConcept("cosmic_web", "宇宙の大規模構造")])]
        )

    def test_present_nodes_are_flagged_and_labelled(self):
        links = registry.annotate_node_links(
            [{"node_id": "cosmic_web"}], self._skeleton()
        )
        assert links[0]["node_in_current_version"] is True
        assert links[0]["node_label"] == "宇宙の大規模構造"

    def test_missing_nodes_are_kept_with_the_fact(self):
        links = registry.annotate_node_links([{"node_id": "gone"}], self._skeleton())
        assert links[0]["node_in_current_version"] is False
        assert links[0]["node_label"] == ""

    def test_unknown_skeleton_is_none_not_false(self):
        links = registry.annotate_node_links([{"node_id": "cosmic_web"}], None)
        assert links[0]["node_in_current_version"] is None

    def test_input_rows_are_not_mutated(self):
        rows = [{"node_id": "cosmic_web"}]
        registry.annotate_node_links(rows, self._skeleton())
        assert rows == [{"node_id": "cosmic_web"}]


# ---------------------------------------------------------------------------
# 7. 候補の冪等性（§6.1 / §6.2 が使う読み口）— 共有フェイクテーブルで通しに見る
# ---------------------------------------------------------------------------


class TestCandidateIdempotencyOnTheSharedFake:
    """``tests/fixtures/library_entries_fake.py``（実 SQL 面の模倣）を通した経路確認。

    候補導出（担当 B）が使う ``create_entry(review_status='candidate', ...)`` →
    ``get_entry_by_candidate_key`` の往復が、``candidate_key`` で同じ行に畳めること。
    """

    @pytest.fixture
    def fake_store(self, monkeypatch):
        from tests.fixtures.library_entries_fake import (
            LibraryEntryTableFake,
            make_session_factory,
        )

        fake = LibraryEntryTableFake()
        monkeypatch.setattr(store, "get_session", make_session_factory(fake))
        return fake

    def _create_candidate(self):
        key = schema.build_candidate_key("astro", "Cosmic Web")
        entry = store.create_entry(
            domain_key="astro",
            entry_type="concept",
            name="Cosmic web",
            aliases=["Cosmic Web"],
            review_status="candidate",
            mapping_justification="lexical_match",
            candidate_key=key,
        )
        return key, entry

    def test_candidate_key_round_trip(self, fake_store):
        key, entry = self._create_candidate()
        assert store.get_entry_by_candidate_key(key)["id"] == entry["id"]

    def test_unknown_candidate_key_returns_none(self, fake_store):
        assert store.get_entry_by_candidate_key("cand|astro|nothing") is None
        assert store.get_entry_by_candidate_key("") is None

    def test_candidates_are_hidden_from_the_default_listing(self, fake_store):
        self._create_candidate()
        assert store.list_entries() == []
        assert len(store.list_entries(include_candidates=True)) == 1

    def test_candidate_cannot_be_frozen_until_confirmed(self, fake_store):
        _key, entry = self._create_candidate()
        with pytest.raises(store.LibraryConflictError):
            store.freeze_entry(entry["id"], embed_fn=lambda _t: [0.1])
        assert fake_store.versions == []

    def test_aliases_are_mirrored_through_the_real_sql_surface(self, fake_store):
        self._create_candidate()
        assert [row["normalized_label"] for row in fake_store.labels] == ["cosmic web"]
        assert fake_store.labels[0]["kind"] == "alternate"
