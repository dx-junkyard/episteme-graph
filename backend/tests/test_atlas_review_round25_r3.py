"""第 25 周 面 C（分野の地図の運用）第 3 周の是正（IK-0937〜IK-0943）。

- IK-0937: 導出から外れた採用済みの判断に、通常の候補と同じ根拠（題名・逐語）を可視性ゲート付きで運ぶ。
- IK-0938: 判断済みを先頭の帯に（採用済み・未反映 → 下書きに入っている → 未判断）・反映の結果文に項目名。
- IK-0939: 凍結を止める採用の帰属・根拠・メモを止め理由と反映の確認へ。他人の採用を戻すときは理由必須。
- IK-0940: コースの地図 L1 の霧に、生きている論文の配置を数える（論文の海と同じ材料）。
- IK-0941: 霧の状態を領域の名前に焼かない。海の円は領域の名前を領域から引く。
- IK-0942: 版の呼び名は 1 形式（label_vocab.ATLAS_VERSION_PREFIX・JS は逐語ミラー）。
- IK-0943: 候補区画の説明は定義の文（候補ゼロでも矛盾しない）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import atlas as atlas_module  # noqa: E402
from core import atlas_lifecycle  # noqa: E402
from core import atlas_review_display as display  # noqa: E402
from core import atlas_state as st  # noqa: E402
from core import label_vocab  # noqa: E402
from core.atlas_gaps import schema as gap_schema  # noqa: E402
from core.atlas_gaps import store as gap_store  # noqa: E402

DOC_A = "aaaaaaaa-0000-0000-0000-000000000001"
DOC_B = "bbbbbbbb-0000-0000-0000-000000000002"
ACTOR = "11111111-1111-1111-1111-111111111111"
JS = ROOT / "frontend" / "public" / "js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    def __init__(self, handlers):
        self.handlers = list(handlers)

    def execute(self, statement, params=None):
        sql = str(statement)
        for needle, rows in self.handlers:
            if needle in sql:
                return FakeResult(rows)
        return FakeResult([])

    def rollback(self):
        pass


def _signal_row(signal_id, document_id, title, *, label="Interstellar magnetic field"):
    return (
        signal_id, document_id, None, "astro", "v1", "concept", "r1",
        label, gap_schema.normalize_label(label),
        "reason " + signal_id, "quote " + signal_id, 0.8, "active",
        "2026-10-22T00:00:0" + signal_id[-1], "2026-10-22T00:00:00",
        title, "", "",
    )


def _decision_row(cluster_key, *, status="accepted", note=""):
    return (
        "d-1", cluster_key, status, note, "", "", "",
        ACTOR, "2026-10-22T12:00:00+00:00", "2026-10-22T12:00:00+00:00",
        "2026-10-22T12:00:00+00:00",
    )


CLUSTER = gap_schema.build_cluster_key("astro", "r1", "Interstellar magnetic field")


# ---------------------------------------------------------------------------
# IK-0937 外れた判断に根拠を運ぶ
# ---------------------------------------------------------------------------


class TestOrphanedDecisionCarriesEvidence:
    def _session(self):
        return FakeSession(
            [
                ("FROM atlas_gap_decisions", [_decision_row(CLUSTER)]),
                ("FROM landscape_gap_signals", [_signal_row("s1", DOC_A, "論文 A")]),
            ]
        )

    def test_below_threshold_decision_has_documents_with_quote(self):
        items = gap_store.list_orphaned_decisions(
            self._session(),
            domain_key="astro",
            derived_cluster_keys=[],
            cluster_states={CLUSTER: "below_threshold"},
        )
        assert len(items) == 1
        item = items[0]
        assert item["reason"] == gap_store.ORPHAN_REASON_BELOW_THRESHOLD
        assert [d["title"] for d in item["documents"]] == ["論文 A"]
        assert item["documents"][0]["evidence_quote"] == "quote s1"
        # 生 confidence を運ばない（LS5）。
        assert "confidence" not in item["documents"][0]
        assert "documents_hidden" not in item

    def test_invisible_paper_is_dropped_and_only_the_fact_is_returned(self):
        items = gap_store.list_orphaned_decisions(
            self._session(),
            domain_key="astro",
            derived_cluster_keys=[],
            cluster_states={CLUSTER: "below_threshold"},
            visible_document_ids={DOC_B},
        )
        assert items[0]["documents"] == []
        assert items[0]["documents_hidden"] is True

    def test_gate_documents_is_fail_closed_and_counts_nothing(self):
        kept, hidden = gap_store.gate_documents(
            [{"document_id": DOC_A}, {"document_id": DOC_B}], set()
        )
        assert kept == [] and hidden is True
        kept, hidden = gap_store.gate_documents([{"document_id": DOC_A}], None)
        assert kept == [{"document_id": DOC_A}] and hidden is False

    def test_route_passes_the_visibility_gate(self):
        src = _read(BACKEND / "api" / "routes" / "atlas_gaps.py")
        assert "visible_document_ids=_visible_document_ids(current_user)" in src
        assert "services.list_visible_document_ids(" in src


# ---------------------------------------------------------------------------
# IK-0938 判断済みを先頭の帯に
# ---------------------------------------------------------------------------


class TestDecidedFirstOrdering:
    def test_bands_accepted_pending_then_in_draft_then_others(self):
        items = [
            {"key": "u1"},
            {"key": "d1", "decision": {"status": "accepted"}, "in": True},
            {"key": "p1", "decision": {"status": "accepted"}},
            {"key": "u2", "decision": {"status": "dismissed"}},
            {"key": "p2", "decision": {"status": "accepted"}},
        ]
        out = display.order_decided_first(items, lambda c: bool(c.get("in")))
        assert [c["key"] for c in out] == ["p1", "p2", "d1", "u1", "u2"]
        assert out[0]["pending_note"] == display.ACCEPTED_PENDING_NOTE
        assert "pending_note" not in out[2]

    def test_routes_order_before_capping(self):
        edges = _read(BACKEND / "api" / "routes" / "atlas_edges.py")
        assert edges.index("order_decided_first(") < edges.index("cap_for_review(")
        gaps = _read(BACKEND / "api" / "routes" / "atlas_gaps.py")
        assert "order_decided_first(" in gaps

    def test_result_line_names_the_item(self):
        assert display.INCORPORATED_EDGE_RESULT.format(label="A — B") == (
            "「A — B」を次版の下書きに追加しました。"
        )
        for name in ("atlas_edges.py", "atlas_gaps.py"):
            assert '"result_line"' in _read(BACKEND / "api" / "routes" / name)
        admin = _read(JS / "admin.js")
        assert "(marked && marked.result_line)" in admin


# ---------------------------------------------------------------------------
# IK-0939 凍結を止める採用の帰属と根拠
# ---------------------------------------------------------------------------


class TestPendingFreezeContext:
    def _session(self):
        return FakeSession([("FROM users", [(ACTOR, "鈴木")])])

    def test_items_carry_attribution_basis_and_memo(self):
        items = display.pending_freeze_items(
            self._session(),
            gap_items=[
                {
                    "proposed_label": "星間磁場",
                    "review_note": "",
                    "decision": {"status": "accepted", "decided_by": ACTOR,
                                 "decided_at": "2026-10-22T12:00:00+00:00"},
                    "documents": [{"document_id": DOC_A, "title": "論文 A"}],
                }
            ],
            edge_items=[
                {
                    "edge_key": "edge|astro|a|c",
                    "review_note": "同じ過程を扱う",
                    "decision": {"status": "accepted", "decided_by": ACTOR,
                                 "decided_at": "2026-10-22T12:00:00+00:00"},
                }
            ],
            edge_label=lambda item: "降着円盤 — 磁場",
            edge_origins={"edge|astro|a|c": ["vector"]},
            visible_document_ids={DOC_A},
        )
        gap, edge = items
        assert gap["kind"] == "gap" and gap["label"] == "星間磁場"
        assert "2026-10-22" in gap["decision_line"]
        assert "『論文 A』" in gap["basis_line"]
        assert gap["note_line"] == display.PENDING_NOTE_NONE
        assert edge["label"] == "降着円盤 — 磁場"
        assert edge["basis_line"] == display.EDGE_BASIS_VECTOR
        assert edge["note_line"] == "採用のメモ: 同じ過程を扱う"
        for item in items:
            assert ACTOR not in str(item)  # 判断者の UUID を出さない（IK-0848）

    def test_unknown_basis_is_said_not_invented(self):
        items = display.pending_freeze_items(
            FakeSession([]),
            gap_items=[{"proposed_label": "X", "decision": None, "documents": []}],
            edge_items=[{"edge_key": "k", "decision": None}],
            edge_label=lambda item: "A — B",
            visible_document_ids=set(),
        )
        assert items[0]["basis_line"] == display.PENDING_PAPERS_NONE
        assert items[1]["basis_line"] == display.PENDING_EDGE_BASIS_NONE

    def test_readiness_adds_items_without_changing_the_gate(self):
        out = atlas_lifecycle.freeze_readiness(["星間磁場"], [], [{"label": "星間磁場"}])
        assert out["ready"] is False
        assert out["pending_items"] == [{"label": "星間磁場"}]
        # 渡さなければ従来の形のまま。
        assert "pending_items" not in atlas_lifecycle.freeze_readiness([], [])

    def test_restore_of_another_teachers_accept_requires_a_reason(self):
        api_dir = str(BACKEND / "api")
        if api_dir not in sys.path:
            sys.path.insert(0, api_dir)
        from routes import atlas_gaps as route

        accepted = {"status": "accepted", "decided_by": ACTOR}
        assert route._overrides_other_teacher(accepted, "someone-else") is True
        assert route._overrides_other_teacher(accepted, ACTOR) is False
        assert route._overrides_other_teacher({"status": "dismissed", "decided_by": ACTOR}, "x") is False
        src = _read(BACKEND / "api" / "routes" / "atlas_gaps.py")
        assert "_DETAIL_OVERRIDE_REASON_REQUIRED" in src
        assert '"overrides_other_teacher"' in src
        assert '"overrides_other_teacher"' in _read(BACKEND / "api" / "routes" / "atlas_edges.py")

    def test_previews_state_the_timing(self):
        assert "公開（凍結）すると" in display.INCORPORATE_TIMING_EDGE
        for name in ("atlas_edges.py", "atlas_gaps.py"):
            src = _read(BACKEND / "api" / "routes" / name)
            assert '"timing_line"' in src and '"decision_line"' in src
        admin = _read(JS / "admin.js")
        assert "学習者には表示されません。\");" not in admin
        assert "preview.timing_line" in admin
        assert "readiness.pending_items" in admin
        assert "GAP_ACCEPT_MEMO_PROMPT" in admin and "EDGE_ACCEPT_MEMO_PROMPT" in admin


# ---------------------------------------------------------------------------
# IK-0940 / IK-0941 霧の判定と名前
# ---------------------------------------------------------------------------


def _skeleton():
    return atlas_module.AtlasSkeleton(
        cartridge="astro",
        status="frozen",
        version="v1",
        regions=(
            atlas_module.SkeletonRegion(
                id="r1", label="降着・ジェット",
                concepts=(atlas_module.SkeletonConcept(id="a", label="降着円盤"),),
            ),
            atlas_module.SkeletonRegion(
                id="r2", label="素粒子・場",
                concepts=(atlas_module.SkeletonConcept(id="c", label="磁場"),),
            ),
        ),
    )


class TestFogCountsPlacements:
    def _regions(self, snapshot):
        rows = st.compute_overlay_rows(_skeleton(), snapshot, cartridge_id="astro")
        return {r["entry_id"]: r for r in rows if r["entry_type"] == "region"}

    def test_placement_on_a_concept_lights_its_region(self):
        regions = self._regions(st.CorpusSnapshot(placed_node_ids={"a"}))
        assert regions["r1"]["status"] == st.STATUS_LIT
        assert regions["r1"]["verify_line"] == st.REGION_PLACED_ONLY_VERIFY_LINE
        # 配置は記帳の件数に足さない（真偽だけ — CR3）。
        assert regions["r1"]["evidence"]["coverage"] == 0
        assert regions["r2"]["status"] == st.STATUS_FOG

    def test_placement_on_the_region_itself_lights_it(self):
        regions = self._regions(st.CorpusSnapshot(placed_node_ids={"r2"}))
        assert regions["r2"]["status"] == st.STATUS_LIT

    def test_snapshot_reads_the_same_statuses_as_the_sea(self):
        src = _read(BACKEND / "core" / "atlas_state.py")
        assert "LEARNER_VISIBLE_STATUSES" in src
        assert "FROM landscape_placements lp" in src  # 署名にも入る（キャッシュの陳腐化）

    def test_fog_state_is_not_baked_into_the_region_name(self):
        view = _read(BACKEND / "api" / "routes" / "atlas_view.py")
        assert "（霧の領域）" not in view
        sea = _read(JS / "corpus-sea.js")
        body = sea[sea.index("function anchorLabel("):]
        body = body[: body.index("\n  }\n") + 4]
        assert body.index("regionById[anchorId]") < body.index("posById[anchorId]")


# ---------------------------------------------------------------------------
# IK-0942 版の呼び名 / IK-0943 区画の説明
# ---------------------------------------------------------------------------


class TestVersionNaming:
    def test_canonical_label(self):
        assert label_vocab.atlas_version_label("2026.1") == "地図 版 2026.1"
        assert label_vocab.atlas_version_label("") == ""
        assert display.VECTOR_INDEX_CURRENT.format(version="2026.1").startswith(
            "現在の公開版（地図 版 2026.1）"
        )

    def test_js_mirrors_are_verbatim(self):
        prefix = label_vocab.ATLAS_VERSION_PREFIX
        assert f'var ATLAS_VERSION_PREFIX = "{prefix}";' in _read(JS / "admin.js")
        assert f'const ATLAS_VERSION_PREFIX = "{prefix}";' in _read(JS / "landscape-layer.js")
        assert f'const ATLAS_VERSION_PREFIX = "{prefix}";' in _read(JS / "atlas-threads-layer.js")
        assert f'"{prefix}"' in _read(JS / "app.js")
        assert f'" {prefix}"' in _read(JS / "admin-release-review.js")

    def test_old_names_are_gone_from_the_atlas_surfaces(self):
        for name in ("landscape-layer.js", "atlas-threads-layer.js", "admin-release-review.js"):
            src = _read(JS / name)
            code = re.sub(r"//[^\n]*|/\*.*?\*/", "", src, flags=re.S)
            assert "骨格 版" not in code and "骨格版 " not in code, name

    def test_state_line_does_not_repeat_the_published_version(self):
        assert "学習者に公開中: 版" not in _read(JS / "admin.js")


class TestSectionIntroIsADefinition:
    def test_gap_intro_does_not_assert_papers_exist(self):
        admin = _read(JS / "admin.js")
        line = admin[admin.index("var GAP_GROUP_INTRO = "):].split("\n", 1)[0]
        assert "とき、ここに候補として出ます" in line
        assert "触れています。" not in line
