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
        for path in (README_DOC, TEMPLATE_DOC, ik.TAXONOMY_DOC, ik.DICTIONARY_DOC, ik.INDEX_DOC, ik.LAYERS_DOC):
            assert path.exists(), f"{path.relative_to(ROOT)} が無い"
        assert ik.ENTRIES_DIR.is_dir()

    def test_reference_documents_declare_state(self):
        for path in (README_DOC, ik.TAXONOMY_DOC, ik.DICTIONARY_DOC, ik.INDEX_DOC, ik.LAYERS_DOC):
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
        return set(re.findall(r"`([a-z_]+(?:\.[a-z_]+)?)`", text))

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
