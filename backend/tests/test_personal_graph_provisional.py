"""個人地図の暫定ノード（カテゴリギャップ候補 v1-b）の単体テスト。

正本: ``docs/features/category_gap_candidates_design.md`` §4.4 裁定（個人地図の暫定ノードは
v1-b に採用・共有骨格へは書き込まない）/ §5.6（学習者側）/ §8-2（暫定ノードの寿命）と
``docs/features/personal_knowledge_network_design.md`` §0（PN-1〜7）。

``core/personal_graph/provisional.py`` は ``derive.py`` / ``bridges.py`` と同じ
「純粋部 + DB 部」構成なので、``build_provisional_nodes`` は fake rows（dict のリスト）
だけで検証できる。DB 部（``resolve_course_map_domain`` / ``derive_provisional_nodes``）は
フェイクセッション + monkeypatch で検証する（実 DB 接続なし）。

検証観点:
- 同一主題の統合（``normalize_label`` 経由）・代表は最新信号・上限12・決定論順
- コースの sources 由来 document 以外の信号は出さない（空集合は fail-closed で ``[]``）
- **DTO のキー集合が仕様どおり**で、``confidence`` / ``weight`` / ``cluster_key`` /
  ``decision`` / ``status`` / ``layer`` などの内部語彙・数値が再帰的に一切現れない
  （PN-4 / LS5・共有候補の存在を学習者に見せない）
- 骨格の無いコース（``/api/atlas`` が 404 になるコース）では空
- 取得失敗・例外はすべて ``[]`` へ縮退し、個人ネットワーク本体を壊さない（PN-7）
- 共有候補・教員判断テーブル（``atlas_gap_decisions``）を読まない・書かない（構造検査）
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.personal_graph.provisional import (  # noqa: E402
    MAX_PROVISIONAL_NODES,
    PROVISIONAL_NODE_ID_PREFIX,
    PROVISIONAL_SOURCE_LABEL,
    build_provisional_nodes,
    derive_provisional_nodes,
    provisional_node_id,
    resolve_course_map,
    resolve_course_map_domain,
    skeleton_labels,
)

_DOC1 = "11111111-1111-1111-1111-111111111111"
_DOC2 = "22222222-2222-2222-2222-222222222222"
_OTHER_DOC = "33333333-3333-3333-3333-333333333333"
_DOMAIN = "astrophysics"

_PROVISIONAL_SRC = (
    BACKEND / "core" / "personal_graph" / "provisional.py"
).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# fake row ビルダ（core/atlas_gaps/store.py::list_active_signals の返す dict と同型）
# ---------------------------------------------------------------------------


def _signal(
    *,
    id_: str,
    document_id: str = _DOC1,
    proposed_label: str = "宇宙論的レンズ",
    created_at: str = "2026-08-01T00:00:00+00:00",
    evidence_quote: str = "we measure the lensing amplitude",
    reason: str = "この主題は既存の概念で言い表せない",
    layer: str = "concept",
    parent_region_id: str = "cosmology",
    status: str = "active",
    confidence: float | None = 0.82,
    domain_key: str = _DOMAIN,
) -> dict:
    return {
        "id": id_,
        "document_id": document_id,
        "run_id": None,
        "domain_key": domain_key,
        "skeleton_version": "2026.1",
        "layer": layer,
        "parent_region_id": parent_region_id,
        "proposed_label": proposed_label,
        "normalized_label": proposed_label.casefold(),
        "reason": reason,
        "evidence_quote": evidence_quote,
        "confidence": confidence,
        "status": status,
        "created_at": created_at,
        "updated_at": created_at,
    }


def _code_text(src: str) -> str:
    """ソースから **docstring とコメントを除いた実コード**の識別子・文字列を抜き出す。

    設計意図の説明（docstring）に語彙が出るのは正しい状態なので、層境界の検査は
    実際に評価されるコード（識別子・SQL 文字列・import 名）だけを対象にする。
    """
    import ast

    tree = ast.parse(src)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))

    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                parts.append(node.value)
        elif isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Attribute):
            parts.append(node.attr)
        elif isinstance(node, ast.Import):
            parts.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            parts.append(node.module or "")
            parts.extend(alias.name for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parts.append(node.name)
    return "\n".join(parts)


def _walk_keys(value) -> list[str]:
    """dict / list の入れ子を再帰的に走査してキー名を全部集める。"""
    keys: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            keys.append(str(k))
            keys.extend(_walk_keys(v))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_walk_keys(item))
    return keys


# ---------------------------------------------------------------------------
# 純粋部
# ---------------------------------------------------------------------------


class TestDocumentScope:
    """コースの sources 由来 document に閉じる（PN-1 の実効・fail-closed）。"""

    def test_signal_from_other_document_is_excluded(self):
        nodes = build_provisional_nodes(
            [
                _signal(id_="s1", document_id=_DOC1, proposed_label="A"),
                _signal(id_="s2", document_id=_OTHER_DOC, proposed_label="B"),
            ],
            document_ids={_DOC1},
            titles={_DOC1: "論文1"},
        )
        assert [n["label"] for n in nodes] == ["A"]

    def test_empty_document_set_returns_empty(self):
        nodes = build_provisional_nodes(
            [_signal(id_="s1")], document_ids=set(), titles={_DOC1: "論文1"}
        )
        assert nodes == []

    def test_none_document_set_returns_empty(self):
        assert build_provisional_nodes([_signal(id_="s1")], document_ids=None) == []

    def test_superseded_signal_is_not_shown(self):
        nodes = build_provisional_nodes(
            [_signal(id_="s1", status="superseded")],
            document_ids={_DOC1},
            titles={_DOC1: "論文1"},
        )
        assert nodes == []


class TestSubjectMerging:
    """同一主題は1ノードに統合し、代表は最新信号（表記と逐語引用は最新のもの）。"""

    def test_same_subject_across_documents_merges_into_one_node(self):
        nodes = build_provisional_nodes(
            [
                _signal(
                    id_="s1",
                    document_id=_DOC1,
                    proposed_label="Cosmic  Web",
                    created_at="2026-08-01T00:00:00+00:00",
                    evidence_quote="古い引用",
                ),
                _signal(
                    id_="s2",
                    document_id=_DOC2,
                    proposed_label="cosmic web",
                    created_at="2026-08-05T00:00:00+00:00",
                    evidence_quote="新しい引用",
                ),
            ],
            document_ids={_DOC1, _DOC2},
            titles={_DOC1: "論文1", _DOC2: "論文2"},
        )
        assert len(nodes) == 1
        assert nodes[0]["label"] == "cosmic web"  # 最新の表記
        assert nodes[0]["evidence_quote"] == "新しい引用"  # 最新 signal の逐語1件
        assert nodes[0]["documents"] == [{"title": "論文1"}, {"title": "論文2"}]

    def test_different_subjects_stay_separate(self):
        nodes = build_provisional_nodes(
            [
                _signal(id_="s1", proposed_label="A", created_at="2026-08-01T00:00:00+00:00"),
                _signal(id_="s2", proposed_label="B", created_at="2026-08-02T00:00:00+00:00"),
            ],
            document_ids={_DOC1},
            titles={_DOC1: "論文1"},
        )
        assert [n["label"] for n in nodes] == ["A", "B"]

    def test_document_titles_are_deduplicated_and_titleless_rows_are_omitted(self):
        nodes = build_provisional_nodes(
            [
                _signal(id_="s1", document_id=_DOC1, proposed_label="A"),
                _signal(id_="s2", document_id=_DOC2, proposed_label="A"),
            ],
            document_ids={_DOC1, _DOC2},
            titles={_DOC1: "同じ題名", _DOC2: "同じ題名"},
        )
        assert nodes[0]["documents"] == [{"title": "同じ題名"}]

        nodes = build_provisional_nodes(
            [_signal(id_="s1", document_id=_DOC1, proposed_label="A")],
            document_ids={_DOC1},
            titles={},
        )
        assert nodes[0]["documents"] == []  # 題名が引けなければ内部 ID を代わりに出さない

    def test_empty_label_is_dropped(self):
        nodes = build_provisional_nodes(
            [_signal(id_="s1", proposed_label="   ")],
            document_ids={_DOC1},
            titles={_DOC1: "論文1"},
        )
        assert nodes == []


class TestResolvedLabelExclusion:
    """§8-2 裁定（解消済みラベルの除外）: 現行凍結骨格に同名（正規化一致）の領域・概念が
    ある主題は暫定ノードにしない — 帯は「地図の外の主題」なので、既に地図へ入った主題を
    出し続けない。除外は読み時導出のまま（保存物・掃除バッチなし）。"""

    def _nodes(self, label: str, resolved: set[str] | None) -> list[dict]:
        return build_provisional_nodes(
            [_signal(id_="s1", proposed_label=label)],
            document_ids={_DOC1},
            titles={_DOC1: "論文1"},
            resolved_labels=resolved,
        )

    def test_exact_concept_label_is_excluded(self):
        assert self._nodes("宇宙論的レンズ", {"宇宙論的レンズ"}) == []

    def test_match_is_normalized_case_width_and_whitespace_insensitive(self):
        # normalize_label（NFKC + casefold + 空白畳み）の規則で一致すれば除外する。
        assert self._nodes("Cosmic  Web", {"cosmic web"}) == []
        assert self._nodes("cosmic web", {"Cosmic Web"}) == []
        assert self._nodes("ＣＭＢ Lensing", {"cmb lensing"}) == []

    def test_region_label_also_excludes(self):
        """除外集合は領域・概念を区別しない（地図にその名前がある時点で「外」ではない）。"""
        skeleton = {
            "regions": [
                {"id": "cosmology", "label": "宇宙論", "concepts": []},
            ]
        }
        assert self._nodes("宇宙論", skeleton_labels(skeleton)) == []

    def test_unresolved_label_is_kept(self):
        nodes = self._nodes("宇宙論的レンズ", {"別の概念", "宇宙論"})
        assert [n["label"] for n in nodes] == ["宇宙論的レンズ"]

    def test_omitted_resolved_labels_keeps_everything(self):
        """既定（None）は除外しない — 後方互換（骨格が読めないときに全部消さない）。"""
        assert [n["label"] for n in self._nodes("宇宙論的レンズ", None)] == ["宇宙論的レンズ"]
        assert [n["label"] for n in self._nodes("宇宙論的レンズ", set())] == ["宇宙論的レンズ"]

    def test_only_the_resolved_subject_disappears(self):
        nodes = build_provisional_nodes(
            [
                _signal(id_="s1", proposed_label="取り込まれた主題",
                        created_at="2026-08-01T00:00:00+00:00"),
                _signal(id_="s2", proposed_label="まだ外の主題",
                        created_at="2026-08-02T00:00:00+00:00"),
            ],
            document_ids={_DOC1},
            titles={_DOC1: "論文1"},
            resolved_labels={"取り込まれた主題"},
        )
        assert [n["label"] for n in nodes] == ["まだ外の主題"]


class TestSkeletonLabels:
    """除外集合の作り方（骨格の3形状 = dataclass / dict / ラッパー dict を受ける）。"""

    def test_dict_skeleton(self):
        skeleton = {
            "regions": [
                {
                    "id": "cosmology",
                    "label": "宇宙論",
                    "concepts": [{"id": "cmb_lensing", "label": "CMB Lensing"}],
                }
            ]
        }
        assert skeleton_labels(skeleton) == {"宇宙論", "cmb lensing"}

    def test_wrapped_dict_skeleton(self):
        skeleton = {"atlas_skeleton": {"regions": [{"id": "r", "label": "領域", "concepts": []}]}}
        assert skeleton_labels(skeleton) == {"領域"}

    def test_dataclass_like_skeleton(self):
        class _Concept:
            id = "cmb_lensing"
            label = "CMB Lensing"

        class _Region:
            id = "cosmology"
            label = "宇宙論"
            concepts = [_Concept()]

        class _Sk:
            regions = [_Region()]

        assert skeleton_labels(_Sk()) == {"宇宙論", "cmb lensing"}

    def test_missing_or_empty_skeleton_is_empty_set(self):
        assert skeleton_labels(None) == set()
        assert skeleton_labels({}) == set()
        assert skeleton_labels(_Skeleton()) == set()  # regions を持たない骨格でも落ちない

    def test_id_variants_are_not_included(self):
        """要件: ラベル突合のみ（id 変種の逆写像は作らない）。"""
        skeleton = {
            "regions": [
                {"id": "cosmology", "label": "宇宙論",
                 "concepts": [{"id": "cmb_lensing", "label": "CMB Lensing"}]}
            ]
        }
        labels = skeleton_labels(skeleton)
        assert "cosmology" not in labels
        assert "cmb_lensing" not in labels


class TestOrderingAndLimit:
    """決定論順（その主題が最初に現れた日時 → 正規化ラベル）と上限12。"""

    def test_limit_is_twelve_and_deterministic(self):
        signals = [
            _signal(
                id_=f"s{i:02d}",
                proposed_label=f"主題{i:02d}",
                created_at=f"2026-08-{i + 1:02d}T00:00:00+00:00",
            )
            for i in range(15)
        ]
        nodes = build_provisional_nodes(
            signals, document_ids={_DOC1}, titles={_DOC1: "論文1"}
        )
        assert MAX_PROVISIONAL_NODES == 12
        assert len(nodes) == 12
        assert [n["label"] for n in nodes] == [f"主題{i:02d}" for i in range(12)]

    def test_output_independent_of_input_order(self):
        signals = [
            _signal(id_="s1", proposed_label="B", created_at="2026-08-02T00:00:00+00:00"),
            _signal(id_="s2", proposed_label="A", created_at="2026-08-01T00:00:00+00:00"),
        ]
        forward = build_provisional_nodes(
            signals, document_ids={_DOC1}, titles={_DOC1: "論文1"}
        )
        backward = build_provisional_nodes(
            list(reversed(signals)), document_ids={_DOC1}, titles={_DOC1: "論文1"}
        )
        assert forward == backward
        assert [n["label"] for n in forward] == ["A", "B"]

    def test_zero_limit_returns_empty(self):
        nodes = build_provisional_nodes(
            [_signal(id_="s1")], document_ids={_DOC1}, titles={_DOC1: "論文1"}, limit=0
        )
        assert nodes == []


class TestNodeIdentity:
    """id は決定論・安定（再解析・再読み込みで同じ主題は同じ id）。"""

    def test_id_is_prefixed_and_stable(self):
        node = build_provisional_nodes(
            [_signal(id_="s1", proposed_label="Cosmic Web")],
            document_ids={_DOC1},
            titles={_DOC1: "論文1"},
        )[0]
        assert node["id"].startswith(PROVISIONAL_NODE_ID_PREFIX)
        assert node["id"] == provisional_node_id(_DOMAIN, "cosmic web")

    def test_id_does_not_expose_cluster_key_or_label(self):
        node_id = provisional_node_id(_DOMAIN, "cosmic web")
        assert "gap|" not in node_id
        assert "cosmic" not in node_id
        assert _DOMAIN not in node_id


class TestDtoShape:
    """DTO は仕様の5キーだけ。内部語彙・数値を再帰的に一切載せない（PN-4 / LS5 / §5.6）。"""

    def _nodes(self) -> list[dict]:
        return build_provisional_nodes(
            [
                _signal(id_="s1", document_id=_DOC1, proposed_label="A"),
                _signal(id_="s2", document_id=_DOC2, proposed_label="A"),
            ],
            document_ids={_DOC1, _DOC2},
            titles={_DOC1: "論文1", _DOC2: "論文2"},
        )

    def test_exact_key_set(self):
        node = self._nodes()[0]
        assert set(node.keys()) == {
            "id",
            "label",
            "documents",
            "evidence_quote",
            "source_label",
        }
        for doc in node["documents"]:
            assert set(doc.keys()) == {"title"}

    def test_no_internal_or_numeric_keys_anywhere(self):
        keys = set(_walk_keys(self._nodes()))
        for forbidden in (
            "confidence",
            "confidence_label",
            "weight",
            "cluster_key",
            "decision",
            "status",
            "layer",
            "parent_region_id",
            "skeleton_version",
            "normalized_label",
            "document_id",
            "reason",
            "run_id",
            "domain_key",
            "count",
            "version_mismatch",
        ):
            assert forbidden not in keys, f"暫定ノード DTO に {forbidden!r} が漏れている"

    def test_source_label_is_the_shared_wording(self):
        assert self._nodes()[0]["source_label"] == PROVISIONAL_SOURCE_LABEL
        assert PROVISIONAL_SOURCE_LABEL == "AIによる推定（未確認）"


# ---------------------------------------------------------------------------
# DB 部（フェイクセッション + monkeypatch）
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeSession:
    """``SELECT data FROM learning_courses`` だけを知っている最小セッション。"""

    def __init__(self, course_data: dict | None):
        self.course_data = course_data
        self.closed = False

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        if "FROM learning_courses" in sql:
            return _FakeResult([(self.course_data,)] if self.course_data is not None else [])
        return _FakeResult([])

    def close(self):
        self.closed = True


class _Skeleton:
    version = "2026.1"


def _skeleton_with(labels: list[str]) -> dict:
    """指定ラベルの概念だけを持つ骨格（dict 形状。load_learner_skeleton の戻りを模す）。"""
    return {
        "regions": [
            {
                "id": "cosmology",
                "label": "宇宙論",
                "concepts": [
                    {"id": f"c{i}", "label": label} for i, label in enumerate(labels)
                ],
            }
        ]
    }


def _patch_atlas(monkeypatch, *, derived: str = _DOMAIN, skeleton=None, anchored: bool = True):
    from core import atlas_state, atlas_store

    monkeypatch.setattr(
        atlas_state, "resolve_course_cartridge", lambda session, data: derived
    )
    monkeypatch.setattr(
        atlas_store, "load_learner_skeleton", lambda domain, session=None: skeleton
    )
    monkeypatch.setattr(
        atlas_state,
        "course_has_skeleton_anchor",
        lambda session, sk, domain, data: anchored,
    )


class TestResolveCourseMapDomain:
    """地図が出ないコースでは暫定ノードも出さない（/api/atlas と同じ fail-closed 規則）。"""

    def test_no_frozen_skeleton_means_no_domain(self, monkeypatch):
        _patch_atlas(monkeypatch, skeleton=None)
        assert resolve_course_map_domain(_FakeSession({}), {"title": "コース"}) == ""

    def test_derived_cartridge_without_anchor_means_no_domain(self, monkeypatch):
        _patch_atlas(monkeypatch, skeleton=_Skeleton(), anchored=False)
        assert resolve_course_map_domain(_FakeSession({}), {"title": "コース"}) == ""

    def test_derived_cartridge_with_anchor_resolves(self, monkeypatch):
        _patch_atlas(monkeypatch, skeleton=_Skeleton(), anchored=True)
        assert resolve_course_map_domain(_FakeSession({}), {"title": "コース"}) == _DOMAIN

    def test_explicit_cartridge_skips_the_anchor_gate(self, monkeypatch):
        # 明示 cartridge_id は妥当性ゲートを免除される（既存 atlas の規則と同じ）。
        _patch_atlas(monkeypatch, derived="", skeleton=_Skeleton(), anchored=False)
        assert (
            resolve_course_map_domain(_FakeSession({}), {"cartridge_id": _DOMAIN})
            == _DOMAIN
        )

    def test_resolved_labels_come_from_the_same_skeleton_read(self, monkeypatch):
        """§8-2: 除外集合はドメイン判定と同一の骨格読みから作る（骨格を2回読まない）。"""
        loads: list[str] = []

        from core import atlas_state, atlas_store

        def _load(domain, session=None):
            loads.append(domain)
            return _skeleton_with(["CMB Lensing"])

        monkeypatch.setattr(atlas_state, "resolve_course_cartridge", lambda s, d: _DOMAIN)
        monkeypatch.setattr(atlas_store, "load_learner_skeleton", _load)
        monkeypatch.setattr(
            atlas_state, "course_has_skeleton_anchor", lambda s, sk, dk, d: True
        )

        domain_key, resolved = resolve_course_map(_FakeSession({}), {"title": "コース"})
        assert domain_key == _DOMAIN
        assert resolved == {"宇宙論", "cmb lensing"}
        assert loads == [_DOMAIN]

    def test_no_map_means_empty_label_set(self, monkeypatch):
        _patch_atlas(monkeypatch, skeleton=None)
        assert resolve_course_map(_FakeSession({}), {"title": "コース"}) == ("", set())


class TestDeriveProvisionalNodes:
    """エントリポイントの結線と fail-closed。"""

    def _patch_all(
        self,
        monkeypatch,
        *,
        signals,
        document_ids={_DOC1},
        titles=None,
        course_data=None,
        skeleton=_Skeleton(),
        anchored=True,
    ):
        from core.atlas_gaps import store as gap_store
        from core.personal_graph import queries
        import core.postgres as postgres_mod

        session = _FakeSession(course_data if course_data is not None else {"title": "コース"})
        _patch_atlas(monkeypatch, skeleton=skeleton, anchored=anchored)
        monkeypatch.setattr(postgres_mod, "get_session", lambda: session)
        monkeypatch.setattr(
            queries, "fetch_course_document_ids", lambda course_id: set(document_ids)
        )
        monkeypatch.setattr(
            queries,
            "fetch_document_titles",
            lambda ids: dict(titles or {_DOC1: "論文1", _DOC2: "論文2"}),
        )
        if isinstance(signals, Exception):
            def _boom(session, **kwargs):
                raise signals

            monkeypatch.setattr(gap_store, "list_active_signals", _boom)
        else:
            monkeypatch.setattr(
                gap_store, "list_active_signals", lambda session, **kwargs: list(signals)
            )
        return session

    def test_returns_nodes_and_closes_session(self, monkeypatch):
        session = self._patch_all(
            monkeypatch, signals=[_signal(id_="s1", proposed_label="Cosmic Web")]
        )
        nodes = derive_provisional_nodes("courseA")
        assert [n["label"] for n in nodes] == ["Cosmic Web"]
        assert session.closed  # try/finally で必ず close（開発ルール4）

    def test_empty_course_id_is_empty(self):
        assert derive_provisional_nodes("") == []

    def test_missing_course_row_is_empty(self, monkeypatch):
        self._patch_all(monkeypatch, signals=[_signal(id_="s1")], course_data={})
        assert derive_provisional_nodes("courseA") == []

    def test_course_without_map_is_empty(self, monkeypatch):
        self._patch_all(monkeypatch, signals=[_signal(id_="s1")], skeleton=None)
        assert derive_provisional_nodes("courseA") == []

    def test_course_without_documents_is_empty(self, monkeypatch):
        self._patch_all(monkeypatch, signals=[_signal(id_="s1")], document_ids=set())
        assert derive_provisional_nodes("courseA") == []

    def test_signals_from_other_documents_are_not_shown(self, monkeypatch):
        self._patch_all(
            monkeypatch,
            signals=[_signal(id_="s1", document_id=_OTHER_DOC)],
            document_ids={_DOC1},
        )
        assert derive_provisional_nodes("courseA") == []

    def test_subject_already_in_the_frozen_skeleton_is_not_shown(self, monkeypatch):
        """§8-2 裁定の結線: 凍結骨格に入った主題は再解析を待たず帯から消える。"""
        self._patch_all(
            monkeypatch,
            signals=[
                _signal(id_="s1", proposed_label="CMB Lensing",
                        created_at="2026-08-01T00:00:00+00:00"),
                _signal(id_="s2", proposed_label="まだ外の主題",
                        created_at="2026-08-02T00:00:00+00:00"),
            ],
            skeleton=_skeleton_with(["cmb lensing"]),
        )
        nodes = derive_provisional_nodes("courseA")
        assert [n["label"] for n in nodes] == ["まだ外の主題"]

    def test_store_failure_degrades_to_empty(self, monkeypatch):
        session = self._patch_all(monkeypatch, signals=RuntimeError("relation missing"))
        assert derive_provisional_nodes("courseA") == []
        assert session.closed

    def test_session_failure_degrades_to_empty(self, monkeypatch):
        import core.postgres as postgres_mod

        def _boom():
            raise RuntimeError("no database")

        monkeypatch.setattr(postgres_mod, "get_session", _boom)
        assert derive_provisional_nodes("courseA") == []


# ---------------------------------------------------------------------------
# 構造検査（層の境界: 共有骨格・共有候補へ書かない・読まない）
# ---------------------------------------------------------------------------


class TestLayerBoundaries:
    """§0 層分離 / LS7: 個人地図は signal 層のみを読む。"""

    def test_does_not_touch_decisions_table_or_cluster_vocabulary(self):
        code = _code_text(_PROVISIONAL_SRC)
        for forbidden in ("atlas_gap_decisions", "cluster_key", "derive_candidates",
                          "upsert_decision", "get_decision", "confidence"):
            assert forbidden not in code, (
                f"provisional.py が共有候補側の語彙に触れている: {forbidden!r}"
            )

    def test_has_no_write_statements(self):
        for forbidden in ("INSERT ", "UPDATE ", "DELETE "):
            assert forbidden not in _PROVISIONAL_SRC, (
                f"provisional.py は読み取り専用でなければならない: {forbidden!r}"
            )

    def test_does_not_import_framework_layers(self):
        for forbidden in ("import fastapi", "from fastapi", "import services",
                          "from services", "import routes", "from routes",
                          "from core.llm", "import core.llm"):
            assert forbidden not in _PROVISIONAL_SRC

    def test_route_exposes_provisional_nodes_on_course_view_only(self):
        route_src = (BACKEND / "api" / "routes" / "personal_map.py").read_text(
            encoding="utf-8"
        )
        assert "provisional_nodes" in route_src
        assert "derive_provisional_nodes(course_id)" in route_src
        # 本人スコープ正本 API（/api/me）には足さない（コースビュー限定・§5.6）
        me_part = route_src.split("me_router.get(\"/personal-network\")")[1]
        assert "provisional_nodes" not in me_part
