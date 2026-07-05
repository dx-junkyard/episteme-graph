"""分野の地図 — 状態導出バッチ (Issue E-1) core.atlas_state のユニットテスト。

- 導出規則 (§10) を §12 フィクスチャの15ノードを期待値として検証 (受け入れ条件1)
- 検証行・承認行に評価語が含まれないことの静的チェック (受け入れ条件6)
- 論文取り込みで fog → 灯り (受け入れ条件2 の core 部分)
- 同一入力での再実行が同一の行を返す (受け入れ条件3)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import atlas  # noqa: E402
from core import atlas_state as st  # noqa: E402


# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------


def _skeleton(**overrides) -> atlas.AtlasSkeleton:
    """凍結済みの小さな骨格 (バンドル版 particle_physics と同じ構成の縮約)。"""
    regions = (
        atlas.SkeletonRegion(
            id="eft_sumrules",
            label="有効場理論・QCD和則",
            layout=atlas.RegionLayout(x=0.06, y=0.1, w=0.53, h=0.62),
            concepts=(
                atlas.SkeletonConcept(
                    id="ope",
                    label="演算子積展開",
                    layout=atlas.ConceptLayout(x=0.22, y=0.39),
                    seed_status=atlas.SeedStatus(value="verified", reviewed=True),
                ),
                atlas.SkeletonConcept(
                    id="dual",
                    label="クォーク・ハドロン双対性",
                    layout=atlas.ConceptLayout(x=0.72, y=0.8),
                    seed_status=atlas.SeedStatus(value="assumed", reviewed=True),
                ),
                atlas.SkeletonConcept(
                    id="bc_sumrule",
                    label="B→c 和則",
                    layout=atlas.ConceptLayout(x=0.75, y=0.39),
                ),
            ),
        ),
        atlas.SkeletonRegion(
            id="lattice_qcd",
            label="格子QCD",
            layout=atlas.RegionLayout(x=0.06, y=0.8, w=0.42, h=0.16),
            concepts=(),
        ),
    )
    fields = dict(
        cartridge="particle_physics",
        status=atlas.STATUS_FROZEN,
        version="2026.1",
        generated_by="model:test",
        reviewed_by=("faculty:test",),
        changelog=(atlas.ChangelogEntry(version="2026.1", note="test"),),
        regions=regions,
        edges=(),
        concept_bindings=(),
    )
    fields.update(overrides)
    return atlas.AtlasSkeleton(**fields)


def _signals(**kw) -> st.ConceptSignals:
    return st.ConceptSignals(key=kw.pop("key", "n"), **kw)


# ---------------------------------------------------------------------------
# 導出規則 (§10 / §16-1 / §16-2)
# ---------------------------------------------------------------------------


class TestDeriveNodeStatus:
    def test_gap_takes_precedence(self):
        status, source = st.derive_node_status(
            _signals(inferred_without_source=True, confirmed_assumption=True)
        )
        assert status == st.STATUS_GAP
        assert source == st.STATUS_SOURCE_DERIVED

    def test_assumed_over_contested(self):
        """§16-1 の確定: assumed と contested の併発は assumed 優先。"""
        status, _ = st.derive_node_status(
            _signals(confirmed_assumption=True, personal_shared_count=2)
        )
        assert status == st.STATUS_ASSUMED

    def test_inferred_accumulation_threshold(self):
        """§16-2 の確定: inferred の蓄積 3件で assumed 候補へ昇格。"""
        below, _ = st.derive_node_status(
            _signals(inferred_count=st.ASSUMED_INFERRED_THRESHOLD - 1)
        )
        at, _ = st.derive_node_status(
            _signals(inferred_count=st.ASSUMED_INFERRED_THRESHOLD)
        )
        assert below != st.STATUS_ASSUMED
        assert at == st.STATUS_ASSUMED

    def test_direct_verification_blocks_assumed(self):
        status, _ = st.derive_node_status(
            _signals(
                confirmed_assumption=True,
                direct_verification=True,
                evidence_count=2,
            )
        )
        assert status == st.STATUS_VERIFIED

    def test_contested_by_interpretations(self):
        status, _ = st.derive_node_status(
            _signals(personal_shared_count=2, evidence_count=1)
        )
        assert status == st.STATUS_CONTESTED

    def test_contested_by_endorsement_split(self):
        status, _ = st.derive_node_status(
            _signals(endorsement_split=True, evidence_count=1, endorser_count=2)
        )
        assert status == st.STATUS_CONTESTED

    def test_verified_needs_evidence(self):
        status, _ = st.derive_node_status(_signals(evidence_count=1))
        assert status == st.STATUS_VERIFIED

    def test_seed_applies_only_without_corpus_state(self):
        """seed は弱い初期表示。コーパス状態が付いたら上書きされる。"""
        seeded, source = st.derive_node_status(_signals(seed_status="assumed"))
        assert (seeded, source) == (st.STATUS_ASSUMED, st.STATUS_SOURCE_SEED)
        overridden, source = st.derive_node_status(
            _signals(seed_status="assumed", evidence_count=2, direct_verification=True)
        )
        assert (overridden, source) == (st.STATUS_VERIFIED, st.STATUS_SOURCE_DERIVED)

    def test_unknown_default(self):
        status, _ = st.derive_node_status(_signals())
        assert status == st.STATUS_UNKNOWN

    def test_region_fog_and_lit(self):
        assert st.derive_region_status(0) == st.STATUS_FOG
        assert st.derive_region_status(1) == st.STATUS_LIT


# ---------------------------------------------------------------------------
# 受け入れ条件1: §12 フィクスチャの15ノードを期待値とした導出
# (既存データを模したシグナル → 同じ状態・検証行・承認行)
# ---------------------------------------------------------------------------

# 各フィクスチャノードの「既存データを模したテストデータ」(シグナル) と期待状態。
# now/unvisited/glow は個人層 (API 実行時合成) の軸なので、ここでは台帳状態を検証する。
_FIXTURE_CASES = {
    # now (B→c 和則): 台帳上は verified (原文3本・式(12)・§3.2)
    "now": (
        _signals(
            label="B→c 和則",
            component_count=1,
            evidence_count=3,
            evidence_refs=("式(12)", "§3.2", "式(14)"),
            direct_verification=True,
            standard_explanation=True,
            endorser_count=3,
            strong_count=1,
            expertise_breadth=2,
        ),
        st.STATUS_VERIFIED,
    ),
    "ope": (
        _signals(
            label="演算子積展開",
            component_count=1,
            evidence_count=2,
            direct_verification=True,
            scope_text="高エネルギー・摂動領域",
            standard_explanation=True,
            personal_shared_count=1,
            author_labels=("A先生",),
        ),
        st.STATUS_VERIFIED,
    ),
    "disp": (
        _signals(
            label="分散関係",
            component_count=1,
            evidence_count=2,
            direct_verification=True,
            standard_explanation=True,
            endorser_count=2,
        ),
        st.STATUS_VERIFIED,
    ),
    "qft": (
        _signals(
            label="場の量子論の基礎",
            component_count=1,
            evidence_count=1,
            direct_verification=True,
            standard_explanation=True,
        ),
        st.STATUS_VERIFIED,
    ),
    # dual: 記帳された直接検証なし + 確定された前提候補 + 行間の蓄積 → assumed
    "dual": (
        _signals(
            label="クォーク・ハドロン双対性",
            component_count=1,
            evidence_count=1,
            confirmed_assumption=True,
            inferred_count=3,
            dependent_count=5,
        ),
        st.STATUS_ASSUMED,
    ),
    # vcb: 2つの解釈が帰属つきで並存 → contested
    "vcb": (
        _signals(
            label="Vcb の測定間の不一致",
            component_count=1,
            evidence_count=2,
            direct_verification=True,
            personal_shared_count=2,
            author_labels=("A先生", "B先生"),
        ),
        st.STATUS_CONTESTED,
    ),
    # spec / borel: 台帳は verified (未訪問・隣接の光は個人層)
    "spec": (
        _signals(label="スペクトル密度", component_count=1, evidence_count=1,
                 direct_verification=True, standard_explanation=True),
        st.STATUS_VERIFIED,
    ),
    "borel": (
        _signals(label="ボレル変換", component_count=1, evidence_count=1,
                 direct_verification=True, standard_explanation=True),
        st.STATUS_VERIFIED,
    ),
}


class TestFixtureParity:
    @pytest.mark.parametrize("node_id", list(_FIXTURE_CASES))
    def test_status_matches_fixture(self, node_id):
        signals, expected = _FIXTURE_CASES[node_id]
        status, _ = st.derive_node_status(signals)
        assert status == expected

    def test_fog_regions(self):
        # fogL / fogH: コーパス被覆 0 → fog。定型文は §6.2 のとおり。
        assert st.derive_region_status(0) == st.STATUS_FOG
        assert st.FOG_VERIFY_LINE == "この領域はAIの一般知識による地形。台帳に根拠の記帳がありません。"
        assert st.FOG_ENDORSE_LINE == "論文を追加すると、この領域に灯りがつきます。"

    def test_verify_lines_carry_ledger_facts(self):
        signals, _ = _FIXTURE_CASES["now"]
        line = st.verify_line_for(st.STATUS_VERIFIED, signals)
        assert "原文3本に裏付け" in line
        assert "式(12)" in line and "§3.2" in line and "ほか" in line

        signals, _ = _FIXTURE_CASES["ope"]
        line = st.verify_line_for(st.STATUS_VERIFIED, signals)
        assert "スコープ: 高エネルギー・摂動領域" in line

        signals, _ = _FIXTURE_CASES["dual"]
        line = st.verify_line_for(st.STATUS_ASSUMED, signals)
        assert "記帳された直接検証なし" in line
        assert "5件の導出が依存" in line

    def test_endorse_lines_carry_c_layer_facts(self):
        signals, _ = _FIXTURE_CASES["now"]
        assert st.endorse_line_for(st.STATUS_VERIFIED, signals) == (
            "承認: 強い支持 — 3名の教員が承認（専門2分野）"
        )
        signals, _ = _FIXTURE_CASES["ope"]
        assert st.endorse_line_for(st.STATUS_VERIFIED, signals) == (
            "承認: 標準の説明とA先生の説明が並存"
        )
        signals, _ = _FIXTURE_CASES["disp"]
        assert st.endorse_line_for(st.STATUS_VERIFIED, signals) == "承認: 2名の教員が承認"
        signals, _ = _FIXTURE_CASES["dual"]
        assert st.endorse_line_for(st.STATUS_ASSUMED, signals) == (
            "承認: 承認記録なし — 台帳のスコープ欄は空欄"
        )
        signals, _ = _FIXTURE_CASES["vcb"]
        assert st.endorse_line_for(st.STATUS_CONTESTED, signals) == (
            "承認: 2つの解釈が帰属つきで並存"
        )

    def test_gap_step_lines(self):
        # gy (行間): L3 導出チェーンの inferred ステップ
        snapshot = st.CorpusSnapshot(
            graphs=[
                {
                    "document_id": "doc-1",
                    "graph": {
                        "nodes": [
                            {"id": "d1", "graph_layer": "main", "label": "相関関数を定義",
                             "stage": "theory_basis", "source_backing_status": "source_backed",
                             "linked_equation_ids": ["eq_3"]},
                            {"id": "d2", "graph_layer": "main", "label": "OPE で展開",
                             "stage": "equation_system", "source_backing_status": "source_backed",
                             "linked_equation_ids": ["eq_5", "eq_8"]},
                            {"id": "gy", "graph_layer": "main", "label": "高次項の抑制",
                             "stage": "elimination", "source_backing_status": "inferred"},
                            {"id": "d4", "graph_layer": "main", "label": "和則（整合関係）",
                             "stage": "consistency_relation",
                             "source_backing_status": "source_backed",
                             "linked_equation_ids": ["eq_12"]},
                            {"id": "d5", "graph_layer": "main", "label": "Vcb の抽出へ",
                             "stage": "diagnostic_application",
                             "source_backing_status": "partially_source_backed"},
                            # equation_detail / debug 層は L3 チェーンに出さない
                            {"id": "detail", "graph_layer": "equation_detail",
                             "source_backing_status": "source_backed"},
                        ]
                    },
                }
            ]
        )
        chain = st.build_derivation_chain(snapshot)
        assert [c["id"] for c in chain] == ["step_d1", "step_d2", "step_gy", "step_d4", "step_d5"]
        gy = chain[2]
        assert gy["status"] == st.STATUS_GAP
        assert gy["verify"] == st.GAP_VERIFY_LINE
        assert gy["endorse"] == st.GAP_ENDORSE_LINE
        assert chain[0]["status"] == st.STATUS_VERIFIED
        assert "eq_12" in chain[3]["verify"]


# ---------------------------------------------------------------------------
# 受け入れ条件6: 評価語の静的チェック
# ---------------------------------------------------------------------------


class TestEvaluativeVocabulary:
    def test_finder(self):
        assert st.find_evaluative_language("この概念は重要です") == ["重要"]
        assert st.find_evaluative_language("原文2本に裏付け") == []

    def test_all_generated_lines_are_free_of_evaluative_words(self):
        """フィクスチャ相当の全シグナル + 定型文で評価語ゼロを保証する。"""
        lines = [st.FOG_VERIFY_LINE, st.FOG_ENDORSE_LINE, st.GAP_VERIFY_LINE, st.GAP_ENDORSE_LINE]
        for signals, expected in _FIXTURE_CASES.values():
            lines.append(st.verify_line_for(expected, signals))
            lines.append(st.endorse_line_for(expected, signals))
        # 未知・seed のパスも通す
        empty = _signals(seed_status="assumed")
        lines.append(st.verify_line_for(st.STATUS_ASSUMED, empty))
        lines.append(st.verify_line_for(st.STATUS_UNKNOWN, _signals()))
        lines.append(st.endorse_line_for(st.STATUS_UNKNOWN, _signals()))
        for line in lines:
            assert st.find_evaluative_language(line) == [], line


# ---------------------------------------------------------------------------
# 既存データ近似からのシグナル集計
# ---------------------------------------------------------------------------


def _snapshot_with_corpus() -> st.CorpusSnapshot:
    return st.CorpusSnapshot(
        components=[
            {
                "id": "comp-ope",
                "name": "演算子積展開",
                "document_id": "doc-1",
                "review_status": "teacher_review_required",
                "status": "draft",
                "evidence_claims": ["claim-1"],
                "source_scope": {"scope": "高エネルギー・摂動領域"},
            },
            {
                "id": "comp-extra",
                "name": "ボレル変換の応用",
                "document_id": "doc-1",
                "review_status": "teacher_review_required",
                "status": "draft",
                "evidence_claims": [],
                "source_scope": {},
            },
        ],
        claims=[
            {
                "id": "claim-1",
                "document_id": "doc-1",
                "claim_type": "equation_definition",
                "concepts": ["演算子積展開"],
                "equation": {"eq_no": "5"},
                "support_status": "source_backed",
                "evidence_text": "OPE expansion ...",
                "review_status": "teacher_review_required",
                "source_scope": {"section": "2"},
            },
            {
                "id": "claim-2",
                "document_id": "doc-1",
                "claim_type": "assumption",
                "concepts": ["クォーク・ハドロン双対性"],
                "equation": {},
                "support_status": "source_backed",
                "evidence_text": "duality is assumed ...",
                "review_status": "teacher_approved",
                "source_scope": {},
            },
        ],
        graphs=[],
        explanations=[
            {
                "component_id": "comp-ope",
                "explanation_id": "exp-1",
                "kind": "standard",
                "shared": False,
                "review_status": "teacher_review_required",
                "author_name": "",
                "endorser_count": 2,
                "strong_count": 0,
                "expertise_breadth": 1,
            }
        ],
        vectors={"comp-ope": [1.0, 0.0], "comp-extra": [0.9, 0.1]},
    )


class TestBuildConceptSignals:
    def test_label_match_and_evidence(self):
        signals = st.build_concept_signals(
            _snapshot_with_corpus(), key="ope", label="演算子積展開"
        )
        assert signals.component_count == 1
        assert signals.evidence_count == 1
        assert "式(5)" in signals.evidence_refs
        assert "§2" in signals.evidence_refs
        assert signals.direct_verification is True
        assert signals.scope_text == "高エネルギー・摂動領域"
        assert signals.endorser_count == 2
        status, _ = st.derive_node_status(signals)
        assert status == st.STATUS_VERIFIED

    def test_assumption_only_concept_is_assumed(self):
        signals = st.build_concept_signals(
            _snapshot_with_corpus(), key="dual", label="クォーク・ハドロン双対性"
        )
        assert signals.confirmed_assumption is True
        assert signals.direct_verification is False
        status, _ = st.derive_node_status(signals)
        assert status == st.STATUS_ASSUMED

    def test_no_match_has_no_corpus_state(self):
        signals = st.build_concept_signals(
            _snapshot_with_corpus(), key="x", label="存在しない概念"
        )
        assert signals.has_corpus_state is False


# ---------------------------------------------------------------------------
# compute_overlay_rows: fog → 灯り (受け入れ条件2) と再現性 (受け入れ条件3)
# ---------------------------------------------------------------------------


class TestComputeOverlayRows:
    def _region_status(self, rows, region_id):
        return next(
            r["status"] for r in rows if r["entry_type"] == "region" and r["entry_id"] == region_id
        )

    def test_fog_before_ingest_then_lit_after(self):
        """論文取り込み前は fog、取り込み後の差分バッチで灯りがつく。"""
        skeleton = _skeleton()
        before = st.compute_overlay_rows(
            skeleton, st.CorpusSnapshot(), cartridge_id="particle_physics"
        )
        assert self._region_status(before, "eft_sumrules") == st.STATUS_FOG
        assert self._region_status(before, "lattice_qcd") == st.STATUS_FOG

        after = st.compute_overlay_rows(
            skeleton, _snapshot_with_corpus(), cartridge_id="particle_physics"
        )
        assert self._region_status(after, "eft_sumrules") == st.STATUS_LIT
        # コーパスが触れていない領域は霧のまま
        assert self._region_status(after, "lattice_qcd") == st.STATUS_FOG

    def test_seed_status_used_only_before_corpus(self):
        skeleton = _skeleton()
        before = {r["entry_id"]: r for r in st.compute_overlay_rows(
            skeleton, st.CorpusSnapshot(), cartridge_id="particle_physics"
        ) if r["entry_type"] == "node"}
        # コーパスなし → reviewed シードが弱い初期表示
        assert before["ope"]["status"] == st.STATUS_VERIFIED
        assert before["ope"]["status_source"] == st.STATUS_SOURCE_SEED
        assert before["dual"]["status"] == st.STATUS_ASSUMED
        # seed なし・コーパスなし → unknown
        assert before["bc_sumrule"]["status"] == st.STATUS_UNKNOWN

        after = {r["entry_id"]: r for r in st.compute_overlay_rows(
            skeleton, _snapshot_with_corpus(), cartridge_id="particle_physics"
        ) if r["entry_type"] == "node"}
        # コーパス状態が付いたら seed を上書き (derived に変わる)
        assert after["ope"]["status_source"] == st.STATUS_SOURCE_DERIVED
        assert after["dual"]["status"] == st.STATUS_ASSUMED
        assert after["dual"]["status_source"] == st.STATUS_SOURCE_DERIVED

    def test_corpus_concept_is_placed_by_embedding(self):
        """E-2: 骨格に紐づかない component は埋め込みで領域へ配置される。"""
        rows = st.compute_overlay_rows(
            _skeleton(), _snapshot_with_corpus(), cartridge_id="particle_physics"
        )
        placed = next(r for r in rows if r["entry_id"] == "comp_comp-extra")
        assert placed["placement"]["method"] == "embedding"
        assert placed["region_id"] == "eft_sumrules"
        layout = placed["layout"]
        assert 0.06 <= layout["x"] <= 0.59
        assert 0.1 <= layout["y"] <= 0.72

    def test_reproducibility(self):
        """受け入れ条件3: 同一入力での再実行が同一の配置・状態を返す。"""
        skeleton = _skeleton()
        rows1 = st.compute_overlay_rows(
            skeleton, _snapshot_with_corpus(), cartridge_id="particle_physics"
        )
        rows2 = st.compute_overlay_rows(
            skeleton, _snapshot_with_corpus(), cartridge_id="particle_physics"
        )
        assert rows1 == rows2

    def test_all_rows_free_of_evaluative_words(self):
        rows = st.compute_overlay_rows(
            _skeleton(), _snapshot_with_corpus(), cartridge_id="particle_physics"
        )
        for row in rows:
            assert st.find_evaluative_language(row["verify_line"]) == [], row["verify_line"]
            assert st.find_evaluative_language(row["endorse_line"]) == [], row["endorse_line"]


# ---------------------------------------------------------------------------
# refresh_overlay_cache: SQL 実行 (FakeSession) とキャッシュ差し替え
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeSession:
    def __init__(self, router=None):
        self.calls = []
        self.router = router or (lambda sql, params: FakeResult())

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, params or {}))
        return self.router(sql, params or {})


class TestRefreshOverlayCache:
    def test_refresh_replaces_cache_and_clears_dirty(self):
        session = FakeSession()
        summary = st.refresh_overlay_cache(
            session, _skeleton(), cartridge_id="particle_physics"
        )
        sqls = [sql for sql, _ in session.calls]
        assert any("DELETE FROM atlas_overlay_cache" in s for s in sqls)
        assert any("INSERT INTO atlas_overlay_cache" in s for s in sqls)
        assert any("DELETE FROM atlas_overlay_dirty" in s for s in sqls)
        assert summary["skeleton_version"] == "2026.1"
        assert summary["regions"] == 2
        # meta 行 (コーパス署名) が書かれている
        meta = [
            p for s, p in session.calls
            if "INSERT INTO atlas_overlay_cache" in s and p.get("entry_type") == "meta"
        ]
        assert len(meta) == 1
        assert "signature" in json.loads(meta[0]["evidence"])

    def test_mark_dirty_is_idempotent_upsert(self):
        session = FakeSession()
        st.mark_overlay_dirty(session, "particle_physics", reason="paper_ingested")
        sql, params = session.calls[0]
        assert "ON CONFLICT (cartridge_id)" in sql
        assert params["reason"] == "paper_ingested"

    def test_is_stale_when_dirty(self):
        session = FakeSession(
            lambda sql, p: FakeResult([(1,)]) if "atlas_overlay_dirty" in sql else FakeResult()
        )
        assert st.is_overlay_stale(session, "particle_physics", "2026.1") is True

    def test_is_stale_when_signature_changes(self):
        meta = json.dumps({"signature": "old"})

        def route(sql, params):
            if "atlas_overlay_dirty" in sql:
                return FakeResult()
            if "entry_type = 'meta'" in sql:
                return FakeResult([(meta,)])
            return FakeResult([("2026-07-01", "", "", "", "")])

        assert st.is_overlay_stale(FakeSession(route), "particle_physics", "2026.1") is True

    def test_not_stale_when_signature_matches(self):
        signature = "2026-07-01||||"
        meta = json.dumps({"signature": signature})

        def route(sql, params):
            if "atlas_overlay_dirty" in sql:
                return FakeResult()
            if "entry_type = 'meta'" in sql:
                return FakeResult([(meta,)])
            return FakeResult([("2026-07-01", "", "", "", "")])

        assert st.is_overlay_stale(FakeSession(route), "particle_physics", "2026.1") is False


# ---------------------------------------------------------------------------
# topic ↔ 骨格概念 binding の DB 解決 (個人層 binding の整備)
# ---------------------------------------------------------------------------


class TestResolveCourseCartridge:
    def test_explicit_cartridge_id_wins(self):
        # 明示指定は session を使わない (None でも解決できる)
        assert st.resolve_course_cartridge(None, {"cartridge_id": "chem"}) == "chem"

    def test_derived_from_sources(self):
        def route(sql, params):
            if "document_analysis_runs" in sql:
                assert params["material_ids"] == ["m1", "m2"]
                return FakeResult([("bio", 3)])
            return FakeResult()

        cd = {"sources": [{"material_id": "m1"}, {"material_id": "m2"}]}
        assert st.resolve_course_cartridge(FakeSession(route), cd) == "bio"

    def test_fallback_to_default_when_no_material(self):
        # sources 無し → 既定カートリッジ (Settings.default_cartridge_id) へ縮退
        assert st.resolve_course_cartridge(None, {"sources": []}) == "particle_physics"


class TestCourseHasSkeletonAnchor:
    """導出カートリッジの妥当性ゲート (gap3 hardening)。

    解析パイプラインは既定カートリッジで走るため、導出だけでは別分野のコース
    (例: 修正重力理論) にも particle_physics が返る。骨格へ足がかりが無ければ
    False → atlas_view が 404 (骨格なし扱い) に縮退させる。
    """

    def test_false_for_unrelated_course(self):
        """別分野コース: どのトピックも骨格概念にラベル一致せず、チャンクも無い → False。"""
        cd = {
            "topics": [
                {"id": "t1", "title": "修正重力理論のテストとしての大規模構造"},
                {"id": "t2", "title": "局所バイアス模型の導入"},
            ]
        }
        assert st.course_has_skeleton_anchor(None, _skeleton(), "particle_physics", cd) is False

    def test_true_via_label_match(self):
        """topic.title が骨格概念にラベル一致 → DB 不要で True。"""
        cd = {"topics": [{"id": "t1", "title": "演算子積展開"}]}
        assert st.course_has_skeleton_anchor(None, _skeleton(), "particle_physics", cd) is True

    def test_true_via_explicit_binding(self):
        """topic.atlas_node_id の明示 binding → True。"""
        cd = {"topics": [{"id": "t1", "title": "何でもよい題目", "atlas_node_id": "ope"}]}
        assert st.course_has_skeleton_anchor(None, _skeleton(), "particle_physics", cd) is True

    def test_true_via_corpus_binding(self):
        """ラベル不一致でも教材チャンク → component → 骨格概念で True。"""
        sk = _skeleton()

        def route(sql, params):
            if "primary_chunk_id IN" in sql:
                assert sorted(params["chunk_ids"]) == ["ch1", "ch2"]
                return FakeResult([("comp-ope", "演算子積展開")])
            if "evidence_claims" in sql:  # _COMPONENTS_SQL (load_corpus_snapshot)
                return FakeResult(
                    [("comp-ope", "演算子積展開", "d", "", "draft", [], {})]
                )
            return FakeResult()

        cd = {
            "topics": [
                {"id": "t1", "title": "無関係な題目", "material_chunk_ids": ["ch1"]},
                {"id": "t2", "title": "別の題目", "material_chunk_ids": ["ch2"]},
            ]
        }
        assert st.course_has_skeleton_anchor(FakeSession(route), sk, "particle_physics", cd) is True

    def test_false_when_components_do_not_bind(self):
        """component はあるが骨格概念へ対応しない (別分野の component) → False。"""
        sk = _skeleton()

        def route(sql, params):
            if "primary_chunk_id IN" in sql:
                return FakeResult([("comp-x", "DHOST 係数の読み方")])
            if "evidence_claims" in sql:
                return FakeResult(
                    [("comp-x", "DHOST 係数の読み方", "d", "", "draft", [], {})]
                )
            return FakeResult()

        cd = {"topics": [{"id": "t1", "title": "無関係な題目", "material_chunk_ids": ["ch1"]}]}
        assert (
            st.course_has_skeleton_anchor(FakeSession(route), sk, "particle_physics", cd) is False
        )

    def test_false_without_session_or_course(self):
        assert st.course_has_skeleton_anchor(None, _skeleton(), "particle_physics", None) is False
        assert st.course_has_skeleton_anchor(None, None, "particle_physics", {}) is False


class TestBuildComponentConceptMap:
    def _snapshot(self):
        def comp(cid, name, status="draft", review=""):
            return {
                "id": cid,
                "name": name,
                "status": status,
                "review_status": review,
                "document_id": "d",
                "evidence_claims": [],
                "source_scope": {},
            }

        return st.CorpusSnapshot(
            components=[
                comp("comp-ope", "演算子積展開"),           # binding で ope
                comp("comp-dual", "クォーク・ハドロン双対性"),  # 名前一致で dual
                comp("comp-rej", "演算子積展開", status="rejected"),  # 除外
            ]
        )

    def test_maps_via_binding_and_name(self):
        sk = _skeleton(
            concept_bindings=(
                atlas.ConceptBinding(skeleton="ope", graph_concept_id="comp-ope", reviewed=True),
            )
        )
        m = st.build_component_concept_map(self._snapshot(), sk)
        assert m["comp-ope"] == "ope"       # reviewed binding
        assert m["comp-dual"] == "dual"     # 正規化名一致 (中黒を無視)
        assert "comp-rej" not in m          # rejected は除外

    def test_unreviewed_binding_ignored(self):
        sk = _skeleton(
            concept_bindings=(
                atlas.ConceptBinding(skeleton="bc_sumrule", graph_concept_id="comp-ope", reviewed=False),
            )
        )
        m = st.build_component_concept_map(self._snapshot(), sk)
        # 未レビュー binding は無効。comp-ope は名前一致で ope になる
        assert m.get("comp-ope") == "ope"


class TestResolveTopicConceptViaCorpus:
    def test_none_without_chunk_ids(self):
        out = st.resolve_topic_concept_via_corpus(
            FakeSession(), _skeleton(), "particle_physics", {"id": "t"}
        )
        assert out is None

    def test_resolves_via_component_name(self):
        sk = _skeleton()

        def route(sql, params):
            if "primary_chunk_id IN" in sql:
                return FakeResult([("comp-ope", "演算子積展開")])
            if "evidence_claims" in sql:  # _COMPONENTS_SQL (load_corpus_snapshot)
                return FakeResult(
                    [("comp-ope", "演算子積展開", "d", "", "draft", [], {})]
                )
            return FakeResult()  # claims / graphs / explanations / vectors は空

        topic = {"id": "t", "material_chunk_ids": ["ch1", "ch2"]}
        out = st.resolve_topic_concept_via_corpus(
            FakeSession(route), sk, "particle_physics", topic
        )
        assert out == "ope"

    def test_none_when_no_components_match(self):
        def route(sql, params):
            if "primary_chunk_id IN" in sql:
                return FakeResult([])  # トピックに紐づく component 無し
            return FakeResult()

        topic = {"id": "t", "material_chunk_ids": ["ch1"]}
        out = st.resolve_topic_concept_via_corpus(
            FakeSession(route), _skeleton(), "particle_physics", topic
        )
        assert out is None
