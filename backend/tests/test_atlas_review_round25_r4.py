"""第 25 周 面 C（分野の地図の運用）第 4 周の是正（IK-0962〜IK-0967 + IK-0677 / 0887 / 0942 の残り）。

- IK-0962: 関係の採用で画面が出所を送る。記録の無い過去の採用は「いまの導出では」と時点を明示して補う。
- IK-0963: 凍結の影響が加わる項目を「名前（親領域）」で列挙する（件数なし）。
- IK-0964: 札「未訪問」「いまここ」の説明は訪問の軸で言い、台帳の状態は検証行だけが言う。
- IK-0965: 索引の状態は再構築と同じ材料の指紋で要否を言い、変わった項目を名前で出す。
- IK-0966: 外れた判断の区画の冒頭は定義の文。凍結を止める一文は未取り込みの行があるときだけ。
- IK-0967 / IK-0677: 配置の理由に「AIの要約」の印、引用に出所の印、引用に無い固有の語は一文で。
- IK-0942: 学習者の地図に「骨格」を出さない（フッター・検証行）。
- IK-0887: 学習者の糸ごとに根拠の一文（組の名前・数値なし）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import atlas_lifecycle  # noqa: E402
from core import atlas_review_display as display  # noqa: E402
from core import atlas_state as st  # noqa: E402
from core import label_vocab  # noqa: E402
from core.atlas_edges import threads  # noqa: E402
from core.landscape import projection, schema as ls_schema  # noqa: E402

JS = ROOT / "frontend" / "public" / "js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# IK-0962
# ---------------------------------------------------------------------------


class TestEdgeOriginWired:
    def test_admin_sends_origin_on_accept(self):
        src = _read(JS / "admin.js")
        block = src[src.index("var body = { edge_key: edgeKey, action: action"):][:900]
        assert 'action === "accept"' in block
        assert 'body.origin = "co_occurrence"' in block
        assert 'body.origin = "vector"' in block
        # 共起を優先する
        assert block.index('"co_occurrence"') < block.index('body.origin = "vector"')

    def test_recorded_basis_wins_over_derived(self):
        items = display.pending_freeze_items(
            None,
            edge_items=[{"edge_key": "k1"}],
            edge_label=lambda _i: "A — B",
            edge_origins={"k1": ["co_occurrence"]},
            edge_derived_origins={"k1": ["vector"]},
        )
        assert items[0]["basis_line"] == display.EDGE_BASIS_CO_OCCURRENCE

    def test_unrecorded_falls_back_to_derived_with_time_marker(self):
        items = display.pending_freeze_items(
            None,
            edge_items=[{"edge_key": "k1"}],
            edge_label=lambda _i: "A — B",
            edge_origins={},
            edge_derived_origins={"k1": ["vector"]},
        )
        line = items[0]["basis_line"]
        assert line.startswith("採用の時点の根拠は記録されていません。いまの導出では: ")
        assert display.EDGE_BASIS_VECTOR in line

    def test_no_record_no_derivation_stays_none(self):
        items = display.pending_freeze_items(
            None,
            edge_items=[{"edge_key": "k1"}],
            edge_label=lambda _i: "A — B",
        )
        assert items[0]["basis_line"] == display.PENDING_EDGE_BASIS_NONE


# ---------------------------------------------------------------------------
# IK-0963
# ---------------------------------------------------------------------------


def _skeleton():
    return SimpleNamespace(
        regions=[
            SimpleNamespace(
                id="ism", label="星間物質",
                concepts=[SimpleNamespace(id="ism_mag", label="星間磁場"),
                          SimpleNamespace(id="dust", label="星間塵")],
            ),
            SimpleNamespace(id="new_r", label="新領域", concepts=[]),
        ]
    )


class TestFreezeImpactNamesAddedNodes:
    def test_labels_with_parent_region(self):
        labels = atlas_lifecycle.added_node_labels(_skeleton(), {"ism_mag", "new_r"})
        assert labels == ["星間磁場（星間物質）", "新領域（領域）"]

    def test_fact_lists_names_without_counts(self):
        facts = atlas_lifecycle._freeze_impact_facts(
            set(), {"ism_mag"}, has_frozen=True, added_nodes=["星間磁場（星間物質）"]
        )
        assert "次版で加わる項目: 星間磁場（星間物質）" in facts
        assert atlas_lifecycle.FACT_ADDED_NODES_NEED_REANALYSIS in facts

    def test_first_freeze_does_not_list_everything(self):
        facts = atlas_lifecycle._freeze_impact_facts(
            set(), {"ism_mag"}, has_frozen=False, added_nodes=["星間磁場（星間物質）"]
        )
        assert not any(f.startswith("次版で加わる項目") for f in facts)


# ---------------------------------------------------------------------------
# IK-0964
# ---------------------------------------------------------------------------


class TestPillNoteAxis:
    def test_unvisited_note_speaks_of_visit_not_ledger(self):
        note = st.pill_note_for("unvisited", "unvisited")
        assert "まだこの場所から学んでいません" in note
        assert "地図で選ぶだけでは数えず" in note
        assert "台帳" not in note

    def test_now_note(self):
        assert "いま学習している場所" in st.pill_note_for("now", "now")

    def test_route_uses_display_axis(self):
        src = _read(BACKEND / "api" / "routes" / "atlas_view.py")
        assert 'if display_status in ("unvisited", "now"):' in src


# ---------------------------------------------------------------------------
# IK-0965
# ---------------------------------------------------------------------------


class TestVectorStatusSameCondition:
    def test_changed_labels_override_current(self):
        facts = display.vector_status_facts(
            version="2026.1", total_nodes=3, embedded_nodes=3, stale=False,
            changed_labels=["宇宙膨張"],
        )
        joined = "".join(facts)
        assert "作り直す必要はありません" not in joined
        assert "宇宙膨張" in joined

    def test_unchanged_stays_current(self):
        facts = display.vector_status_facts(
            version="2026.1", total_nodes=3, embedded_nodes=3, stale=False,
        )
        assert facts[-1] == display.VECTOR_INDEX_CURRENT.format(version="2026.1")

    def test_refresh_fact_names_items(self):
        fact = display.vector_refresh_fact(
            {"status": "completed", "embedded": 1, "embedded_node_ids": ["cmb"]},
            {"cmb": "宇宙背景放射"},
        )
        assert fact == "索引を作り直しました。新しく索引した項目: 宇宙背景放射。"

    def test_status_and_refresh_share_the_hash_function(self):
        src = _read(BACKEND / "core" / "atlas_vectors" / "builder.py")
        assert src.count("_attach_source_hashes(session, domain, specs)") == 2


# ---------------------------------------------------------------------------
# IK-0966
# ---------------------------------------------------------------------------


class TestOrphanedNoteConditional:
    def test_all_in_draft_has_no_block_sentence(self):
        note = display.orphaned_note([{"in_draft": True}])
        assert note == display.ORPHANED_NOTE
        assert "凍結" not in note

    def test_not_in_draft_adds_block_sentence(self):
        assert display.orphaned_note([{"in_draft": False}]).endswith(
            display.ORPHANED_BLOCKS_FREEZE
        )

    def test_edge_variant(self):
        assert display.orphaned_note([{}], edge=True).endswith(
            display.EDGE_ORPHANED_BLOCKS_FREEZE
        )
        assert display.orphaned_note([{"in_draft": True}], edge=True) == display.EDGE_ORPHANED_NOTE


# ---------------------------------------------------------------------------
# IK-0967 / IK-0677
# ---------------------------------------------------------------------------


class TestReasonAndEvidenceMarks:
    def test_evidence_source(self):
        assert ls_schema.evidence_source_for("x", "c1") == "claim"
        assert ls_schema.evidence_source_for("BAO data", None, ["We use BAO data here"]) == "claim"
        assert ls_schema.evidence_source_for("中心の問い", None, []) == "paper_summary"

    def test_unrecorded_old_row_is_not_asserted_as_summary(self):
        label = ls_schema.evidence_source_label({"quote": "x"})
        assert label == label_vocab.LANDSCAPE_EVIDENCE_SOURCE_LABELS["unrecorded"]

    def test_reason_beyond_quote(self):
        note = ls_schema.reason_beyond_quote_note(
            "論文は BAO と Ia 型超新星の距離測定を用いて背景膨張史を制約している",
            ["constrained using DESI DR2 BAO measurements."],
        )
        assert "「Ia」" in note
        assert "BAO" not in note
        assert not ls_schema.reason_beyond_quote_note(
            "BAO で制約している", ["DESI DR2 BAO measurements"]
        )

    def test_learner_dto_carries_marks_without_claim_id(self):
        row = {
            "reason": "論文は BAO と Ia 型超新星を用いる",
            "status": "inferred",
            "evidence": [{"quote": "DESI DR2 BAO measurements", "claim_id": "c1", "source": "claim"}],
        }
        dto = projection._learner_placement_dto(row, {})
        assert dto["reason_label"] == label_vocab.LANDSCAPE_REASON_SOURCE_LABEL
        assert "「Ia」" in dto["reason_note"]
        assert dto["evidence"][0]["source_label"] == label_vocab.LANDSCAPE_EVIDENCE_SOURCE_LABELS["claim"]
        assert "claim_id" not in dto["evidence"][0]

    def test_frontends_render_marks_from_server(self):
        for name in ("landscape-layer.js", "app.js", "admin.js", "admin-release-review.js"):
            src = _read(JS / name)
            assert "reason_label" in src, name
            assert "reason_note" in src, name
            assert "source_label" in src, name
        # 文はサーバの値の素通し（JS に表を持たない）
        assert "AIの要約" not in _read(JS / "landscape-layer.js")


# ---------------------------------------------------------------------------
# IK-0942 / IK-0887
# ---------------------------------------------------------------------------


class TestLearnerAtlasWording:
    def test_seed_verify_line_has_no_skeleton_word(self):
        assert "骨格" not in st.SEED_VERIFY_LINE

    def test_overlay_footer_uses_version_prefix_mirror(self):
        src = _read(JS / "atlas-overlay.js")
        assert f'const ATLAS_VERSION_PREFIX = "{label_vocab.ATLAS_VERSION_PREFIX}";' in src
        assert '"骨格 " + (state.data.skeleton_version' not in src

    def test_threads_carry_basis_and_ui_renders_it(self):
        assert "basis_line" in threads._ITEM_KEYS
        src = _read(JS / "atlas-threads-layer.js")
        assert "item.basis_line" in src
        assert "糸ごとの根拠" in src
