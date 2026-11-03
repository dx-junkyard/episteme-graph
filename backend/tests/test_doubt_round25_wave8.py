"""第 25 周 面 B 第 8 波（IK-1057 / IK-1058 / IK-1059 / IK-1060 / IK-0901 の支持線の段名）の固定。

- IK-1057: 観測の根の同定は、式から合成した主張（origin=equation_synthesis）をどの段でも観測と
  数えず、段（B）による代理の同定は主張自身の種類が背景（prior_work）なら採らない。fixture は
  Cepheus TeX の実 artifact の形（主グラフの集約ノード「Diagnostic / application」が背景文・
  式由来の合成主張・観測の解釈をまとめて linked_claim_ids に持ち、式の手順が合成主張を参照する）。
- IK-0901: 支持線の事実文の崩れる対象は、主グラフの英語の段名を日本語の段名で言う。
- IK-1058: 手で記帳した前提（load_score NULL）を、前提の地図・教員の未検証合意リスト・台帳で
  「計算できない」段の事実文として出す（黙って落とさない・数値なし）。
- IK-1059: 学習者向け台帳の一行と画面文脈の地図パネルの行は、地図と同じ学習者の語の正本を通る。
- IK-1060: 疑義の行に誰が・いつ（取り下げ済みなら取り下げの日時と取り下げた人）。
"""

from __future__ import annotations

import inspect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(_BACKEND), str(_BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.doubt as doubt  # noqa: E402
import routes.learning as learning_routes  # noqa: E402
from core import label_vocab  # noqa: E402
from core.doubt import observation_targets as ot  # noqa: E402
from core.doubt import open_assumptions as oa  # noqa: E402
from core.doubt import population  # noqa: E402
from core.doubt import support_paths as sp  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_ATLAS_JS = (_ROOT / "frontend" / "public" / "js" / "doubt-atlas.js").read_text(encoding="utf-8")
_APP_JS = (_ROOT / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")
_PM_HOME_JS = (_ROOT / "frontend" / "public" / "js" / "personal-map-home.js").read_text(encoding="utf-8")
_ADMIN_HTML = (_ROOT / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")

_DOC = "1771872a-0000-4000-8000-000000000000"
_C_BG = "044965aa-0000-4000-8000-000000000001"     # prior_work（序論の背景文）
_C_SYN = "3ef1002f-0000-4000-8000-000000000002"    # equation_synthesis（式由来の合成主張）
_C_OBS = "6564fd95-0000-4000-8000-000000000003"    # interpretation（観測の解釈）
_C_DEF = "a1b2c3d4-0000-4000-8000-000000000004"    # observable_definition（C 段）
_C_TGT = "42c4e2e8-0000-4000-8000-000000000005"    # 対象（$\ddot I$ depends on …・合成主張）


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


# 実 artifact の形（Cepheus TeX 1771872a の読み取りで見た構造を縮めたもの）:
#   - 主グラフ: theory_op_0001「Theory basis」← theory_op_0005「Diagnostic / application」
#     （main→main の資格付きエッジ。集約ノードは成員の主張をまとめて linked_claim_ids に持つ）
#   - 式の詳細層: eq_op_0001（合成主張 _C_SYN を参照）→ eq_op_0004（対象 _C_TGT を参照）
#   修正前は 根 = 集約ノード（観測の解釈・背景文で）+ eq_op_0001（合成主張で）の 2 本 =「複数」。
_GRAPH = {
    "nodes": [
        {"component_id": "theory_op_0001", "graph_layer": "main", "label": "Theory basis",
         "linked_claim_ids": [_C_TGT]},
        {"component_id": "theory_op_0005", "graph_layer": "main", "label": "Diagnostic / application",
         "linked_claim_ids": [_C_BG, _C_SYN, _C_OBS]},
        {"component_id": "eq_op_0001", "graph_layer": "equation_detail", "label": "eq step 1",
         "linked_claim_ids": [_C_SYN]},
        {"component_id": "eq_op_0004", "graph_layer": "equation_detail", "label": "eq step 4",
         "linked_claim_ids": [_C_TGT]},
    ],
    "edges": [
        {"source": "theory_op_0005", "target": "theory_op_0001", "source_backing_status": "source_backed"},
        {"source": "eq_op_0001", "target": "eq_op_0004", "source_backing_status": "source_backed"},
    ],
}
_KINDS = {
    _C_BG: ("claim_object", "prior_work"),
    _C_SYN: ("equation_synthesis", "unknown"),
    _C_OBS: ("claim_object", "interpretation"),
    _C_DEF: ("atomic_rewrite", "observable_definition"),
    _C_TGT: ("equation_synthesis", "dependency_claim"),
}


class _ArtifactSession:
    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "FROM theory_component_graphs" in sql:
            if "AS document_id" in sql:
                return _Rows([(_DOC, json.dumps(_GRAPH))])
            return _Rows([(json.dumps(_GRAPH),)])
        if "claim_type = ANY" in sql:
            return _Rows([(_C_DEF, _DOC)])
        if "COALESCE(origin" in sql:
            ids = params.get("ids") or []
            return _Rows([(i, *_KINDS[i]) for i in ids if i in _KINDS])
        if "COALESCE(NULLIF(normalized_text" in sql:
            ids = params.get("ids") or []
            return _Rows([(i, f"本文 {i[:8]}") for i in ids if i in _KINDS])
        return _Rows([])

    def rollback(self):
        pass


class TestIk1057ObservationRoots:
    def test_synthesis_and_stage_proxied_background_are_not_observations(self):
        items = ot.observation_claim_targets(_ArtifactSession(), document_ids=[_DOC])
        ids = {i["claim_id"]: i["identified_via"] for i in items}
        assert _C_SYN not in ids          # 式由来の合成主張は観測の記録ではない
        assert _C_BG not in ids           # 段の代理で拾った背景文（prior_work）は採らない
        assert ids.get(_C_OBS) == "theory_stage"   # 段で拾った観測の解釈は残る
        assert ids.get(_C_DEF) == "claim_type"     # C 段は主張自身の分類なので残る

    def test_kind_unknown_claims_are_kept_fail_soft(self):
        assert ot._counts_as_observation(None, "theory_stage") is True
        assert ot._counts_as_observation(("claim_object", "prior_work"), "claim_type") is True
        assert ot._counts_as_observation(("equation_synthesis", "dependency_claim"), "dsl_measures") is False

    def test_support_line_on_artifact_shape_no_longer_counts_synthesis_line(self, monkeypatch):
        monkeypatch.setattr(population, "claim_sequence_ids_for_payload", lambda *_a: set())
        ctx = sp.build_support_context(_ArtifactSession(), document_ids=[_DOC])
        lines = sp.compute_support_lines_from_context(ctx, "claim", _C_TGT)
        assert lines is not None
        root_ids = {r["claim_id"] for r in lines["observation_roots"]}
        assert _C_SYN not in root_ids and _C_BG not in root_ids
        # 合成主張の線（eq_op_0001 → eq_op_0004）が抜け、集約ノードの 1 本だけ（実データの読み取りと同じ形）
        assert lines["level"] == "single"
        assert "『診断・応用』" in lines["fact_line"]   # IK-0901: 英語の段名を出さない
        assert "Diagnostic" not in lines["fact_line"]


class TestIk0901SupportLineStageName:
    def test_cut_member_label_uses_japanese_stage_name(self):
        assert sp._readable_member_label("Diagnostic / application") == "診断・応用"
        assert sp._readable_member_label("Theory basis") == "理論の土台"
        # 段名でないラベルはそのまま
        assert sp._readable_member_label("観測の手順") == "観測の手順"


class TestIk1060ChallengeRecordLine:
    def test_withdrawn_line_has_both_times_and_withdrawer(self):
        created = datetime(2026, 10, 27, 13, 5, tzinfo=timezone.utc)
        withdrawn = datetime(2026, 10, 27, 22, 40, tzinfo=timezone.utc)
        line = label_vocab.challenge_record_line("岡村 千尋", created, "withdrawn", withdrawn)
        assert line == (
            "記帳: 岡村 千尋（2026-10-27 22:05） ／ 取り下げ: 岡村 千尋（2026-10-28 07:40・記帳者本人）"
        )

    def test_open_line_has_only_recorded_and_unknown_time_is_fact(self):
        assert label_vocab.challenge_record_line("A", None, "open") == "記帳: A（日時の記録なし）"
        assert "取り下げ" not in label_vocab.challenge_record_line("A", "2026-10-27T00:00:00", "open")

    def test_ledger_get_selects_updated_at_and_returns_record_line(self):
        src = inspect.getsource(doubt)
        assert "c.challenge_mode, c.target_element_ref, c.updated_at" in src
        assert '"record_line": challenge_record_line(' in src
        assert "data-doubt-withdrawn-record-line" in _ATLAS_JS
        assert "c.record_line" in _ATLAS_JS


class _OpenSession:
    """compile_open_assumptions の最小フェイク（手で記帳した前提 1 件・load_score NULL）。"""

    def __init__(self):
        self.ledger_sql = ""

    def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM epistemic_ledger" in sql and "verification_scopes" in sql:
            self.ledger_sql = sql
            return _Rows([
                ("as-1", "assumption", "untested", [{"condition": "c"}], 0, None, [], _DOC),
                ("as-2", "assumption", "untested", [], 0, None, [], _DOC),
            ])
        if "FROM assumption_nodes" in sql:
            return _Rows([("as-1",)])
        if "COUNT(*)" in sql:
            return _Rows([(0,)])
        return _Rows([])

    def rollback(self):
        pass


class _NoExclusion:
    excluded = False

    def excludes(self, *_a):
        return False


class TestIk1058UncomputableAssumptions:
    def _run(self, monkeypatch, **kw):
        monkeypatch.setattr(oa, "load_percentiles", lambda *_a, **_k: (1.0, 2.0, 3.0))
        monkeypatch.setattr(oa, "build_support_context", lambda *_a, **_k: None)
        monkeypatch.setattr(oa, "has_naive_signal", lambda *_a, **_k: False)
        import core.doubt.target_labels as tl

        class _Labels:
            def __init__(self, *_a):
                pass

            def label(self, *_a, **_k):
                return "磁場の乱れが小さい"

        monkeypatch.setattr(tl, "LedgerTargetLabels", _Labels)
        session = _OpenSession()
        items = oa.compile_open_assumptions(
            session, "c1", document_ids=[_DOC], exclusion=_NoExclusion(), **kw
        )
        return session, items

    def test_default_keeps_high_load_meaning(self, monkeypatch):
        session, items = self._run(monkeypatch)
        assert items == []
        assert "load_score IS NOT NULL" in session.ledger_sql

    def test_teacher_list_shows_confirmed_manual_assumption_as_uncomputable(self, monkeypatch):
        _session, items = self._run(monkeypatch, include_uncomputable_assumptions=True)
        # 確定済みの as-1 だけ（as-2 は assumption_nodes で確定でない）
        assert [i["target_id"] for i in items] == ["as-1"]
        assert items[0]["load_level"] == ""
        assert items[0]["load_note"] == label_vocab.ASSUMPTION_LOAD_UNCOMPUTABLE_NOTE

    def test_routes_wire_the_uncomputable_stage(self):
        src = inspect.getsource(doubt)
        assert "include_uncomputable_assumptions=True" in src
        assert "(l.load_score IS NOT NULL OR l.target_type = 'assumption')" in src
        assert '"unplotted_points": unplotted' in src
        assert '{"load_note": ASSUMPTION_LOAD_UNCOMPUTABLE_NOTE}' in src
        assert "data-doubt-atlas-unplotted" in _ATLAS_JS
        assert "data-doubt-load-note" in _ATLAS_JS
        assert "doubt-atlas.js?v=round25-doubt-" in _ADMIN_HTML  # 第 9 波で doubt9 に上がる

    def test_note_has_no_numbers(self):
        import re

        for text in (label_vocab.ASSUMPTION_LOAD_UNCOMPUTABLE_NOTE, label_vocab.ASSUMPTION_ATLAS_UNPLOTTED_HEADING):
            assert not re.search(r"\d", text)


class TestIk1059LearnerWording:
    def test_learner_ledger_line_passes_learner_wording(self):
        line = doubt._learner_wording({
            "fact_line": "この内容の検証スコープはまだ記帳されていません。",
            "target_note": "台帳の行",
            "scopes": [{"condition": "記帳"}],
        })
        for key in ("fact_line", "target_note"):
            for word in label_vocab.ATLAS_LEARNER_WORDING_DENYLIST:
                assert word not in line[key]
        # スコープの本文（教員の記帳の中身）は書き換えない
        assert line["scopes"] == [{"condition": "記帳"}]

    def test_unrecorded_line_is_learner_wording(self):
        class _S:
            def execute(self, *_a, **_k):
                return _Rows([])

        out = doubt._learner_unrecorded_line(_S(), "claim", "x", document_id=None)
        for word in label_vocab.ATLAS_LEARNER_WORDING_DENYLIST:
            assert word not in out["fact_line"]

    def test_screen_context_atlas_panel_lines_use_learner_wording(self):
        src = inspect.getsource(learning_routes._screen_atlas_node_sources)
        assert 'out["verify_line"] = label_vocab.learner_atlas_wording(' in src
        assert "label_vocab.learner_atlas_wording(\n                        _atlas_state.learner_endorse_line(" in src

    def test_learner_js_surfaces_do_not_say_kicho(self):
        assert "検証スコープの記帳なし" not in _APP_JS
        assert "検証: 記帳あり" not in _APP_JS
        assert "記帳スコープ" not in _APP_JS
        assert "検証の記帳がある" not in _PM_HOME_JS
