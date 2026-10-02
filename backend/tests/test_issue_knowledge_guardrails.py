"""課題ナレッジ（docs/issue_knowledge/）のガードレール。

正本: docs/issue_knowledge/taxonomy.md（分類体系）/ TEMPLATE.md（記入様式）/
      docs/issue_knowledge/README.md（運用）。

守るもの:
- 各エントリの front-matter が語彙（4 軸の値・確度・観点・一般化レベル）の範囲内で、
  各軸が 1〜2 値か単独の none / unknown を持つ（排他の主分類は無い。群は座標から導出）。
- 原因未確定は ``cause_status: hypothesis`` + 「仮説:」で始まる basis として明示される。
- 分類の根拠（basis）に症状の場所・修正量を書かない（taxonomy §1.2）。
- ``pattern`` は dictionary.md の型に実在し、辞書の型はエントリを最低 1 つ持つ。
- ``sources`` は docs 配下に実在する。
- index.md は生成器の出力と一致する（手編集しない）。
- モジュール側の語彙定数と taxonomy.md の語彙表が一致する（片方だけ変えると落ちる）。
- 分類の確定状態（candidate / confirmed）・観点は最大 2・層は layers.md の語彙・
  解決済みの着地先にコードが 1 つ以上・辞書は族（###）→ 型（####）の 2 段。
- 解決原理（principles.md / taxonomy §9）: 参照の無い原理は置かない・行の見出しは固定・
  「当たる型」は辞書に実在・``resolution.principles`` の原理は実在し ``use`` は語彙内。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts import issue_knowledge_index as ik

ROOT = Path(__file__).resolve().parents[2]
IK_DIR = ROOT / "docs" / "issue_knowledge"
README_DOC = IK_DIR / "README.md"
TEMPLATE_DOC = IK_DIR / "TEMPLATE.md"


@pytest.fixture(scope="module")
def entries() -> list[ik.Entry]:
    return ik.load_entries()


@pytest.fixture(scope="module")
def dictionary_text() -> str:
    return ik.DICTIONARY_DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def taxonomy_text() -> str:
    return ik.TAXONOMY_DOC.read_text(encoding="utf-8")


class TestLayout:
    def test_core_documents_exist(self):
        for path in (README_DOC, TEMPLATE_DOC, ik.TAXONOMY_DOC, ik.DICTIONARY_DOC, ik.INDEX_DOC, ik.LAYERS_DOC, ik.PRINCIPLES_DOC):
            assert path.exists(), f"{path.relative_to(ROOT)} が無い"
        assert ik.ENTRIES_DIR.is_dir()

    def test_reference_documents_declare_state(self):
        for path in (README_DOC, ik.TAXONOMY_DOC, ik.DICTIONARY_DOC, ik.INDEX_DOC, ik.LAYERS_DOC, ik.PRINCIPLES_DOC):
            head = path.read_text(encoding="utf-8")[:1500]
            assert re.search(r"(?m)^.*(状態|ステータス):", head), (
                f"{path.relative_to(ROOT)} の冒頭にラベル付き状態行が無い（development_checklist §5-2）"
            )

    def test_entry_filenames_follow_convention(self, entries):
        bad = [e.path.name for e in entries if not ik.ENTRY_FILE_RE.match(e.path.name)]
        assert bad == [], f"IK-NNNN-<slug>.md に従わないファイル: {bad}"

    def test_there_is_at_least_one_entry(self, entries):
        assert entries, "entries/ が空（仕組みだけで中身が無い状態を許さない）"


class TestVocabularyMirrorsTaxonomy:
    """モジュール側の語彙は taxonomy.md のミラー。表に無い語彙・表にあるのに無い語彙を検出する。"""

    @staticmethod
    def _backticked(text: str) -> set[str]:
        return set(re.findall(r"`([a-z0-9_]+(?:\.[a-z_]+)?)`", text))

    def test_every_module_token_is_defined_in_taxonomy(self, taxonomy_text):
        defined = self._backticked(taxonomy_text)
        for group in (
            ik.AXES,
            ik.FACETS,
            ik.AXIS_EMPTY,
            ik.CONFIDENCE_LEVELS,
            ik.PROPOSAL_KINDS,
            ik.CAUSE_STATUSES,
            ik.REVIEW_STATES,
            ik.STATUSES,
            ik.GENERALIZATION_LEVELS,
            ik.DISCOVERY_PERSPECTIVES,
            ik.RESOLUTION_PERSPECTIVES,
            ik.VERIFICATION_METHODS,
            ik.PRINCIPLE_USES,
        ):
            missing = [t for t in group if t not in defined]
            assert missing == [], f"taxonomy.md に定義の無い語彙（モジュール側だけにある）: {missing}"

    def test_every_taxonomy_axis_value_is_known_to_module(self, taxonomy_text):
        section = ik_section(taxonomy_text, "## 2.")
        in_doc = set(re.findall(r"`((?:processing|structure|connection|governance)\.[a-z_]+)`", section))
        assert in_doc == set(ik.FACETS), (
            f"軸の値の語彙が taxonomy §2 とモジュールで食い違う: doc-only={in_doc - set(ik.FACETS)} "
            f"module-only={set(ik.FACETS) - in_doc}"
        )
        assert set(ik.GOVERNANCE_SUBGROUPS["制御系（実行時）"]) | set(ik.GOVERNANCE_SUBGROUPS["手続系（人）"]) == set(
            ik.AXIS_VALUES["governance"]
        ), "統制軸の区分（制御系 / 手続系）が値の全体を覆っていない"

    def test_every_taxonomy_perspective_is_known_to_module(self, taxonomy_text):
        disc = set(re.findall(r"(?m)^\| `([a-z_]+)` \|", ik_section(taxonomy_text, "## 4.")))
        res = set(re.findall(r"(?m)^\| `([a-z_]+)` \|", ik_section(taxonomy_text, "## 5.")))
        assert disc == set(ik.DISCOVERY_PERSPECTIVES), (
            f"発見観点が食い違う: doc-only={disc - set(ik.DISCOVERY_PERSPECTIVES)} "
            f"module-only={set(ik.DISCOVERY_PERSPECTIVES) - disc}"
        )
        assert res == set(ik.RESOLUTION_PERSPECTIVES), (
            f"解決観点が食い違う: doc-only={res - set(ik.RESOLUTION_PERSPECTIVES)} "
            f"module-only={set(ik.RESOLUTION_PERSPECTIVES) - res}"
        )

    def test_every_taxonomy_verification_method_is_known_to_module(self, taxonomy_text):
        """検証方法（taxonomy §8）の表とモジュールの語彙は一致する。§5 の解決観点とは別の節に置く（混ぜない）。"""
        ver = set(re.findall(r"(?m)^\| `([a-z0-9_]+)` \|", ik_section(taxonomy_text, "## 8.")))
        assert ver == set(ik.VERIFICATION_METHODS), (
            f"検証方法が食い違う: doc-only={ver - set(ik.VERIFICATION_METHODS)} "
            f"module-only={set(ik.VERIFICATION_METHODS) - ver}"
        )
        assert ik.VERIFICATION_NOT_VERIFIED in ik.VERIFICATION_METHODS
        res = set(re.findall(r"(?m)^\| `([a-z_]+)` \|", ik_section(taxonomy_text, "## 5.")))
        assert not (res & set(ik.VERIFICATION_METHODS)), "検証方法の語彙が解決観点の節に混入している"


def ik_section(text: str, heading_prefix: str) -> str:
    m = re.search(rf"(?m)^{re.escape(heading_prefix)}", text)
    assert m is not None, f"taxonomy.md の見出し「{heading_prefix}」が見つからない（改名したらテストも追随）"
    tail = text[m.end() :]
    rel_end = tail.find("\n## ")
    return tail if rel_end == -1 else tail[:rel_end]


class TestEntries:
    def test_every_entry_passes_validation(self, entries, dictionary_text):
        per_entry, global_errs = ik.validate_all(entries, dictionary_text)
        report = []
        for name, errs in sorted(per_entry.items()):
            report.append(f"[{name}]")
            report.extend(f"  - {err}" for err in errs)
        report.extend(f"[全体] {err}" for err in global_errs)
        assert not report, "課題ナレッジの規約違反:\n" + "\n".join(report)

    def test_hypotheses_are_explicit_in_body(self, entries):
        """原因が仮説のエントリは、本文でも「仮説」と明示する（front-matter だけに埋めない）。"""
        offenders = []
        for e in entries:
            cl = e.meta.get("classification") or {}
            if cl.get("cause_status") == "hypothesis" and "仮説" not in e.body:
                offenders.append(e.path.name)
        assert offenders == [], f"cause_status=hypothesis なのに本文に「仮説」の語が無い: {offenders}"

    def test_dictionary_has_no_feature_names_in_general_forms(self, dictionary_text):
        """辞書は型を機能から切り離す。見出し直後の表にファイル名・パスを書かない。"""
        for slug in ik.dictionary_patterns(dictionary_text):
            block = ik_section(dictionary_text, f"#### {slug}")
            table = block.split("\n###", 1)[0]
            for pat in ik.FORBIDDEN_GENERAL_FORM_PATTERNS:
                # バッククォート自体は語彙表記に使うので、パス・拡張子のみ弾く
                if pat.pattern.startswith("[/"):
                    continue
                assert not pat.search(table), f"dictionary.md の型 `{slug}` にファイル名・拡張子が含まれる"

    def test_dictionary_families_are_listed_under_axis_sections(self, dictionary_text):
        """各族（###）は 4 軸の節（## 処理 / ## 構造 / ## 接続 / ## 統制）の下、各型（####）は族の下に置く。"""
        allowed = {"処理", "構造", "接続", "統制"}
        current = None
        family = None
        for line in dictionary_text.splitlines():
            if line.startswith("## "):
                current = line[3:].strip()
                family = None
            elif line.startswith("### "):
                assert current in allowed, f"族 `{line[4:].strip()}` が軸の節の外にある（節: {current}）"
                family = line[4:].strip()
            elif line.startswith("#### "):
                assert family is not None, f"型 `{line[5:].strip()}` が族の下にない"

    def test_every_family_has_a_table_and_types(self, dictionary_text):
        fams = ik.dictionary_families(dictionary_text)
        assert "" not in fams or fams[""] == [], f"族の外にある型: {fams.get('')}"
        for fam, types in fams.items():
            assert types, f"族 `{fam}` に型が無い"
            block = ik_section(dictionary_text, f"### {fam}")
            assert "| 族の名 |" in block and "| 含む型 |" in block, f"族 `{fam}` の表（族の名 / 含む型）が無い"
            listed = set(re.findall(r"`([a-z0-9-]+)`", block.split("| 含む型 |", 1)[1].split("\n", 1)[0]))
            assert listed == set(types), f"族 `{fam}` の「含む型」と #### 見出しが食い違う: {listed ^ set(types)}"

    def test_cycle_stage_layers_are_declared(self):
        """改善サイクルの段の層（cycle_*）は layers.md に全部あり、モジュールの宣言と一致する。"""
        slugs = set(ik.layer_vocabulary())
        missing = [s for s in ik.CYCLE_STAGE_LAYERS if s not in slugs]
        assert missing == [], f"layers.md にサイクル層が無い: {missing}"
        extra = sorted(s for s in slugs if s.startswith(ik.CYCLE_LAYER_PREFIX) and s not in ik.CYCLE_STAGE_LAYERS)
        assert extra == [], f"モジュールに宣言されていない cycle_ 層: {extra}（段を足すなら CYCLE_STAGE_LAYERS にも）"
        cycle_doc = ik.IMPROVEMENT_CYCLE_DOC.read_text(encoding="utf-8")
        for s in ik.CYCLE_STAGE_LAYERS:
            assert f"`{s}`" in cycle_doc, f"改善サイクル文書 §3 にサイクル層 `{s}` が挙がっていない"

    def test_improvement_cycle_doc_exists_and_is_indexed(self):
        """改善サイクルの正本はリポジトリにあり、状態行を持ち、索引と課題ナレッジ README から参照される。"""
        assert ik.IMPROVEMENT_CYCLE_DOC.exists(), "docs/architecture/improvement_cycle.md が無い（手順の正本をリポジトリ外に置かない）"
        head = ik.IMPROVEMENT_CYCLE_DOC.read_text(encoding="utf-8")[:1500]
        assert re.search(r"(?m)^.*(状態|ステータス):", head)
        for path in (ROOT / "docs" / "README.md", ROOT / "CLAUDE.md", README_DOC, ROOT / "docs" / "development_checklist.md"):
            assert "improvement_cycle.md" in path.read_text(encoding="utf-8"), f"{path.relative_to(ROOT)} が改善サイクル文書を参照していない"
        text = ik.IMPROVEMENT_CYCLE_DOC.read_text(encoding="utf-8")
        for needle in ("## 3.", "## 4.", "index.md", "再帰"):
            assert needle in text, f"改善サイクル文書に「{needle}」が無い（自己適用・線引き・計器の節）"

    def test_layers_doc_rows_map_to_layer_registry(self):
        """layers.md の各行は、レイヤー索引表 §1 の層に対応するか「（索引表外）」と明記する。"""
        registry = (ROOT / "docs" / "architecture" / "layer_registry.md").read_text(encoding="utf-8")
        text = ik.LAYERS_DOC.read_text(encoding="utf-8")
        slugs = ik.layer_vocabulary(text)
        assert slugs and len(slugs) == len(set(slugs)), "layers.md の slug が空か重複"
        for line in text.splitlines():
            m = re.match(r"^\| `([a-z0-9_]+)` \| (.+?) \|$", line)
            if not m:
                continue
            desc = m.group(2)
            if "索引表外" in desc:
                continue
            # 正式名の先頭数語（層ラベルの後ろ）が索引表に現れること
            name = re.sub(r"^\S+層\S*\s+|^運営基盤\s+|^[A-Z]{1,2}層\s+|^[A-Z]{1,2}追補\s+", "", desc)
            key = re.split(r"[（(]", name)[0].strip()[:6]
            assert key and key in registry, f"layers.md `{m.group(1)}` の対応「{desc}」がレイヤー索引表に見当たらない"


class TestNeighbors:
    def test_neighbors_are_symmetric_and_valid(self):
        for tok, nbs in ik.NEIGHBORS.items():
            assert tok in ik.FACETS
            for nb in nbs:
                assert nb in ik.FACETS, f"{tok} の相手 {nb} が語彙外"
                assert tok in ik.NEIGHBORS[nb], f"相手が非対称: {tok} → {nb}"
                assert nb != tok

    def test_taxonomy_neighbor_column_mirrors_module(self, taxonomy_text):
        """taxonomy §2 の「境界が曖昧になりやすい相手」列はモジュールの NEIGHBORS と逐語一致する。"""
        section = ik_section(taxonomy_text, "## 2.")
        doc: dict[str, set[str]] = {}
        for line in section.splitlines():
            m = re.match(r"^\| [^|]+ \| `((?:processing|structure|connection|governance)\.[a-z_]+)` \| [^|]* \| ([^|]*) \|$", line)
            if m:
                doc[m.group(1)] = set(re.findall(r"`([a-z_]+\.[a-z_]+)`", m.group(2)))
        assert set(doc) == set(ik.FACETS), f"相手の列が無い値: {set(ik.FACETS) - set(doc)} / 余分: {set(doc) - set(ik.FACETS)}"
        for tok in ik.FACETS:
            assert doc[tok] == set(ik.NEIGHBORS[tok]), f"{tok} の相手が taxonomy とモジュールで食い違う: doc={doc[tok]} module={set(ik.NEIGHBORS[tok])}"

    def test_provisional_values_declare_neighbors_and_exist(self):
        for tok in ik.PROVISIONAL_VALUES:
            assert tok in ik.FACETS and ik.NEIGHBORS.get(tok), f"暫定の値 {tok} は語彙にあり相手を宣言する"


class TestAxes:
    def test_group_is_derived_from_coordinates(self):
        cl = {"axes": {"processing": ["logic"], "structure": ["none"], "connection": ["none"], "governance": ["none"]}}
        assert ik.group_of(cl) == ik.GROUP_LOCAL
        cl["axes"]["connection"] = ["version"]
        assert ik.group_of(cl) == ik.GROUP_DESIGN
        cl["axes"] = {"processing": ["none"], "structure": ["unknown"], "connection": ["none"], "governance": ["none"]}
        assert ik.group_of(cl) == ik.GROUP_UNDETERMINED

    def test_none_and_unknown_are_distinct_and_solitary(self, entries, dictionary_text):
        """none（見て無いと判断）と unknown（まだ見ていない）は別の値で、他の値と併記できない。"""
        assert set(ik.AXIS_EMPTY) == {"none", "unknown"}
        bad = ik.Entry(path=ik.ENTRIES_DIR / "IK-9999-x.md", meta={}, body="")
        cl = {"axes": {"processing": ["none", "logic"], "structure": ["none"], "connection": ["none"], "governance": ["none"]}}
        # 直接 validate_entry を呼ぶと他キー不足で早期 return するので、規則だけを関数で確認
        assert not ik.axis_is_empty(["none", "logic"])
        assert ik.axis_is_empty(["none"]) and ik.axis_is_empty(["unknown"])

    def test_unknown_axes_imply_hypothesis(self, entries):
        bad = [e.id for e in entries if any(ik.axis_values(e.meta["classification"], a) == ["unknown"] for a in ik.AXES)
               and e.meta["classification"].get("cause_status") != "hypothesis"]
        assert bad == [], f"unknown の軸があるのに hypothesis でないエントリ: {bad}"

    def test_no_entry_declares_primary_or_facets(self, entries):
        """排他の主分類（primary / facets）は廃止。残っていれば座標化漏れ。"""
        leftovers = [e.id for e in entries if "primary" in (e.meta.get("classification") or {}) or "facets" in (e.meta.get("classification") or {})]
        assert leftovers == [], f"旧形式（primary / facets）が残るエントリ: {leftovers}"


class TestIndexIsGenerated:
    def test_index_matches_generator_output(self, entries, dictionary_text):
        rendered = ik.render_index(entries, dictionary_text) + "\n"
        current = ik.INDEX_DOC.read_text(encoding="utf-8")
        assert current == rendered, (
            "docs/issue_knowledge/index.md が生成器の出力と一致しない。手編集せず "
            "`backend/.venv/bin/python backend/scripts/issue_knowledge_index.py` で再生成すること"
        )

    def test_index_links_every_entry(self, entries):
        current = ik.INDEX_DOC.read_text(encoding="utf-8")
        missing = [e.id for e in entries if e.rel not in current]
        assert missing == [], f"index.md にリンクされていないエントリ: {missing}"


class TestModulePurity:
    def test_module_does_not_import_app_code(self):
        src = (ROOT / "backend" / "scripts" / "issue_knowledge_index.py").read_text(encoding="utf-8")
        import_lines = [ln for ln in src.splitlines() if re.match(r"^\s*(from|import)\s", ln)]
        for forbidden in ("fastapi", "sqlalchemy", "core", "api", "openai"):
            hits = [ln for ln in import_lines if re.search(rf"\b{forbidden}\b", ln)]
            assert hits == [], f"issue_knowledge_index.py はアプリコード（{forbidden}）を import しない: {hits}"


class TestVerificationRecord:
    """`resolution.verification`（taxonomy §8）— 未観測と良好を分ける欄。省略は記録なし、確かめていないなら not_verified。"""

    @staticmethod
    def _resolved_meta(entries) -> dict:
        import copy
        base = next(e for e in entries if e.meta.get("status") == "resolved")
        meta = copy.deepcopy(base.meta)
        meta["resolution"].pop("verification", None)
        return meta

    @staticmethod
    def _errors(entries, dictionary_text, meta, **overrides) -> list[str]:
        import copy
        meta = copy.deepcopy(meta)
        meta.update(overrides)
        base = next(e for e in entries if e.meta.get("status") == "resolved")
        entry = ik.Entry(path=base.path, meta=meta, body=base.body)
        patterns = set(ik.dictionary_patterns(dictionary_text))
        known = {str(e.meta.get("id")) for e in entries}
        errs = ik.validate_entry(entry, patterns=patterns, known_ids=known, layers=set(ik.layer_vocabulary()))
        return [e for e in errs if "verification" in e]

    def test_absent_verification_is_allowed_as_no_record(self, entries, dictionary_text):
        meta = self._resolved_meta(entries)
        assert self._errors(entries, dictionary_text, meta) == []

    def test_well_formed_verification_passes(self, entries, dictionary_text):
        meta = self._resolved_meta(entries)
        meta["resolution"]["verification"] = {"methods": ["scratch_db", "guardrail"], "unverified": ["同時実行"]}
        assert self._errors(entries, dictionary_text, meta) == []
        meta["resolution"]["verification"] = {"methods": ["not_verified"], "unverified": []}
        assert self._errors(entries, dictionary_text, meta) == []

    def test_verification_only_on_resolved(self, entries, dictionary_text):
        meta = self._resolved_meta(entries)
        meta["status"] = "open"
        meta["resolved_at"] = None
        meta["resolution"]["perspective"] = ["pending"]
        meta["resolution"]["verification"] = {"methods": ["guardrail"], "unverified": []}
        assert any("status=resolved" in e for e in self._errors(entries, dictionary_text, meta))

    def test_vocabulary_and_shape_are_enforced(self, entries, dictionary_text):
        meta = self._resolved_meta(entries)
        cases = {
            "語彙外": {"methods": ["eyeballed"], "unverified": []},
            "not_verified 単独": {"methods": ["not_verified", "guardrail"], "unverified": []},
            "methods 空": {"methods": [], "unverified": []},
            "unverified 欠落": {"methods": ["guardrail"]},
            "数値の記述": {"methods": ["guardrail"], "unverified": ["3 件のケースは未確認"]},
            "未知キー": {"methods": ["guardrail"], "unverified": [], "coverage": 0.8},
        }
        for label, ver in cases.items():
            meta["resolution"]["verification"] = ver
            assert self._errors(entries, dictionary_text, meta), f"{label} が通ってしまう: {ver}"

    def test_dictionary_types_declare_how_to_verify(self, dictionary_text):
        """辞書の型は「確かめ方」（処方）を持つ。実績は索引 §14.4。"""
        for slug in ik.dictionary_patterns(dictionary_text):
            table = ik_section(dictionary_text, f"#### {slug}").split("\n###", 1)[0]
            m = re.search(r"(?m)^\| 確かめ方 \| (.+?) \|\s*$", table)
            assert m and m.group(1).strip(), f"dictionary.md の型 `{slug}` に「確かめ方」の行が無い"
            assert not re.search(r"\d+\s*(件|%|％)", m.group(1)), f"型 `{slug}` の確かめ方に件数・率がある"

    def test_index_renders_verification_cooccurrence(self, entries, dictionary_text):
        rendered = ik.render_index(entries, dictionary_text)
        assert "### 14.4 型ごとの確かめ方" in rendered
        verified = [e for e in entries if e.meta.get("status") == "resolved" and ik.verification_of(e)]
        for e in verified:
            assert e.rel in rendered.split("### 14.4", 1)[1].split("## 10.", 1)[0], f"{e.id} が §14.4 に載っていない"
        assert "確かめていないと記録されたエントリ" in rendered


class TestSolutionPrinciples:
    """principles.md（taxonomy §9）— 型を横断する条件付きの解決知識。参照の無い原理は置かない。"""

    @pytest.fixture(scope="class")
    def principles_text(self) -> str:
        return ik.PRINCIPLES_DOC.read_text(encoding="utf-8")

    def test_principles_doc_has_at_least_one_principle(self, principles_text):
        assert ik.principle_slugs(principles_text), "principles.md に原理（#### 見出し）が無い"

    def test_every_principle_declares_required_rows_and_existing_types(self, principles_text, dictionary_text):
        patterns = set(ik.dictionary_patterns(dictionary_text))
        sections = ik.principle_sections(principles_text)
        for slug in ik.principle_slugs(principles_text):
            body = sections[slug]
            for row in ik.PRINCIPLE_REQUIRED_ROWS:
                m = re.search(rf"(?m)^\| {re.escape(row)} \| (.+?) \|\s*$", body)
                assert m and m.group(1).strip(), f"原理 `{slug}` に「{row}」の行が無い"
            types = ik.principle_types(principles_text)[slug]
            assert types, f"原理 `{slug}` の「当たる型」が空"
            assert set(types) <= patterns, f"原理 `{slug}` の「当たる型」に辞書に無い型: {set(types) - patterns}"
            principle_row = re.search(r"(?m)^\| 原理 \| (.+?) \|\s*$", body).group(1)
            for pat in ik.FORBIDDEN_GENERAL_FORM_PATTERNS:
                assert not pat.search(principle_row), f"原理 `{slug}` の「原理」行にファイル名・パス・コード片（{pat.pattern}）"
            assert not re.search(r"\d+\s*(件|%|％)", principle_row), f"原理 `{slug}` の「原理」行に件数・率"

    def test_every_principle_is_referenced_by_an_entry(self, entries, principles_text):
        used = {str(x.get("principle")) for e in entries for x in ik.principles_of(e)}
        missing = sorted(set(ik.principle_slugs(principles_text)) - used)
        assert missing == [], f"参照するエントリの無い原理: {missing}"

    def test_entry_references_are_validated(self, entries, dictionary_text):
        import copy
        base = next(e for e in entries if ik.principles_of(e))
        good = copy.deepcopy(base.meta)
        patterns = set(ik.dictionary_patterns(dictionary_text))
        known = {str(x.meta.get("id")) for x in entries}
        principles = set(ik.principle_slugs(ik.PRINCIPLES_DOC.read_text(encoding="utf-8")))

        def errors(meta) -> list[str]:
            entry = ik.Entry(path=base.path, meta=meta, body=base.body)
            return [
                err for err in ik.validate_entry(entry, patterns=patterns, known_ids=known, principles=principles)
                if "principles" in err
            ]

        assert errors(good) == []
        cases = {
            "原理が実在しない": [{"principle": "no-such-principle", "use": "consulted", "note": "x"}],
            "use が語彙外": [{"principle": good["resolution"]["principles"][0]["principle"], "use": "applied", "note": "x"}],
            "note が空": [{"principle": good["resolution"]["principles"][0]["principle"], "use": "consulted", "note": ""}],
            "note に件数": [{"principle": good["resolution"]["principles"][0]["principle"], "use": "consulted", "note": "3 件で確認"}],
            "重複": [
                {"principle": good["resolution"]["principles"][0]["principle"], "use": "consulted", "note": "x"},
                {"principle": good["resolution"]["principles"][0]["principle"], "use": "extracted", "note": "y"},
            ],
            "未知キー": [{"principle": good["resolution"]["principles"][0]["principle"], "use": "consulted", "note": "x", "score": 1}],
        }
        for label, items in cases.items():
            meta = copy.deepcopy(good)
            meta["resolution"]["principles"] = items
            assert errors(meta), f"{label} が通ってしまう: {items}"

    def test_index_renders_principles_section(self, entries, dictionary_text, principles_text):
        rendered = ik.render_index(entries, dictionary_text, principles_text)
        assert "## 15. 解決原理別" in rendered
        section = rendered.split("## 15.", 1)[1].split("## 10.", 1)[0]
        for slug in ik.principle_slugs(principles_text):
            assert f"#### `{slug}`" in section
        for e in entries:
            if ik.principles_of(e):
                assert e.rel in section, f"{e.id} が §15 に載っていない"
        assert "`consulted`" in section

    def test_taxonomy_and_docs_describe_the_principles_layer(self, taxonomy_text):
        assert "## 9. 解決原理" in taxonomy_text
        readme = README_DOC.read_text(encoding="utf-8")
        assert "principles.md" in readme
        checklist = (ROOT / "docs" / "development_checklist.md").read_text(encoding="utf-8")
        assert "principles.md" in checklist
        cycle = ik.IMPROVEMENT_CYCLE_DOC.read_text(encoding="utf-8")
        assert "principles.md" in cycle

    def test_establishment_is_derived_from_forward_use_not_human_confirmation(self, entries, principles_text):
        """成立 = consulted が別々の型から 2 件以上・適用後の同型の見逃しなし（taxonomy §9）。review: confirmed は条件にしない。"""
        import copy
        slug = ik.principle_slugs(principles_text)[0]
        base = next(e for e in entries if e.meta.get("status") == "resolved")

        def fake(ident: str, pattern: str, use: str | None, *, recorded: str, resolved: str | None, related=()) -> ik.Entry:
            meta = copy.deepcopy(base.meta)
            meta.update({"id": ident, "pattern": pattern, "recorded_at": recorded, "resolved_at": resolved,
                         "related": list(related), "view_of": [], "status": "resolved" if resolved else "open"})
            meta["classification"]["review"] = "candidate"
            meta["resolution"]["principles"] = [{"principle": slug, "use": use, "note": "x"}] if use else []
            return ik.Entry(path=base.path, meta=meta, body=base.body)

        # 後付けだけ → 暫定（確定エントリがあっても成立しない）
        only_extracted = [fake("IK-9001", "a", "extracted", recorded="2026-01-01", resolved="2026-01-02"),
                          fake("IK-9002", "b", "extracted", recorded="2026-01-01", resolved="2026-01-02")]
        for e in only_extracted:
            e.meta["classification"]["review"] = "confirmed"
        assert ik.principle_state(slug, only_extracted)[0].startswith("暫定")
        # 同じ型から 2 件 → 暫定
        same_type = [fake("IK-9001", "a", "consulted", recorded="2026-01-01", resolved="2026-01-02"),
                     fake("IK-9002", "a", "consulted", recorded="2026-01-03", resolved="2026-01-04")]
        assert ik.principle_state(slug, same_type)[0].startswith("暫定")
        # 別々の型から 2 件・見逃しなし → 成立（review は candidate のまま）
        two_types = [fake("IK-9001", "a", "consulted", recorded="2026-01-01", resolved="2026-01-02"),
                     fake("IK-9002", "b", "consulted", recorded="2026-01-03", resolved="2026-01-04")]
        assert ik.principle_state(slug, two_types)[0] == "成立"
        # 適用後に同型の見逃し（consulted を related で指す同じ型の後発エントリ）→ 暫定
        miss = two_types + [fake("IK-9003", "a", None, recorded="2026-02-01", resolved=None, related=["IK-9001"])]
        state, _, misses = ik.principle_state(slug, miss)
        assert state.startswith("暫定") and [m.id for m in misses] == ["IK-9003"]
        # 別の型からの後発参照は見逃しに数えない
        other = two_types + [fake("IK-9003", "c", None, recorded="2026-02-01", resolved=None, related=["IK-9001"])]
        assert ik.principle_state(slug, other)[0] == "成立"

    def test_docs_do_not_say_humans_select_principles(self, taxonomy_text):
        readme = README_DOC.read_text(encoding="utf-8")
        cycle = ik.IMPROVEMENT_CYCLE_DOC.read_text(encoding="utf-8")
        for name, text in {"taxonomy": taxonomy_text, "README": readme, "improvement_cycle": cycle}.items():
            # 訂正の記録行（旧文を引用する行）は除く
            live = [ln for ln in text.splitlines() if "訂正" not in ln and "撤回" not in ln]
            hits = [ln for ln in live if "選ぶのは人" in ln]
            assert hits == [], f"{name}: 人は選ばない（問題が明らかになったときだけ介入する）: {hits}"
        assert "問題が明らかになったとき" in taxonomy_text

# ---------------------------------------------------------------------------
# 想起シート（recall.md）と設計書の pre-mortem 節 — 「思い出させる」経路（2026-10-02）
# ---------------------------------------------------------------------------

# pre-mortem 節を持たないレガシー設計書。新しい `*_design.md` には節を必須とするため、
# この allowlist は**減らす方向のみ**（新しい設計書をここに足してはならない）。
_PREMORTEM_LEGACY_ALLOWLIST = frozenset(
    {
        "account_lifecycle_management_design.md",
        "admin_assistant_design.md",
        "assistant_common_infra_design.md",
        "assistant_screen_adapter_design.md",
        "atlas_binding_lifecycle_design.md",
        "atlas_node_correspondence_design.md",
        "atlas_relation_edges_design.md",
        "atlas_vector_anchoring_design.md",
        "candidate_flow_design.md",
        "category_gap_candidates_design.md",
        "claim_concept_grounding_design.md",
        "concept_registry_design.md",
        "corpus_complement_design.md",
        "corpus_roaming_design.md",
        "decision_context_design.md",
        "dialogue_response_shape_design.md",
        "disclosure_axes_design.md",
        "discuss_dialogue_alignment_design.md",
        "discuss_observation_design.md",
        "discuss_opening_authoring_design.md",
        "discussion_mode_design.md",
        "display_projection_design.md",
        "element_context_lens_design.md",
        "element_deliberation_workspace_design.md",
        "element_inventory_design.md",
        "equation_context_panel_display_design.md",
        "equation_hover_content_design.md",
        "exposition_layer_design.md",
        "figure_concept_linking_design.md",
        "focus_document_design.md",
        "generation_language_design.md",
        "graph_dialogue_review_design.md",
        "graph_paper_layer_design.md",
        "guidance_layer_design.md",
        "guided_figure_reanalysis_design.md",
        "hierarchical_context_explanation_design.md",
        "image_pipeline_knowledge_library_design.md",
        "indicator_governance_design.md",
        "knowledge_landscape_design.md",
        "knowledge_objects_design.md",
        "knowledge_transfer_design.md",
        "label_vocab_design.md",
        "learner_element_context_design.md",
        "learning_chat_entry_unification_design.md",
        "learning_ui_inspect_hover_design.md",
        "learning_units_design.md",
        "lecture_slide_sync_design.md",
        "llm_model_selection_design.md",
        "llm_response_streaming_design.md",
        "llm_usage_metering_design.md",
        "manual_help_kb_design.md",
        "paper_discovery_design.md",
        "paper_radar_design.md",
        "personal_knowledge_network_design.md",
        "personal_map_curiosity_design.md",
        "personal_map_nearby_design.md",
        "reconstruction_loop_design.md",
        "release_review_flow_design.md",
        "return_door_design.md",
        "seminar_brief_mirroring_design.md",
        "shared_versioning_design.md",
        "stakes_ledger_design.md",
        "status_notification_design.md",
        "structure_descent_design.md",
        "teacher_triage_instruments_design.md",
        "teaching_figure_studio_design.md",
        "trace_registry_sovereignty_ledger_design.md",
        "understanding_cycle_design.md",
        "url_material_upload_design.md",
    }
)

_RECALL_READERS = (
    ROOT / "CLAUDE.md",
    ROOT / "docs" / "development_checklist.md",
    ROOT / "docs" / "architecture" / "improvement_cycle.md",
    README_DOC,
    ROOT / ".claude" / "skills" / "episteme-graph-dev" / "SKILL.md",
    ROOT / ".claude" / "skills" / "episteme-graph-knowledge" / "SKILL.md",
)


class TestRecallSheet:
    """辞書・原理は「開きに行かないと読まれない」。短い想起シートを読み込み経路に置き、設計書に型を写させる。"""

    def test_recall_doc_exists_and_matches_generator(self, entries, dictionary_text):
        assert ik.RECALL_DOC.exists(), "docs/issue_knowledge/recall.md が無い（生成器で作る）"
        rendered = ik.render_recall(entries, dictionary_text) + "\n"
        assert ik.RECALL_DOC.read_text(encoding="utf-8") == rendered, (
            "recall.md が生成器の出力と一致しない。再生成すること: "
            "backend/.venv/bin/python backend/scripts/issue_knowledge_index.py"
        )

    def test_recall_doc_is_short(self):
        n = len(ik.RECALL_DOC.read_text(encoding="utf-8").splitlines())
        assert n <= ik.RECALL_MAX_LINES, f"recall.md が {n} 行。読み込み経路に置く 1 枚は {ik.RECALL_MAX_LINES} 行以内"

    def test_recall_lists_bounded_patterns_with_detection_and_verification(self, entries, dictionary_text):
        pats = ik.recall_patterns(entries, dictionary_text)
        assert 0 < len(pats) <= ik.RECALL_MAX_PATTERNS
        rows = ik.dictionary_type_rows(dictionary_text)
        for pat in pats:
            assert rows[pat].get("見分け方") and rows[pat].get("確かめ方"), f"辞書の型 `{pat}` に見分け方・確かめ方が無い"

    def test_trigger_families_exist_in_dictionary(self, dictionary_text):
        families = set(ik.dictionary_families(dictionary_text))
        for _words, fam in ik.PREMORTEM_TRIGGERS:
            assert fam in families, f"引き金の族 `{fam}` が辞書に無い"

    def test_trigger_table_is_canonical_in_module(self):
        # README §3.3b は表を持たず recall.md（生成器の PREMORTEM_TRIGGERS）を指す。表を 2 箇所に持たない。
        readme = README_DOC.read_text(encoding="utf-8")
        assert "recall.md" in readme
        assert "| 変更に含まれる語 | 先に当てる族 |" not in readme, "引き金の表は生成器（recall.md）だけが持つ"

    def test_recall_is_in_the_reading_path(self):
        for path in _RECALL_READERS:
            assert "recall.md" in path.read_text(encoding="utf-8"), f"{path.relative_to(ROOT)} が recall.md を参照していない"

    def test_new_design_docs_declare_premortem(self, dictionary_text):
        missing = []
        for path in ik.design_docs():
            if path.name in _PREMORTEM_LEGACY_ALLOWLIST:
                continue
            decl = ik.premortem_declaration(path.read_text(encoding="utf-8"), dictionary_text)
            if decl is None or not (decl["slugs"] or decl["none"]):
                missing.append(path.relative_to(ROOT).as_posix())
        assert missing == [], (
            f"pre-mortem 節（見出しに pre-mortem・本文に辞書の型 slug か「{ik.PREMORTEM_NONE_TEXT}」）が無い設計書: {missing}。"
            "書き方は docs/issue_knowledge/recall.md §4"
        )

    def test_premortem_legacy_allowlist_has_no_stale_entries(self, dictionary_text):
        names = {p.name for p in ik.design_docs()}
        stale = sorted(n for n in _PREMORTEM_LEGACY_ALLOWLIST if n not in names)
        assert stale == [], f"存在しない設計書が allowlist に残っている: {stale}"
        graduated = sorted(
            p.name for p in ik.design_docs()
            if p.name in _PREMORTEM_LEGACY_ALLOWLIST and ik.premortem_section(p.read_text(encoding="utf-8")) is not None
        )
        assert graduated == [], f"pre-mortem 節を得た設計書は allowlist から外す: {graduated}"

    def test_premortem_slugs_in_design_docs_exist(self, dictionary_text):
        known = set(ik.dictionary_patterns(dictionary_text))
        for path in ik.design_docs():
            sec = ik.premortem_section(path.read_text(encoding="utf-8"))
            if sec is None:
                continue
            # 節の中でバッククォートされた slug 形の語のうち、族でも型でもないものは typo の疑い
            families = set(ik.dictionary_families(dictionary_text))
            for tok in ik._BACKTICK_SLUG_RE.findall(sec):
                if "-" in tok and tok not in known and tok not in families:
                    # 原理 slug・識別子（snake_case は対象外）は許す
                    assert tok in set(ik.principle_slugs(ik.principles_text_or_empty())), (
                        f"{path.name} の pre-mortem 節に辞書に無い slug `{tok}`"
                    )

    def test_index_renders_premortem_hits(self, entries, dictionary_text):
        text = ik.render_index(entries, dictionary_text)
        assert "### 14.5 pre-mortem の的中" in text
        hits = ik.premortem_hits(entries, dictionary_text)
        assert hits, "pre-mortem 節を持つ設計書が 1 つも無い"
        for h in hits:
            assert set(h["hit"]) <= set(h["predicted"])
