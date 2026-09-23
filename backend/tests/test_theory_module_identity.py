"""理論モジュールの同一性候補（規則 ⑤）と ``related``（theory_module_layer_design.md §13.7 / §13.8）。

fake session（SQL 文字列でディスパッチする最小実装）で見る。DB にも LLM にも embedding API にも
外部 API にも触らない。

固定するもの:

- 規則 ⑤ の母集合（外枠・``identity_eligible``・現在の規則の版・保存された成員 step からの
  下限の再確認 = 成員 3 以上・汎用でない工程の型 2 種以上）
- 指紋の完全一致で構造エントリ 1 行 + 同一性リンク候補 2 本（``structural_match`` /
  ``theory_module`` / 行 UUID / ``local_expression.name`` = ``visual_label``）
- リンク済みと ``dismissed`` の構造を再提案しない・既存の候補エントリは再利用する
- ``candidate_key`` に指紋の平文が無い（TM12）・エントリの ``name`` / リンクの ``reason`` に
  論文の中身（``visual_label`` 等）が無い（TM15）
- 上限は別枠で、0 なら規則 ⑤ だけが止まる・超過は ``coverage_modules``
- ``related``: 3 つの事実文・可視性 fail-closed（``hidden`` のみ・件数なし）・見送り /
  却下の組の除外・応答に数値キーと指紋が再帰的に無い
- 読むのは live ビューだけ（基表 ``knowledge_theory_modules`` を SQL で触らない）
- 学習者向けの旅の列挙に ``theory_module`` 型のリンクを使わない
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.library import identity_candidates as ic  # noqa: E402
from core.library import schema as library_schema  # noqa: E402
from core.library import structural_matches  # noqa: E402
from core.theory_modules import related as tm_related  # noqa: E402
from core.theory_modules import schema as tm_schema  # noqa: E402

DOC = "11111111-1111-1111-1111-111111111111"
OTHER_DOC = "22222222-2222-2222-2222-222222222222"
THIRD_DOC = "33333333-3333-3333-3333-333333333333"

RULE = tm_schema.RULE_VERSION
FP = f"{RULE}|outer|ops=defines:1,substitutes:2|in=1|out=1|premise=0"
FP_OTHER = f"{RULE}|outer|ops=defines:2,solves:1|in=0|out=1|premise=1"

#: 論文由来の表示（理論対象を含む）。候補の name / reason に漏れてはならない。
OWN_VISUAL = "定義・代入で組む 重力ポテンシャルの補正"
PARTNER_VISUAL = "定義・代入で組む 散乱振幅の展開"


def _members(kinds=("defines", "substitutes", "substitutes"), *, generic=False) -> list[dict]:
    return [
        {"step_refs": [f"d1:s{i}"], "operation": kind, "edge_type": kind, "generic": generic}
        for i, kind in enumerate(kinds)
    ]


def _module(
    mid: str,
    document_id: str,
    *,
    fingerprint: str = FP,
    level: str = "outer",
    rule_version: str = RULE,
    eligible: bool = True,
    members=None,
    visual: str = OWN_VISUAL,
    verbs=("定義", "代入"),
    stage: str = "equation_system",
    key: str | None = None,
) -> tuple:
    """``identity_candidates._MODULE_COLUMNS`` の列順の行。"""
    return (
        mid,
        document_id,
        key or f"{RULE}:{mid}",
        rule_version,
        level,
        visual,
        list(verbs),
        stage,
        fingerprint,
        eligible,
        _members() if members is None else members,
    )


class FakeSession:
    def __init__(self, *, own=(), twins=(), linked_modules=()):
        self.own = list(own)
        self.twins = list(twins)
        self.linked_modules = list(linked_modules)
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "knowledge_theory_modules_live" in sql and "document_id <> " in sql:
            rows = self.twins
        elif "knowledge_theory_modules_live" in sql:
            rows = self.own
        elif "FROM element_identity_links" in sql and (params or {}).get("element_type") == "theory_module":
            rows = [(mid,) for mid in self.linked_modules]
        else:
            rows = []
        return _FakeResult(rows)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _settings(component_limit: int = 20, module_limit: int = 10):
    return type(
        "S",
        (),
        {
            "identity_candidates_max_per_document": component_limit,
            "identity_module_candidates_max_per_document": module_limit,
        },
    )()


@pytest.fixture
def env(monkeypatch):
    state: dict = {
        "created_entries": [],
        "created_links": [],
        "entries_by_candidate_key": {},
        "audits": [],
        "settings": _settings(),
    }
    monkeypatch.setattr(ic, "get_settings", lambda: state["settings"])
    monkeypatch.setattr(
        ic.registry, "labels_for_entries",
        lambda entry_ids, include_hidden=True, session=None: {eid: [] for eid in entry_ids},
    )
    monkeypatch.setattr(ic._corpus, "document_domain_keys", lambda session, ref: [])

    def _create_entry(**kwargs):
        entry = {
            "id": f"entry-{len(state['created_entries'])}",
            "name": kwargs["name"],
            "domain_key": kwargs["domain_key"],
            "entry_type": kwargs["entry_type"],
            "review_status": kwargs["review_status"],
            "candidate_key": kwargs.get("candidate_key"),
        }
        state["created_entries"].append(kwargs)
        state["entries_by_candidate_key"][kwargs.get("candidate_key")] = entry
        return entry

    monkeypatch.setattr(
        ic.library_store, "get_entry_by_candidate_key",
        lambda key: state["entries_by_candidate_key"].get(key),
    )
    monkeypatch.setattr(ic.library_store, "create_entry", _create_entry)

    def _create_candidate(ref, shared_part_id, **kwargs):
        state["created_links"].append({"ref": ref, "shared_part_id": shared_part_id, **kwargs})
        return {"id": f"link-{len(state['created_links'])}", "status": "candidate"}

    monkeypatch.setattr(ic._identity_links, "create_candidate", _create_candidate)

    from core.document_pipeline import persistence

    monkeypatch.setattr(persistence, "set_duplicate_candidates", lambda *a, **k: None)
    monkeypatch.setattr(
        persistence, "record_knowledge_audit",
        lambda session, **kwargs: state["audits"].append(kwargs),
    )
    return state


def _run(session):
    return ic.run_identity_candidates(document_id=DOC, run_id="run-1", session=session)


# ---------------------------------------------------------------------------
# 1. 一致で候補（エントリ 1 + リンク 2）
# ---------------------------------------------------------------------------


class TestStructuralMatch:
    def test_exact_fingerprint_match_creates_entry_and_two_links(self, env):
        session = FakeSession(
            own=[_module("m-own", DOC)],
            twins=[_module("m-twin", OTHER_DOC, visual=PARTNER_VISUAL)],
        )
        result = _run(session)
        assert result["module_entries_created"] == 1
        assert result["module_links_created"] == 2
        assert len(env["created_entries"]) == 1
        entry = env["created_entries"][0]
        assert entry["domain_key"] == library_schema.DOMAIN_KEY_UNASSIGNED
        assert entry["review_status"] == library_schema.REVIEW_STATUS_CANDIDATE
        assert entry["mapping_justification"] == "structural_match"
        assert entry["candidate_key"] == library_schema.build_structural_candidate_key(FP)
        assert entry["source_component_ids"] == []
        assert entry["source_document_ids"] == [DOC, OTHER_DOC]

        refs = [(link["ref"].element_type, link["ref"].element_id, link["ref"].document_id)
                for link in env["created_links"]]
        assert refs == [("theory_module", "m-own", DOC), ("theory_module", "m-twin", OTHER_DOC)]
        for link in env["created_links"]:
            assert link["mapping_justification"] == "structural_match"
            assert link["shared_part_id"] == "entry-0"
            assert "confidence" not in link or link["confidence"] is None
        assert env["created_links"][0]["local_expression"] == {"name": OWN_VISUAL}
        assert env["created_links"][1]["local_expression"] == {"name": PARTNER_VISUAL}

    def test_entry_type_follows_dominant_stage(self, env):
        session = FakeSession(
            own=[_module("m-own", DOC, stage="theory_basis")],
            twins=[_module("m-twin", OTHER_DOC, stage="theory_basis")],
        )
        _run(session)
        assert env["created_entries"][0]["entry_type"] == library_schema.ENTRY_TYPE_THEORY

    def test_entry_type_is_method_otherwise(self, env):
        session = FakeSession(own=[_module("m-own", DOC)], twins=[_module("m-twin", OTHER_DOC)])
        _run(session)
        assert env["created_entries"][0]["entry_type"] == library_schema.ENTRY_TYPE_METHOD

    def test_different_fingerprint_is_not_a_match(self, env):
        # SQL の完全一致を fake でも再現する（相手の指紋が違えば twins に入らない）。
        session = FakeSession(own=[_module("m-own", DOC)], twins=[])
        result = _run(session)
        assert result["module_links_created"] == 0
        assert env["created_entries"] == []

    def test_audit_line_carries_module_counts(self, env):
        session = FakeSession(own=[_module("m-own", DOC)], twins=[_module("m-twin", OTHER_DOC)])
        _run(session)
        assert len(env["audits"]) == 1
        stats = env["audits"][0]["stats"]["identity_candidates"]
        assert stats["modules"] == {"entries": 1, "links": 2}


# ---------------------------------------------------------------------------
# 2. 母集合（下限・版・段）
# ---------------------------------------------------------------------------


class TestPopulation:
    def test_fewer_than_three_members_is_excluded(self, env):
        session = FakeSession(
            own=[_module("m-own", DOC, members=_members(("defines", "substitutes")))],
            twins=[_module("m-twin", OTHER_DOC, members=_members(("defines", "substitutes")))],
        )
        result = _run(session)
        assert result["module_links_created"] == 0
        assert result["coverage_modules"]["population"] == 0

    def test_single_process_kind_is_excluded(self, env):
        one_kind = _members(("defines", "defines", "defines"))
        session = FakeSession(
            own=[_module("m-own", DOC, members=one_kind)],
            twins=[_module("m-twin", OTHER_DOC, members=one_kind)],
        )
        assert _run(session)["module_links_created"] == 0

    def test_generic_steps_do_not_count_as_process_kinds(self, env):
        members = _members(("defines", "defines")) + _members(("requires_review",), generic=True)
        session = FakeSession(
            own=[_module("m-own", DOC, members=members)],
            twins=[_module("m-twin", OTHER_DOC, members=members)],
        )
        assert _run(session)["module_links_created"] == 0

    def test_not_identity_eligible_is_excluded(self, env):
        session = FakeSession(
            own=[_module("m-own", DOC, eligible=False)], twins=[_module("m-twin", OTHER_DOC)]
        )
        assert _run(session)["module_links_created"] == 0

    def test_old_rule_version_is_excluded(self, env):
        session = FakeSession(
            own=[_module("m-own", DOC, rule_version="m0")], twins=[_module("m-twin", OTHER_DOC)]
        )
        assert _run(session)["module_links_created"] == 0

    def test_inner_modules_are_excluded(self, env):
        session = FakeSession(
            own=[_module("m-own", DOC, level="inner")], twins=[_module("m-twin", OTHER_DOC)]
        )
        assert _run(session)["module_links_created"] == 0

    def test_twin_that_fails_the_floor_is_not_linked(self, env):
        session = FakeSession(
            own=[_module("m-own", DOC)],
            twins=[_module("m-twin", OTHER_DOC, members=_members(("defines", "substitutes")))],
        )
        assert _run(session)["module_links_created"] == 0

    def test_floor_constants_are_code_constants(self):
        assert tm_schema.MODULE_IDENTITY_MIN_MEMBERS == 3
        assert tm_schema.MODULE_IDENTITY_MIN_PROCESS_KINDS == 2


# ---------------------------------------------------------------------------
# 3. 再提案しない / 再利用する
# ---------------------------------------------------------------------------


class TestIdempotence:
    def test_dismissed_structure_is_not_proposed_again(self, env):
        key = library_schema.build_structural_candidate_key(FP)
        env["entries_by_candidate_key"][key] = {
            "id": "entry-old", "review_status": library_schema.REVIEW_STATUS_DISMISSED,
        }
        session = FakeSession(own=[_module("m-own", DOC)], twins=[_module("m-twin", OTHER_DOC)])
        result = _run(session)
        assert result["module_links_created"] == 0
        assert env["created_entries"] == []

    def test_already_linked_module_is_not_proposed_again(self, env):
        session = FakeSession(
            own=[_module("m-own", DOC)],
            twins=[_module("m-twin", OTHER_DOC)],
            linked_modules=["m-own"],
        )
        assert _run(session)["module_links_created"] == 0

    def test_existing_candidate_entry_is_reused(self, env):
        key = library_schema.build_structural_candidate_key(FP)
        env["entries_by_candidate_key"][key] = {
            "id": "entry-old", "review_status": library_schema.REVIEW_STATUS_CANDIDATE,
        }
        session = FakeSession(
            own=[_module("m-own", DOC)],
            twins=[_module("m-twin", OTHER_DOC)],
            linked_modules=["m-twin"],
        )
        result = _run(session)
        assert result["module_entries_created"] == 0
        assert [link["ref"].element_id for link in env["created_links"]] == ["m-own"]
        assert env["created_links"][0]["shared_part_id"] == "entry-old"


# ---------------------------------------------------------------------------
# 4. 文面に論文の中身・指紋を入れない（TM12 / TM15）
# ---------------------------------------------------------------------------


class TestNoPaperContentInCandidateText:
    def test_candidate_key_hides_the_fingerprint(self):
        key = library_schema.build_structural_candidate_key(FP)
        assert FP not in key
        assert "ops=" not in key
        digest = hashlib.sha256(FP.encode("utf-8")).hexdigest()[:32]
        assert key == f"cand|tm|{digest}"

    def test_candidate_key_rejects_an_empty_fingerprint(self):
        with pytest.raises(ValueError):
            library_schema.build_structural_candidate_key("  ")

    def test_entry_name_and_reason_carry_no_paper_content(self, env):
        session = FakeSession(
            own=[_module("m-own", DOC)],
            twins=[_module("m-twin", OTHER_DOC, visual=PARTNER_VISUAL)],
        )
        _run(session)
        name = env["created_entries"][0]["name"]
        assert name == "定義・代入の構造"
        for text in [name] + [link["reason"] for link in env["created_links"]]:
            assert "重力" not in text and "散乱" not in text
            assert OWN_VISUAL not in text and PARTNER_VISUAL not in text
            assert "ops=" not in text and RULE + "|" not in text
        assert {link["reason"] for link in env["created_links"]} == {ic.STRUCTURAL_LINK_REASON}

    def test_entry_name_falls_back_without_verbs(self):
        assert ic.structural_entry_name([]) == ic.STRUCTURAL_ENTRY_FALLBACK_NAME
        assert ic.structural_entry_name(["定義", "定義", " "]) == "定義の構造"


# ---------------------------------------------------------------------------
# 5. 上限（別枠）と coverage_modules
# ---------------------------------------------------------------------------


class TestLimits:
    def test_module_limit_zero_stops_only_rule_five(self, env):
        env["settings"] = _settings(component_limit=20, module_limit=0)
        session = FakeSession(own=[_module("m-own", DOC)], twins=[_module("m-twin", OTHER_DOC)])
        result = _run(session)
        assert result["module_links_created"] == 0
        assert "module_candidate_limit_is_zero" in result["coverage_modules"]["reasons"]
        assert "candidate_limit_is_zero" not in result["coverage"]["reasons"]

    def test_component_limit_zero_does_not_stop_rule_five(self, env):
        env["settings"] = _settings(component_limit=0, module_limit=10)
        session = FakeSession(own=[_module("m-own", DOC)], twins=[_module("m-twin", OTHER_DOC)])
        result = _run(session)
        assert result["module_links_created"] == 2
        assert result["module_entries_created"] == 1

    def test_budget_truncates_partners_and_reports_coverage(self, env):
        env["settings"] = _settings(module_limit=3)  # entry 1 + own 1 + partner 1
        session = FakeSession(
            own=[_module("m-own", DOC)],
            twins=[_module("m-a", OTHER_DOC), _module("m-b", THIRD_DOC)],
        )
        result = _run(session)
        assert result["module_entries_created"] + result["module_links_created"] == 3
        assert [link["ref"].element_id for link in env["created_links"]] == ["m-own", "m-a"]
        coverage = result["coverage_modules"]
        assert coverage["unit"] == "theory_modules"
        assert "module_candidate_limit" in coverage["reasons"]
        assert coverage["truncated"] >= 1

    def test_coverage_population_counts_eligible_outer_rows(self, env):
        session = FakeSession(
            own=[_module("m-own", DOC), _module("m-small", DOC, eligible=False)],
            twins=[],
        )
        result = _run(session)
        assert result["coverage_modules"]["population"] == 1
        assert result["coverage_modules"]["truncated"] == 0


# ---------------------------------------------------------------------------
# 6. 読むのは live ビューだけ（KO5 の作法）
# ---------------------------------------------------------------------------


class TestReadsLiveViewOnly:
    @pytest.mark.parametrize(
        "path",
        [
            BACKEND / "core" / "library" / "identity_candidates.py",
            BACKEND / "core" / "library" / "structural_matches.py",
            BACKEND / "core" / "theory_modules" / "related.py",
            BACKEND / "api" / "routes" / "library.py",
        ],
    )
    def test_sources_never_touch_the_base_table(self, path):
        import re

        src = path.read_text(encoding="utf-8")
        assert not re.search(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+knowledge_theory_modules\b(?!_live)", src)

    def test_rule_five_queries_use_the_live_view(self, env):
        session = FakeSession(own=[_module("m-own", DOC)], twins=[_module("m-twin", OTHER_DOC)])
        _run(session)
        module_sql = [sql for sql in session.statements if "knowledge_theory_modules" in sql]
        assert module_sql and all("knowledge_theory_modules_live" in sql for sql in module_sql)

    def test_core_theory_modules_related_has_no_sql(self):
        src = (BACKEND / "core" / "theory_modules" / "related.py").read_text(encoding="utf-8")
        for term in ("import sqlalchemy", "from sqlalchemy", "sa_text", "execute(", "import fastapi",
                     "from fastapi"):
            assert term not in src, term


# ---------------------------------------------------------------------------
# 7. related（§13.7）
# ---------------------------------------------------------------------------


def _own(mid, *, fingerprint=FP, eligible=True, rule_version=RULE, level="outer", key=None):
    return {
        "id": mid,
        "agent_module_key": key or f"{rule_version}:{mid}",
        "rule_version": rule_version,
        "level": level,
        "structure_fingerprint": fingerprint,
        "identity_eligible": eligible,
    }


def _match(mid, document_id, fingerprint=FP):
    return {"id": mid, "document_id": document_id, "structure_fingerprint": fingerprint}


def _payload(own_rows, matches, *, decisions=None, can_view=lambda d: True, titles=None):
    return tm_related.build_related_payload(
        DOC,
        own_rows=own_rows,
        matches=matches,
        decisions=decisions or {},
        rule_version=RULE,
        can_view=can_view,
        titles=titles or {OTHER_DOC: "Paper B", THIRD_DOC: "Paper C"},
        candidate_key_for=library_schema.build_structural_candidate_key,
    )


def _walk(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


class TestRelatedPayload:
    def test_not_saved(self):
        body = _payload([], [])
        assert body["available"] is False
        assert body["facts"] == [tm_related.FACT_RELATED_NOT_SAVED]
        assert body["modules"] == []

    def test_rule_version_mismatch(self):
        body = _payload([_own("m1", rule_version="m0")], [])
        assert body["available"] is False
        assert body["facts"] == [tm_related.FACT_RELATED_RULE_VERSION_STALE]

    def test_lists_titles_of_viewable_documents_only(self):
        body = _payload(
            [_own("m1", key="k1"), _own("m2", fingerprint=FP_OTHER, eligible=False, key="k2")],
            [_match("x1", OTHER_DOC), _match("x2", OTHER_DOC), _match("x3", THIRD_DOC)],
            can_view=lambda d: d == OTHER_DOC,
        )
        assert body["available"] is True
        assert body["modules"] == [
            {"module_key": "k1", "documents": [{"title": "Paper B"}]},
            {"module_key": "k2", "documents": []},
        ]
        assert body["hidden"] is True
        assert body["facts"] == [tm_related.FACT_RELATED_HIDDEN]

    def test_inner_rows_are_not_listed(self):
        body = _payload([_own("m1", key="k1"), _own("i1", level="inner", key="ki")], [])
        assert [m["module_key"] for m in body["modules"]] == ["k1"]

    def test_untitled_document_is_not_silently_dropped(self):
        body = _payload([_own("m1")], [_match("x1", OTHER_DOC)], titles={OTHER_DOC: ""})
        assert body["modules"][0]["documents"] == [{"title": tm_related.UNTITLED_DOCUMENT_LABEL}]

    def test_dismissed_structure_hides_all_partners(self):
        key = library_schema.build_structural_candidate_key(FP)
        body = _payload(
            [_own("m1")], [_match("x1", OTHER_DOC)],
            decisions={key: {"dismissed": True, "rejected_module_ids": set()}},
        )
        assert body["modules"][0]["documents"] == []
        assert body["hidden"] is False

    def test_rejected_own_link_hides_all_partners(self):
        key = library_schema.build_structural_candidate_key(FP)
        body = _payload(
            [_own("m1")], [_match("x1", OTHER_DOC)],
            decisions={key: {"dismissed": False, "rejected_module_ids": {"m1"}}},
        )
        assert body["modules"][0]["documents"] == []

    def test_rejected_partner_link_hides_only_that_partner(self):
        key = library_schema.build_structural_candidate_key(FP)
        body = _payload(
            [_own("m1")], [_match("x1", OTHER_DOC), _match("x2", THIRD_DOC)],
            decisions={key: {"dismissed": False, "rejected_module_ids": {"x1"}}},
        )
        assert body["modules"][0]["documents"] == [{"title": "Paper C"}]

    def test_no_numbers_or_fingerprints_in_the_response(self):
        body = _payload(
            [_own("m1")], [_match("x1", OTHER_DOC), _match("x2", THIRD_DOC)],
            can_view=lambda d: d == OTHER_DOC,
        )
        for key, value in _walk(body):
            assert key not in tm_schema.FORBIDDEN_KEYS, key
            assert key not in {"status", "review_status", "candidate_key"}, key
            assert not (isinstance(value, (int, float)) and not isinstance(value, bool)), key
            if isinstance(value, str):
                assert "ops=" not in value and FP not in value

    def test_inputs_are_not_mutated(self):
        import copy

        own = [_own("m1")]
        matches = [_match("x1", OTHER_DOC)]
        before = copy.deepcopy((own, matches))
        _payload(own, matches)
        assert (own, matches) == before


class _RelatedSession:
    def __init__(self, *, own=(), matches=(), decisions=(), titles=()):
        self.own = list(own)
        self.matches = list(matches)
        self.decisions = list(decisions)
        self.titles = list(titles)
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "knowledge_theory_modules_live" in sql and "document_id <> " in sql:
            rows = self.matches
        elif "knowledge_theory_modules_live" in sql:
            rows = self.own
        elif "FROM library_entries" in sql:
            rows = self.decisions
        elif "FROM documents" in sql:
            rows = self.titles
        else:
            rows = []
        return _FakeResult(rows)


class TestRelatedForDocument:
    def test_reads_and_assembles(self):
        key = library_schema.build_structural_candidate_key(FP)
        session = _RelatedSession(
            own=[("m1", f"{RULE}:m1", RULE, "outer", FP, True)],
            matches=[("x1", OTHER_DOC, FP), ("x2", THIRD_DOC, FP)],
            decisions=[(key, "candidate", "x2", "rejected")],
            titles=[(OTHER_DOC, "Paper B")],
        )
        body = structural_matches.build_related_for_document(
            session, DOC, rule_version=RULE, can_view=lambda d: True
        )
        assert body["modules"] == [{"module_key": f"{RULE}:m1", "documents": [{"title": "Paper B"}]}]
        assert body["hidden"] is False
        assert all(
            "knowledge_theory_modules_live" in sql
            for sql in session.statements
            if "knowledge_theory_modules" in sql
        )

    def test_titles_are_fetched_only_for_viewable_documents(self):
        session = _RelatedSession(
            own=[("m1", f"{RULE}:m1", RULE, "outer", FP, True)],
            matches=[("x1", OTHER_DOC, FP)],
        )
        body = structural_matches.build_related_for_document(
            session, DOC, rule_version=RULE, can_view=lambda d: False
        )
        assert not any("FROM documents" in sql for sql in session.statements)
        assert body["modules"][0]["documents"] == []
        assert body["hidden"] is True

    def test_no_saved_rows_issues_no_matching_query(self):
        session = _RelatedSession()
        body = structural_matches.build_related_for_document(
            session, DOC, rule_version=RULE, can_view=lambda d: True
        )
        assert body["available"] is False
        assert len(session.statements) == 1


# ---------------------------------------------------------------------------
# 8. route（TEACHER・可視性 fail-closed）
# ---------------------------------------------------------------------------

try:
    import fastapi  # noqa: F401

    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

_TEACHER_ID = "99999999-9999-9999-9999-999999999999"
_STUDENT_ID = "88888888-8888-8888-8888-888888888888"
_RELATED = f"/api/admin/documents/{DOC}/theory-modules/related"


@pytest.fixture
def client_and_tokens():
    from fastapi.testclient import TestClient
    from main import app
    from dependencies import ROLE_STUDENT, ROLE_TEACHER, _create_token

    client = TestClient(app)
    student = _create_token(_STUDENT_ID, "stu", "stu@x", ROLE_STUDENT)
    teacher = _create_token(_TEACHER_ID, "tea", "tea@x", ROLE_TEACHER)
    return client, student, teacher


@pytest.fixture
def related_env(monkeypatch):
    from routes import library as library_routes
    from routes import theory_components as tc

    session = _RelatedSession(
        own=[("m1", f"{RULE}:m1", RULE, "outer", FP, True)],
        matches=[("x1", OTHER_DOC, FP), ("x2", THIRD_DOC, FP)],
        titles=[(OTHER_DOC, "Paper B"), (THIRD_DOC, "Paper C")],
    )
    session.close = lambda: None
    monkeypatch.setattr(tc, "_ensure_document_viewable", lambda document_id, user: [])
    monkeypatch.setattr(tc, "_resolve_document", lambda ref: {"id": DOC})
    monkeypatch.setattr(tc, "_pg_session", lambda: session)

    class _Access:
        def __init__(self, can_view):
            self.can_view = can_view

    def _resolve_access(uid, document_id):
        if document_id == THIRD_DOC:
            raise RuntimeError("cannot decide")  # 判定できないものは見せない（fail-closed）
        return _Access(document_id == OTHER_DOC)

    monkeypatch.setattr(library_routes.services, "resolve_document_access", _resolve_access)
    return session


@pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")
class TestRelatedRoute:
    def test_requires_teacher(self, client_and_tokens, related_env):
        client, student, _teacher = client_and_tokens
        response = client.get(_RELATED, headers={"Authorization": "Bearer " + student})
        assert response.status_code == 403

    def test_visibility_is_fail_closed(self, client_and_tokens, related_env):
        client, _student, teacher = client_and_tokens
        body = client.get(_RELATED, headers={"Authorization": "Bearer " + teacher}).json()
        assert body["available"] is True
        assert body["modules"] == [{"module_key": f"{RULE}:m1", "documents": [{"title": "Paper B"}]}]
        assert body["hidden"] is True
        assert body["facts"] == [tm_related.FACT_RELATED_HIDDEN]
        assert "Paper C" not in str(body)

    def test_unknown_document_says_not_saved(self, client_and_tokens, related_env, monkeypatch):
        from routes import theory_components as tc

        monkeypatch.setattr(tc, "_resolve_document", lambda ref: None)
        client, _student, teacher = client_and_tokens
        body = client.get(_RELATED, headers={"Authorization": "Bearer " + teacher}).json()
        assert body["available"] is False
        assert body["facts"] == [tm_related.FACT_RELATED_NOT_SAVED]

    def test_route_is_get_only_and_writes_nothing(self):
        from tests.guardrail_helpers import extract_function_source

        src = (BACKEND / "api" / "routes" / "theory_components.py").read_text(encoding="utf-8")
        assert '@router.get("/documents/{document_id}/theory-modules/related"' in src
        body = extract_function_source(src, "get_document_theory_modules_related")
        for term in ("INSERT", "UPDATE", "DELETE", "commit(", "_record_review_event", "generate_"):
            assert term not in body, term
        assert "_ensure_document_viewable" in body
        assert "_document_access_checker" in body


# ---------------------------------------------------------------------------
# 9. 学習者向けの読み手（§13.8 末尾）
# ---------------------------------------------------------------------------


class TestLearnerReadersIgnoreModuleLinks:
    def test_journey_listing_excludes_theory_module_links(self, monkeypatch):
        from core.deliberation import identity_links
        from core.personal_graph import queries

        monkeypatch.setattr(
            identity_links, "list_for_shared_part",
            lambda shared_part_id: [
                {"status": "confirmed", "instance_element_type": "theory_component",
                 "instance_document_id": OTHER_DOC},
                {"status": "confirmed", "instance_element_type": "theory_module",
                 "instance_document_id": THIRD_DOC},
            ],
        )
        rows = queries.fetch_confirmed_links_for_shared_part("entry-1")
        assert [row["instance_document_id"] for row in rows] == [OTHER_DOC]

    def test_nearby_thread_never_matches_a_module_link(self):
        from core.personal_graph import nearby

        link = {"instance_element_type": "theory_module", "instance_element_id": "m1"}
        node = {"component_id": "m1", "member_component_ids": ["m1"], "linked_claim_ids": ["m1"]}
        assert nearby._link_matches_node(link, node) is False

    def test_theory_module_is_not_a_dialogue_target(self):
        from core.deliberation import schema as dschema

        assert dschema.ELEMENT_THEORY_MODULE in dschema.IDENTITY_LINKABLE_ELEMENT_TYPES
        assert dschema.ELEMENT_THEORY_MODULE not in dschema.DIALOGUE_SESSION_ELEMENT_TYPES

    def test_refs_resolve_rejects_theory_module(self):
        from core.deliberation import refs
        from core.deliberation.schema import ElementResolutionError

        with pytest.raises(ElementResolutionError) as excinfo:
            refs.resolve("theory_module", "m1", document_id=DOC)
        assert excinfo.value.kind == "invalid"
