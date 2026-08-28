"""分野マップのベクトル係留層（VA層）の core ユニットテスト。

正本: ``docs/features/atlas_vector_anchoring_design.md`` §4 / §6 / §7 / §9。

実 DB を使わない（fake session / fake row で組む）。検証するのは:

1. プロトタイプ合成テキストの決定論（別名は normalized 昇順・引用は200字で切る）
2. ``source_hash`` の安定性
3. cosine / nearest / landing（MID 未満は ``None``・ラベルは正本スケールから）
4. 前段絞り込み（region を落とさない・ベクトルなし concept を残す・入力非変更）
5. 近傍注記の fail-soft とキャッシュ
6. builder の skip 理由（骨格なし・日次上限）
7. 別名正規化が ``core/atlas_gaps/schema.py`` の正本の再利用であること
"""

from __future__ import annotations

import pytest

from core.atlas_vectors import annotate, builder, query, schema, store
from core.label_vocab import (
    ANCHOR_NEARNESS_SCALE,
    ANCHOR_NEARNESS_THRESHOLD_MID,
    ANCHOR_NEARNESS_THRESHOLD_NEAR,
)


# ---------------------------------------------------------------------------
# fixtures / fakes
# ---------------------------------------------------------------------------


class _FakeSession:
    """``execute`` を記録するだけの duck-typed セッション（実 DB なし）。"""

    def __init__(self, rows_by_marker=None):
        self.rows_by_marker = rows_by_marker or {}
        self.statements: list[str] = []
        self.commits = 0

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        for marker, rows in self.rows_by_marker.items():
            if marker in sql:
                return _FakeResult(rows)
        return _FakeResult([])

    def commit(self):
        self.commits += 1

    def close(self):  # pragma: no cover — with_session 経由でのみ呼ばれる
        pass


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _anchor(node_id, vector, *, kind="concept", label="", region_label=""):
    return store.AnchorVector(
        node_id=node_id,
        node_kind=kind,
        label=label or node_id,
        region_label=region_label,
        vector=vector,
    )


# ---------------------------------------------------------------------------
# 1. プロトタイプ合成テキスト（§4）
# ---------------------------------------------------------------------------


class TestBuildAnchorSourceText:
    def test_label_only(self):
        assert schema.build_anchor_source_text("Cosmic web") == "Cosmic web"

    def test_blank_label_returns_empty(self):
        assert schema.build_anchor_source_text("   ") == ""

    def test_aliases_sorted_by_normalized_form(self):
        """別名は normalized 昇順（登録順・大小文字で合成が変わらない = 決定論）。"""
        first = schema.build_anchor_source_text(
            "Cosmic web", aliases=["ZZ topology", "大規模構造", "LSS"]
        )
        second = schema.build_anchor_source_text(
            "Cosmic web", aliases=["LSS", "大規模構造", "ZZ topology"]
        )
        assert first == second
        alias_line = [l for l in first.split("\n") if l.startswith("別名: ")][0]
        # normalize_label は casefold するので "lss" < "zz topology" < 日本語
        assert alias_line == "別名: LSS / ZZ topology / 大規模構造"

    def test_alias_duplicates_collapse_by_normalization(self):
        text = schema.build_anchor_source_text("X", aliases=["LSS", "lss", " L S S "])
        alias_line = [l for l in text.split("\n") if l.startswith("別名: ")][0]
        # "LSS"/"lss" は同一・"L S S" は空白が畳まれても別語（正規化規則どおり）
        assert alias_line.count("LSS") >= 1
        assert "lss" not in alias_line

    def test_region_line_only_when_present(self):
        assert "領域:" not in schema.build_anchor_source_text("X")
        assert "領域: 宇宙論" in schema.build_anchor_source_text("X", region_label="宇宙論")

    def test_region_prototype_lists_child_concepts(self):
        text = schema.build_anchor_source_text(
            "宇宙論", child_labels=["Cosmic web", "Dark energy", "Cosmic web"]
        )
        # 重複は落ち、順序は入力順を保つ
        assert text.split("\n")[1] == "Cosmic web / Dark energy"

    def test_evidence_quotes_truncated_and_capped(self):
        quotes = [f"{i}" + "x" * 500 for i in range(10)]
        text = schema.build_anchor_source_text("X", evidence_quotes=quotes)
        line = [l for l in text.split("\n") if l.startswith("根拠: ")][0]
        parts = line[len("根拠: "):].split(" / ")
        assert len(parts) == schema.MAX_EVIDENCE_QUOTES
        assert all(len(p) <= schema.MAX_EVIDENCE_QUOTE_CHARS for p in parts)

    def test_newlines_in_material_do_not_break_line_structure(self):
        text = schema.build_anchor_source_text(
            "X", evidence_quotes=["a\nb\n\nc"], region_label="R\nS"
        )
        assert text.split("\n") == ["X", "領域: R S", "根拠: a b c"]

    def test_blank_materials_emit_no_lines(self):
        text = schema.build_anchor_source_text(
            "X", aliases=["  ", ""], region_label="", evidence_quotes=["", "   "]
        )
        assert text == "X"


class TestSourceHash:
    def test_stable_for_same_text(self):
        assert schema.source_hash("abc") == schema.source_hash("abc")

    def test_differs_for_different_text(self):
        assert schema.source_hash("abc") != schema.source_hash("abd")

    def test_hex_sha256(self):
        digest = schema.source_hash("abc")
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)

    def test_prototype_hash_is_order_independent_for_aliases(self):
        a = schema.build_anchor_source_text("X", aliases=["b", "a"])
        b = schema.build_anchor_source_text("X", aliases=["a", "b"])
        assert schema.source_hash(a) == schema.source_hash(b)


class TestAliasNormalizationReuse:
    def test_reuses_atlas_gaps_normalize_label(self):
        """別名の正規化は ``core/atlas_gaps/schema.py`` の正本そのもの（再実装しない）。"""
        from core.atlas_gaps import schema as gaps_schema

        assert schema.normalize_label is gaps_schema.normalize_label

    def test_normalization_examples(self):
        assert schema.normalize_label("Cosmic  Web") == "cosmic web"
        assert schema.normalize_label("ＬＳＳ") == "lss"


class TestVocabulary:
    def test_status_and_source_vocabulary(self):
        assert schema.ALIAS_STATUSES == ("confirmed", "dismissed")
        assert schema.ALIAS_SOURCES == ("gap_signal", "manual")
        assert schema.NODE_KINDS == ("region", "concept")

    def test_audit_actions(self):
        assert schema.AUDIT_ACTIONS == (
            "vectors_refresh", "alias_register", "alias_dismiss"
        )

    def test_validators(self):
        assert schema.is_valid_alias_status("confirmed")
        assert not schema.is_valid_alias_status("candidate")
        assert schema.is_valid_alias_source("gap_signal")
        assert not schema.is_valid_alias_source("llm")
        assert schema.is_valid_node_kind("region")
        assert not schema.is_valid_node_kind("stage")


# ---------------------------------------------------------------------------
# 3. 純計算（§9）
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert query.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert query.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_dimension_mismatch_is_unmeasured(self):
        assert query.cosine_similarity([1.0, 0.0], [1.0]) is None

    def test_zero_vector_is_unmeasured(self):
        assert query.cosine_similarity([0.0, 0.0], [1.0, 0.0]) is None

    def test_empty_and_none_are_unmeasured(self):
        assert query.cosine_similarity(None, [1.0]) is None
        assert query.cosine_similarity([], []) is None


class TestNearestAnchors:
    def test_orders_by_similarity_descending(self):
        anchors = [
            _anchor("far", [0.0, 1.0]),
            _anchor("near", [1.0, 0.0]),
            _anchor("mid", [1.0, 1.0]),
        ]
        got = query.nearest_anchors([1.0, 0.0], anchors, limit=3)
        assert [a.node_id for a, _ in got] == ["near", "mid", "far"]

    def test_limit_respected(self):
        anchors = [_anchor(f"n{i}", [1.0, float(i)]) for i in range(5)]
        assert len(query.nearest_anchors([1.0, 0.0], anchors, limit=2)) == 2

    def test_unmeasurable_anchors_excluded(self):
        anchors = [_anchor("no_vec", None), _anchor("ok", [1.0, 0.0])]
        got = query.nearest_anchors([1.0, 0.0], anchors)
        assert [a.node_id for a, _ in got] == ["ok"]

    def test_empty_inputs(self):
        assert query.nearest_anchors(None, [_anchor("a", [1.0])]) == []
        assert query.nearest_anchors([1.0], [], limit=0) == []


class TestLandingForVector:
    @staticmethod
    def _anchor_at(similarity: float):
        """``[1,0]`` との cosine が ``similarity`` になるアンカーを作る。"""
        import math

        return _anchor(
            "n1",
            [similarity, math.sqrt(max(0.0, 1.0 - similarity * similarity))],
            label="ノード1",
            region_label="領域A",
        )

    def test_below_mid_threshold_returns_none(self):
        anchor = self._anchor_at(ANCHOR_NEARNESS_THRESHOLD_MID - 0.05)
        assert query.landing_for_vector([1.0, 0.0], [anchor]) is None

    def test_mid_band_returns_facts(self):
        anchor = self._anchor_at((ANCHOR_NEARNESS_THRESHOLD_NEAR + ANCHOR_NEARNESS_THRESHOLD_MID) / 2)
        got = query.landing_for_vector([1.0, 0.0], [anchor])
        assert got["node_id"] == "n1"
        assert got["node_label"] == "ノード1"
        assert got["region_label"] == "領域A"
        assert got["nearness_label"] == ANCHOR_NEARNESS_SCALE.labels[1]

    def test_near_band_uses_top_label(self):
        anchor = self._anchor_at(0.99)
        got = query.landing_for_vector([1.0, 0.0], [anchor])
        assert got["nearness_label"] == ANCHOR_NEARNESS_SCALE.labels[0]

    def test_no_raw_score_in_payload(self):
        got = query.landing_for_vector([1.0, 0.0], [self._anchor_at(0.99)])
        forbidden = {"score", "similarity", "cosine", "confidence", "weight", "distance"}
        assert forbidden.isdisjoint(got.keys())

    def test_no_skeleton_version_key(self):
        """版の刻印は呼び出し側の責務（ここで捏造しない — VA8）。"""
        got = query.landing_for_vector([1.0, 0.0], [self._anchor_at(0.99)])
        assert "skeleton_version" not in got

    def test_no_anchors_returns_none(self):
        assert query.landing_for_vector([1.0, 0.0], []) is None


# ---------------------------------------------------------------------------
# 4. 前段絞り込み（§6）
# ---------------------------------------------------------------------------


def _domain(domain_key="d1", *, concepts, regions=("r1",), version="v1"):
    nodes = []
    for region in regions:
        nodes.append(
            {"node_id": region, "label": region, "kind": "region", "region_id": ""}
        )
        for concept in concepts:
            nodes.append(
                {
                    "node_id": concept,
                    "label": concept,
                    "kind": "concept",
                    "region_id": region,
                }
            )
    return {
        "domain_key": domain_key,
        "domain_name": domain_key,
        "skeleton_version": version,
        "nodes": nodes,
    }


class TestPrefilterDomains:
    def test_top_k_zero_is_passthrough(self):
        domains = [_domain(concepts=["c1", "c2"])]
        out, facts = query.prefilter_domains([1.0, 0.0], domains, {}, top_k=0)
        assert out[0]["nodes"] == domains[0]["nodes"]
        assert facts == {"applied": False, "omitted": 0, "domains": {}}

    def test_no_centroid_is_passthrough(self):
        domains = [_domain(concepts=["c1", "c2"])]
        out, facts = query.prefilter_domains(None, domains, {}, top_k=1)
        assert out[0]["nodes"] == domains[0]["nodes"]
        assert facts["applied"] is False

    def test_domain_without_anchors_passes_through(self):
        domains = [_domain(concepts=["c1", "c2", "c3"])]
        out, facts = query.prefilter_domains([1.0, 0.0], domains, {}, top_k=1)
        assert len(out[0]["nodes"]) == 4
        assert "prefiltered" not in out[0]
        assert facts["omitted"] == 0

    def test_regions_are_never_dropped(self):
        domains = [_domain(concepts=["c1", "c2", "c3"], regions=("r1", "r2"))]
        anchors = {
            "d1": [
                _anchor("c1", [1.0, 0.0]),
                _anchor("c2", [0.0, 1.0]),
                _anchor("c3", [0.0, 1.0]),
                _anchor("r1", [0.0, 1.0], kind="region"),
                _anchor("r2", [0.0, 1.0], kind="region"),
            ]
        }
        out, _ = query.prefilter_domains([1.0, 0.0], domains, anchors, top_k=1)
        kinds = [n["kind"] for n in out[0]["nodes"]]
        assert kinds.count("region") == 2

    def test_concepts_without_vectors_are_kept(self):
        domains = [_domain(concepts=["c1", "c2", "c3", "c4"])]
        anchors = {
            "d1": [
                _anchor("c1", [1.0, 0.0]),
                _anchor("c2", [0.9, 0.1]),
                _anchor("c3", [0.0, 1.0]),
                # c4 はアンカー行が無い = 比較不能 → 落とさない（慎重側）
            ]
        }
        out, facts = query.prefilter_domains([1.0, 0.0], domains, anchors, top_k=1)
        ids = [n["node_id"] for n in out[0]["nodes"]]
        assert "c4" in ids
        assert "c1" in ids  # 最上位は残る
        assert "c3" not in ids  # 最下位は落ちる
        assert facts["omitted"] == 2

    def test_facts_shape(self):
        domains = [_domain(concepts=["c1", "c2", "c3"])]
        anchors = {
            "d1": [
                _anchor("c1", [1.0, 0.0]),
                _anchor("c2", [0.9, 0.1]),
                _anchor("c3", [0.0, 1.0]),
            ]
        }
        out, facts = query.prefilter_domains([1.0, 0.0], domains, anchors, top_k=1)
        assert facts == {"applied": True, "omitted": 2, "domains": {"d1": 2}}
        assert out[0]["prefiltered"] is True

    def test_no_trimming_leaves_no_flag(self):
        domains = [_domain(concepts=["c1", "c2"])]
        anchors = {"d1": [_anchor("c1", [1.0, 0.0]), _anchor("c2", [0.9, 0.1])]}
        out, facts = query.prefilter_domains([1.0, 0.0], domains, anchors, top_k=5)
        assert "prefiltered" not in out[0]
        assert facts["applied"] is False

    def test_inputs_are_not_mutated(self):
        domains = [_domain(concepts=["c1", "c2", "c3"])]
        before_nodes = [dict(n) for n in domains[0]["nodes"]]
        anchors = {
            "d1": [
                _anchor("c1", [1.0, 0.0]),
                _anchor("c2", [0.9, 0.1]),
                _anchor("c3", [0.0, 1.0]),
            ]
        }
        query.prefilter_domains([1.0, 0.0], domains, anchors, top_k=1)
        assert domains[0]["nodes"] == before_nodes
        assert "prefiltered" not in domains[0]

    def test_original_node_order_preserved(self):
        domains = [_domain(concepts=["c1", "c2", "c3"], regions=("r1",))]
        anchors = {
            "d1": [
                _anchor("c1", [0.0, 1.0]),
                _anchor("c2", [1.0, 0.0]),
                _anchor("c3", [0.9, 0.1]),
            ]
        }
        out, _ = query.prefilter_domains([1.0, 0.0], domains, anchors, top_k=2)
        # region が先頭、残した concept は元の相対順のまま
        assert [n["node_id"] for n in out[0]["nodes"]] == ["r1", "c2", "c3"]


# ---------------------------------------------------------------------------
# 5. 近傍注記（§7）— fail-soft とキャッシュ
# ---------------------------------------------------------------------------


class TestAnnotateGapClusters:
    def setup_method(self):
        annotate.reset_cache()
        builder.reset_daily_counter()

    def teardown_method(self):
        annotate.reset_cache()
        builder.reset_daily_counter()

    @staticmethod
    def _clusters(*labels):
        return [{"cluster_key": f"gap|{l}", "proposed_label": l} for l in labels]

    def test_no_anchors_returns_clusters_unchanged(self, monkeypatch):
        monkeypatch.setattr(builder, "anchors_with_labels", lambda s, d, v="": ([], ""))
        clusters = self._clusters("大規模構造")
        out = annotate.annotate_gap_clusters(None, "d1", "v1", clusters, daily_limit=10)
        assert out == clusters
        assert all("near_anchor" not in c for c in out)

    def test_version_mismatch_returns_unchanged(self, monkeypatch):
        monkeypatch.setattr(
            builder, "anchors_with_labels",
            lambda s, d, v="": ([_anchor("c1", [1.0, 0.0])], "v9"),
        )
        out = annotate.annotate_gap_clusters(
            None, "d1", "v1", self._clusters("x"), daily_limit=10
        )
        assert all("near_anchor" not in c for c in out)

    def test_embedding_failure_is_fail_soft(self, monkeypatch):
        monkeypatch.setattr(
            builder, "anchors_with_labels",
            lambda s, d, v="": ([_anchor("c1", [1.0, 0.0])], "v1"),
        )

        def _boom(texts):
            raise RuntimeError("embedding down")

        monkeypatch.setattr(builder, "embed_texts", _boom)
        out = annotate.annotate_gap_clusters(
            None, "d1", "v1", self._clusters("x"), daily_limit=10
        )
        assert all("near_anchor" not in c for c in out)

    def test_gate_exhausted_returns_unchanged(self, monkeypatch):
        monkeypatch.setattr(
            builder, "anchors_with_labels",
            lambda s, d, v="": ([_anchor("c1", [1.0, 0.0])], "v1"),
        )
        monkeypatch.setattr(builder, "embed_texts", lambda texts: [[1.0, 0.0]] * len(texts))
        out = annotate.annotate_gap_clusters(
            None, "d1", "v1", self._clusters("x"), daily_limit=0
        )
        assert all("near_anchor" not in c for c in out)

    def test_near_band_attaches_annotation(self, monkeypatch):
        monkeypatch.setattr(
            builder, "anchors_with_labels",
            lambda s, d, v="": (
                [_anchor("c1", [1.0, 0.0], label="宇宙の大規模構造", region_label="宇宙論")],
                "v1",
            ),
        )
        monkeypatch.setattr(builder, "embed_texts", lambda texts: [[1.0, 0.0]] * len(texts))
        out = annotate.annotate_gap_clusters(
            None, "d1", "v1", self._clusters("大規模構造"), daily_limit=10
        )
        note = out[0]["near_anchor"]
        assert note["node_id"] == "c1"
        assert note["node_label"] == "宇宙の大規模構造"
        assert note["region_label"] == "宇宙論"
        assert note["skeleton_version"] == "v1"
        assert note["nearness_label"] == ANCHOR_NEARNESS_SCALE.labels[0]
        assert set(note.keys()) == {
            "node_id", "node_label", "region_label", "nearness_label", "skeleton_version"
        }

    def test_below_near_threshold_no_annotation(self, monkeypatch):
        import math

        sim = ANCHOR_NEARNESS_THRESHOLD_NEAR - 0.05
        monkeypatch.setattr(
            builder, "anchors_with_labels",
            lambda s, d, v="": (
                [_anchor("c1", [sim, math.sqrt(1 - sim * sim)])], "v1",
            ),
        )
        monkeypatch.setattr(builder, "embed_texts", lambda texts: [[1.0, 0.0]] * len(texts))
        out = annotate.annotate_gap_clusters(
            None, "d1", "v1", self._clusters("x"), daily_limit=10
        )
        assert "near_anchor" not in out[0]

    def test_cache_hit_avoids_second_embed(self, monkeypatch):
        monkeypatch.setattr(
            builder, "anchors_with_labels",
            lambda s, d, v="": ([_anchor("c1", [1.0, 0.0], label="L")], "v1"),
        )
        calls: list[list[str]] = []

        def _embed(texts):
            calls.append(list(texts))
            return [[1.0, 0.0] for _ in texts]

        monkeypatch.setattr(builder, "embed_texts", _embed)
        first = annotate.annotate_gap_clusters(
            None, "d1", "v1", self._clusters("大規模構造"), daily_limit=10
        )
        second = annotate.annotate_gap_clusters(
            None, "d1", "v1", self._clusters("大規模構造"), daily_limit=10
        )
        assert len(calls) == 1  # 2回目はキャッシュヒットで埋め込みを呼ばない
        assert first[0]["near_anchor"] == second[0]["near_anchor"]

    def test_duplicate_labels_embedded_once(self, monkeypatch):
        monkeypatch.setattr(
            builder, "anchors_with_labels",
            lambda s, d, v="": ([_anchor("c1", [1.0, 0.0])], "v1"),
        )
        calls: list[list[str]] = []

        def _embed(texts):
            calls.append(list(texts))
            return [[1.0, 0.0] for _ in texts]

        monkeypatch.setattr(builder, "embed_texts", _embed)
        annotate.annotate_gap_clusters(
            None, "d1", "v1", self._clusters("X", "x", "  X  "), daily_limit=10
        )
        assert len(calls) == 1 and len(calls[0]) == 1

    def test_empty_clusters_short_circuit(self, monkeypatch):
        def _boom(*args, **kwargs):  # pragma: no cover — 呼ばれてはいけない
            raise AssertionError("must not touch anchors for empty clusters")

        monkeypatch.setattr(builder, "anchors_with_labels", _boom)
        assert annotate.annotate_gap_clusters(None, "d1", "v1", []) == []


# ---------------------------------------------------------------------------
# 6. builder の skip 理由（§5）
# ---------------------------------------------------------------------------


class _FakeSkeleton:
    def __init__(self, version="v1", regions=()):
        self.version = version
        self.regions = regions


class _FakeRegion:
    def __init__(self, rid, label, concepts=()):
        self.id = rid
        self.label = label
        self.concepts = concepts


class _FakeConcept:
    def __init__(self, cid, label):
        self.id = cid
        self.label = label


class TestBuilderSkipReasons:
    def setup_method(self):
        builder.reset_daily_counter()

    def teardown_method(self):
        builder.reset_daily_counter()

    def test_no_frozen_skeleton(self, monkeypatch):
        import core.atlas_store as atlas_store

        monkeypatch.setattr(atlas_store, "load_frozen_skeleton", lambda s, d: None)
        got = builder.build_anchor_embeddings("d1", session=_FakeSession())
        assert got == {
            "status": "skipped",
            "skipped_reason": builder.SKIP_NO_FROZEN_SKELETON,
        }

    def test_blank_domain_key(self):
        got = builder.build_anchor_embeddings("  ", session=_FakeSession())
        assert got["skipped_reason"] == builder.SKIP_NO_FROZEN_SKELETON

    def test_skeleton_without_nodes(self, monkeypatch):
        import core.atlas_store as atlas_store

        monkeypatch.setattr(
            atlas_store, "load_frozen_skeleton", lambda s, d: _FakeSkeleton("v1", ())
        )
        got = builder.build_anchor_embeddings("d1", session=_FakeSession())
        assert got["skipped_reason"] == builder.SKIP_NO_NODES

    def test_daily_limit_reached(self, monkeypatch):
        import core.atlas_store as atlas_store

        skeleton = _FakeSkeleton(
            "v1", (_FakeRegion("r1", "領域", (_FakeConcept("c1", "概念"),)),)
        )
        monkeypatch.setattr(atlas_store, "load_frozen_skeleton", lambda s, d: skeleton)
        monkeypatch.setattr(builder, "check_daily_gate", lambda limit: False)
        got = builder.build_anchor_embeddings("d1", session=_FakeSession())
        assert got == {"status": "skipped", "skipped_reason": builder.SKIP_DAILY_LIMIT}

    def test_completed_summary_and_write(self, monkeypatch):
        import core.atlas_store as atlas_store

        skeleton = _FakeSkeleton(
            "v2",
            (
                _FakeRegion(
                    "r1", "領域", (_FakeConcept("c1", "概念1"), _FakeConcept("c2", "概念2"))
                ),
            ),
        )
        monkeypatch.setattr(atlas_store, "load_frozen_skeleton", lambda s, d: skeleton)
        monkeypatch.setattr(builder, "check_daily_gate", lambda limit: True)
        embedded_texts: list[list[str]] = []

        def _embed(texts):
            embedded_texts.append(list(texts))
            return [[1.0, 0.0] for _ in texts]

        monkeypatch.setattr(builder, "embed_texts", _embed)
        session = _FakeSession()
        got = builder.build_anchor_embeddings("d1", session=session)

        assert got["status"] == "completed"
        assert got["domain_key"] == "d1"
        assert got["skeleton_version"] == "v2"
        assert got["total_nodes"] == 3  # region 1 + concept 2
        assert got["embedded"] == 3
        assert got["reused"] == 0
        # 全置換（DELETE → INSERT×3）が1トランザクションで走り commit される
        assert any("DELETE FROM atlas_anchor_embeddings" in s for s in session.statements)
        assert sum("INSERT INTO atlas_anchor_embeddings" in s for s in session.statements) == 3
        assert session.commits == 1
        # region のプロトタイプに配下 concept が入る（§4）
        assert "概念1 / 概念2" in embedded_texts[0][0]

    def test_evidence_quotes_fail_soft(self, monkeypatch):
        class _BoomSession(_FakeSession):
            def execute(self, statement, params=None):
                if "landscape_placements" in str(statement):
                    raise RuntimeError("table missing")
                return super().execute(statement, params)

        got = builder.collect_evidence_quotes(_BoomSession(), "d1")
        assert got == {}

    def test_evidence_quotes_are_capped_and_truncated(self):
        long_quote = "z" * 500
        rows = [
            ("c1", [{"quote": long_quote, "claim_id": "x"}]),
            ("c1", [{"quote": "b"}, {"quote": "c"}, {"quote": "d"}]),
            ("c1", [{"quote": "e"}, {"quote": "f"}]),
        ]
        session = _FakeSession({"landscape_placements": rows})
        got = builder.collect_evidence_quotes(session, "d1")
        assert len(got["c1"]) == schema.MAX_EVIDENCE_QUOTES
        assert len(got["c1"][0]) == schema.MAX_EVIDENCE_QUOTE_CHARS

    def test_evidence_quotes_accept_json_string(self):
        rows = [("c1", '[{"quote": "hello", "claim_id": null}]')]
        session = _FakeSession({"landscape_placements": rows})
        assert builder.collect_evidence_quotes(session, "d1") == {"c1": ["hello"]}


# ---------------------------------------------------------------------------
# 7. store の純粋部（実 DB なしで確かめられる範囲）
# ---------------------------------------------------------------------------


class TestStorePurePieces:
    def test_parse_vector_forms(self):
        assert store.parse_vector([1.0, 2.0]) == [1.0, 2.0]
        assert store.parse_vector("[1.0,2.0]") == [1.0, 2.0]
        assert store.parse_vector("not a vector") is None
        assert store.parse_vector(None) is None
        assert store.parse_vector("[]") is None

    def test_replace_domain_embeddings_no_sql_on_empty_rows(self):
        session = _FakeSession()
        assert store.replace_domain_embeddings(session, "d1", "v1", []) == 0
        assert session.statements == []

    def test_replace_domain_embeddings_no_sql_without_version(self):
        session = _FakeSession()
        rows = [{"node_id": "c1", "node_kind": "concept"}]
        assert store.replace_domain_embeddings(session, "d1", "", rows) == 0
        assert session.statements == []

    def test_replace_domain_embeddings_skips_invalid_kind(self):
        session = _FakeSession()
        rows = [
            {"node_id": "c1", "node_kind": "stage", "source_text": "x", "source_hash": "h"},
            {"node_id": "c2", "node_kind": "concept", "source_text": "y", "source_hash": "h"},
        ]
        assert store.replace_domain_embeddings(session, "d1", "v1", rows) == 1

    def test_upsert_alias_validation(self):
        session = _FakeSession()
        with pytest.raises(ValueError):
            store.upsert_alias(
                session, domain_key="", node_id="c1", alias="a", user_id="u"
            )
        with pytest.raises(ValueError):
            store.upsert_alias(
                session, domain_key="d1", node_id="", alias="a", user_id="u"
            )
        with pytest.raises(ValueError):
            store.upsert_alias(
                session, domain_key="d1", node_id="c1", alias="", user_id="u"
            )
        with pytest.raises(ValueError):
            store.upsert_alias(
                session, domain_key="d1", node_id="c1", alias="a", user_id=""
            )
        with pytest.raises(ValueError):
            store.upsert_alias(
                session, domain_key="d1", node_id="c1", alias="a",
                source="llm", user_id="u",
            )
        assert session.statements == []

    def test_dismiss_alias_requires_actor(self):
        with pytest.raises(ValueError):
            store.dismiss_alias(_FakeSession(), "abc", user_id="")

    def test_dismiss_alias_missing_row_returns_none(self):
        assert store.dismiss_alias(_FakeSession(), "abc", user_id="u") is None
        assert store.dismiss_alias(_FakeSession(), "", user_id="u") is None

    def test_coverage_status_shape(self):
        session = _FakeSession({"atlas_anchor_embeddings": [(3, 2, None)]})
        got = store.coverage_status(session, "d1", "v1")
        assert got == {"total_rows": 3, "embedded_rows": 2, "built_at": None}

    def test_coverage_status_without_version(self):
        session = _FakeSession()
        assert store.coverage_status(session, "d1", "")["total_rows"] == 0
        assert session.statements == []

    def test_anchors_for_domains_is_fail_soft(self):
        class _BoomSession(_FakeSession):
            def execute(self, statement, params=None):
                raise RuntimeError("db down")

        got = store.anchors_for_domains(
            _BoomSession(), [{"domain_key": "d1", "skeleton_version": "v1"}]
        )
        assert got == {}
