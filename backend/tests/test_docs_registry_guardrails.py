"""docs ⇄ 実在物の網羅ガードレール。

正本: docs/architecture/feature_consolidation_proposals_2026-08-13.md §3
（「今回の不具合30件の大半は『テストが固定していない集計値・一覧』で起きた」）。

固定するのは以下6系統の「実在物 → ドキュメント」方向の網羅と、ドキュメント内部の
参照整合:

- ``TestMigrationCoverage``: ``backend/db/*.sql`` の全ファイル名が data-model.md に、
  全番号が layer_registry.md §3 に現れること。空き番号案内 = max+1。
- ``TestRouterCoverage``: ``backend/api/routes/`` の全ルーターが api.md に現れること。
- ``TestPipelineStageCoverage``: ``orchestrator.PIPELINE_STAGES`` の全ステージが
  pipeline/overview.md に現れること。
- ``TestFeatureDocIndexing``: ``docs/features/*.md`` が索引3文書のいずれかから
  参照されていること（孤児ドキュメントの構造的防止）。
- ``TestRelativeLinks`` / ``TestBacktickMarkdownReferences``: Markdown 相対リンクと
  バッククォート ``〜.md`` 参照の実在（リポジトリ外正本の禁止 = §1-5）。
- ``TestFeatureDocStateHeader``: 設計書冒頭の状態表記（§1-2）。

パース方針:
- **ドキュメントの軽微な文言編集で flake させない**。検査は「実在物の名前・番号が
  文書中に出現するか」という包含判定に留め、表の列構成や前後の説明文には依存しない
  （唯一の例外は §3 の migration 番号列で、範囲表記「013〜015, 017」を解釈する必要が
  あるため行頭の第1セルだけを見る）。
- 実在しないことが確定している参照は、理由コメント付きの**凍結 allowlist** に置く
  （減らす方向のみ。新規ファイルはここに追加しない）。
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_DIR = ROOT / "backend" / "db"
ROUTES_DIR = ROOT / "backend" / "api" / "routes"
DOCS_DIR = ROOT / "docs"
FEATURES_DIR = DOCS_DIR / "features"

DATA_MODEL_DOC = DOCS_DIR / "architecture" / "data-model.md"
LAYER_REGISTRY_DOC = DOCS_DIR / "architecture" / "layer_registry.md"
API_DOC = DOCS_DIR / "backend" / "api.md"
PIPELINE_DOC = DOCS_DIR / "pipeline" / "overview.md"
DOCS_INDEX_DOC = DOCS_DIR / "README.md"
CLAUDE_DOC = ROOT / "CLAUDE.md"

# リポジトリ走査から外すディレクトリ（依存物・キャッシュ・VCS 内部）。
_EXCLUDED_DIR_PARTS = frozenset(
    {".git", ".venv", "venv", "node_modules", "site-packages", "__pycache__", ".pytest_cache"}
)

# バッククォート ``〜.md`` 参照のうち、リポジトリ内に実体が存在しないことが確定して
# いるもの（凍結 allowlist — ここに追加しない。原本がコミットされたら削除する）。
_MISSING_MD_REFERENCE_ALLOWLIST = frozenset(
    {
        # オーナー配布の D層構想の原本。一度もコミットされておらず所在不明。
        # doc_review_findings_2026-08-13.md §1-2 が「原本未発掘」として注記済み。
        "episteme-graph_D層構想準備資料.md",
        # 宇宙物理骨格の外部仕様書（オーナー配布物）。同点検で欠落を確認済み。
        "episteme_graph_knowledge_landscape_astrophysics_spec.md",
        # 個人知識ネットワーク UX 提案書。`/Users/Shared/issues/` 配下の絶対パスで
        # 参照されている配布物で、リポジトリには取り込まれていない。
        "episteme_graph_personal_knowledge_network_ux_proposal.md",
    }
)

# 冒頭にラベル付き状態行（「状態:」「ステータス:」）を持たないレガシー設計書。
# 新規ファイルにはラベル付き状態行を必須とするため、この allowlist は**減らす方向のみ**
# （新しい docs/features/*.md をここに追加してはならない）。
_STATE_HEADER_LEGACY_ALLOWLIST = frozenset(
    {
        "admin.md",
        "assistant_common_infra_design.md",
        "auth-visibility.md",
        "contextual_figure_analysis_iterative_verification.md",
        "element_deliberation_workspace_review.md",
        "endorsement-sharing.md",
        "field_atlas_binding.md",
        "field_atlas_correction_reports.md",
        "field_atlas_detail_panel.md",
        "field_atlas_skeleton.md",
        "figure_concept_linking_design.md",
        "guidance_layer_design.md",
        "guided_figure_reanalysis_design.md",
        "image_pipeline_knowledge_library_design.md",
        "learning.md",
        "lecture_audio_generation_readiness.md",
        "lecture_slide_sync_design.md",
        "llm_model_selection_design.md",
        "llm_usage_metering_design.md",
        "personal_knowledge_network_review.md",
        "release_review_flow_design.md",
        "status_notification_design.md",
        "vision_expansion_ux_proposal_revised_2026-08-13.md",
    }
)

_STATE_HEADER_HEAD_CHARS = 1500

# ラベル付き状態行: 行頭（引用 > ・箇条書き - * ・強調 ** を許容）に
# 「状態:」または「ステータス:」が来ること。本文中に偶発的に現れる「状態」の
# 部分文字列一致では検査にならない（レビュー指摘により厳格化）。
_STATE_LABEL_RE = re.compile(r"(?m)^[>\-*\s]*(?:\*\*)?(?:状態|ステータス)(?:\*\*)?\s*[:：]")


def _has_state_header(path: Path) -> bool:
    return bool(_STATE_LABEL_RE.search(_read(path)[:_STATE_HEADER_HEAD_CHARS]))

_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\s*\)")
_BACKTICK_MD_RE = re.compile(r"`([^`\n]*?\.md)`")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_MIGRATION_FILE_RE = re.compile(r"^(\d{3})_")
# 「013〜015」「029〜033」等の範囲表記（全角波ダッシュ・チルダ・各種ダッシュを許容）。
_NUMBER_RANGE_RE = re.compile(r"(\d{3})\s*[〜～~\-–—]\s*(\d{3})")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_code(text: str) -> str:
    """コードフェンス・インラインコードを除去する（リンク検査の対象外にするため）。"""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"~~~.*?~~~", "", text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    return text


def _iter_repo_markdown() -> list[Path]:
    return sorted(
        p
        for p in ROOT.rglob("*.md")
        if not (set(p.parts) & _EXCLUDED_DIR_PARTS)
    )


def _iter_checked_markdown() -> list[Path]:
    """リンク検査の対象（ルート README / CLAUDE.md / docs 配下すべて）。"""
    paths = [p for p in (ROOT / "README.md", CLAUDE_DOC) if p.exists()]
    paths.extend(
        p for p in sorted(DOCS_DIR.rglob("*.md")) if not (set(p.parts) & _EXCLUDED_DIR_PARTS)
    )
    return paths


def _migration_files() -> list[Path]:
    return sorted(p for p in DB_DIR.glob("*.sql") if _MIGRATION_FILE_RE.match(p.name))


def _migration_numbers() -> set[int]:
    return {int(_MIGRATION_FILE_RE.match(p.name).group(1)) for p in _migration_files()}


def _expand_number_tokens(cell: str) -> set[int]:
    """「013〜015, 017」形式のセルを番号集合へ展開する（範囲展開 + カンマ列挙）。"""
    plain = cell.replace("**", "").replace("`", "")
    numbers: set[int] = set()
    for start, end in _NUMBER_RANGE_RE.findall(plain):
        lo, hi = int(start), int(end)
        if lo <= hi:
            numbers.update(range(lo, hi + 1))
    numbers.update(int(token) for token in re.findall(r"\b\d{3}\b", plain))
    return numbers


def _section(text: str, heading_prefix: str) -> str:
    """``## 3.`` のような**行頭**見出しから次の同レベル見出しまでを切り出す。

    部分文字列一致だと下位見出し（### 3.1）や本文中の言及に誤ヒットするため、
    行頭アンカーで探し、見つからなければ意図の読める assert で落とす。
    """
    m = re.search(rf"(?m)^{re.escape(heading_prefix)}", text)
    assert m is not None, f"見出し「{heading_prefix}」が見つからない（改名したらテストも追随すること）"
    tail = text[m.end() :]
    rel_end = tail.find("\n## ")
    return tail if rel_end == -1 else tail[:rel_end]


def _router_names() -> set[str]:
    """``backend/api/routes/`` 直下のモジュール名（stem）とパッケージ名の集合。"""
    names: set[str] = set()
    for path in sorted(ROUTES_DIR.glob("*.py")):
        if path.stem == "__init__":
            continue
        names.add(path.stem)
    for path in sorted(ROUTES_DIR.iterdir()):
        if path.is_dir() and path.name not in _EXCLUDED_DIR_PARTS:
            names.add(path.name)
    return names


class TestMigrationCoverage:
    """``backend/db/*.sql`` を正とした migration 一覧の網羅。"""

    def test_data_model_lists_every_migration_file(self):
        doc = _read(DATA_MODEL_DOC)
        missing = [p.name for p in _migration_files() if p.name not in doc]
        assert missing == [], (
            f"data-model.md に未記載の migration ファイル: {missing} "
            "（マイグレーション追加時は data-model.md の一覧にも1行足すこと）"
        )

    def test_layer_registry_section3_covers_every_migration_number(self):
        section = _section(_read(LAYER_REGISTRY_DOC), "## 3.")
        documented: set[int] = set()
        for line in section.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            first_cell = stripped.strip("|").split("|")[0]
            documented |= _expand_number_tokens(first_cell)
        missing = sorted(_migration_numbers() - documented)
        assert missing == [], (
            f"layer_registry.md §3 の帰属一覧に未記載の migration 番号: {missing} "
            "（範囲表記「013〜015, 017」も解釈済み）"
        )

    def test_next_free_number_matches_actual_max(self):
        doc = _read(LAYER_REGISTRY_DOC)
        announced = [int(n) for n in re.findall(r"次の空き番号は\s*\*{0,2}(\d{3})", doc)]
        assert announced, "layer_registry.md に「次の空き番号は NNN」の案内が見つからない"
        expected = max(_migration_numbers()) + 1
        assert set(announced) == {expected}, (
            f"空き番号案内 {announced} が実在 migration の max+1 = {expected} と一致しない"
        )


class TestRouterCoverage:
    """``backend/api/routes/`` を正としたルーター一覧の網羅。"""

    def test_api_doc_mentions_every_router(self):
        # 素の部分文字列一致（例: "atlas"）は既存本文に偶発一致するため、
        # 「<name>.py」または「routes/<name>」というモジュール名としての出現を要求する。
        doc = _read(API_DOC)
        missing = sorted(
            name
            for name in _router_names()
            if f"{name}.py" not in doc and f"routes/{name}" not in doc
        )
        assert missing == [], (
            f"docs/backend/api.md に未記載のルーター: {missing} "
            "（ルーター追加時は api.md にも節/行を足し、routes/<name>.py の形で明記すること）"
        )


class TestPipelineStageCoverage:
    """``orchestrator.PIPELINE_STAGES`` を正としたステージ一覧の網羅。"""

    def test_pipeline_overview_mentions_every_stage(self):
        from core.document_pipeline.orchestrator import PIPELINE_STAGES

        doc = _read(PIPELINE_DOC)
        missing = [stage for stage in PIPELINE_STAGES if stage not in doc]
        assert missing == [], (
            f"docs/pipeline/overview.md に未記載のパイプラインステージ: {missing} "
            "（ステージ追加時は overview.md の表にも1行足すこと）"
        )


class TestFeatureDocIndexing:
    """孤児ドキュメントの構造的防止（索引3文書のいずれかから参照されていること）。"""

    def test_every_feature_doc_is_referenced_from_an_index(self):
        indexes = [
            _read(path) for path in (DOCS_INDEX_DOC, LAYER_REGISTRY_DOC, CLAUDE_DOC)
        ]
        orphans = sorted(
            path.name
            for path in FEATURES_DIR.glob("*.md")
            if not any(path.name in index for index in indexes)
        )
        assert orphans == [], (
            f"索引（docs/README.md / layer_registry.md / CLAUDE.md）から参照されていない "
            f"docs/features 文書: {orphans}"
        )


class TestRelativeLinks:
    """Markdown 相対リンクの実在（コードフェンス・インラインコードは対象外）。"""

    def test_relative_links_resolve(self):
        broken: list[str] = []
        for path in _iter_checked_markdown():
            body = _strip_code(_read(path))
            for target in _MARKDOWN_LINK_RE.findall(body):
                if _SCHEME_RE.match(target) or target.startswith("#"):
                    continue
                raw = target.split("#", 1)[0].split("?", 1)[0]
                if not raw:
                    continue
                resolved = (path.parent / urllib.parse.unquote(raw)).resolve()
                if not resolved.exists():
                    broken.append(f"{path.relative_to(ROOT)} -> {target}")
        assert broken == [], f"リンク先が存在しない相対リンク: {broken}"


class TestBacktickMarkdownReferences:
    """バッククォートで包まれた ``〜.md`` 参照の実在（リポジトリ外正本の禁止）。

    運用注意: これから作る文書をバッククォートで先に言及すると本テストが落ちる。
    それが §1-5 の意図（実ファイルを先にコミットする）。allowlist は増やさないこと。
    照合はベースネームのみ（ディレクトリの正しさは相対リンク検査側の責務）。
    """

    def test_backtick_md_references_exist_in_repo(self):
        known = {path.name for path in _iter_repo_markdown()}
        missing: list[str] = []
        targets = [CLAUDE_DOC] + [
            p for p in sorted(DOCS_DIR.rglob("*.md")) if not (set(p.parts) & _EXCLUDED_DIR_PARTS)
        ]
        for path in targets:
            for ref in _BACKTICK_MD_RE.findall(_read(path)):
                name = ref.strip().split("/")[-1]
                # `*_design.md` `1x-admin-*.md` のようなパターン表記や、拡張子だけの
                # `.md`（地の文での言及）は実体を指さない。
                if "*" in name or name == ".md" or not name.endswith(".md"):
                    continue
                if name in known or name in _MISSING_MD_REFERENCE_ALLOWLIST:
                    continue
                missing.append(f"{path.relative_to(ROOT)}: `{ref}`")
        assert missing == [], (
            f"リポジトリ内に実体が無い .md 参照: {missing} "
            "（「正本」と呼ぶ文書は docs/ 配下にコミットすること）"
        )


class TestFeatureDocStateHeader:
    """設計書ライフサイクルの明示（冒頭に状態/ステータス表記を持つこと）。"""

    def test_new_feature_docs_declare_state(self):
        offenders = sorted(
            path.name
            for path in FEATURES_DIR.glob("*.md")
            if path.name not in _STATE_HEADER_LEGACY_ALLOWLIST and not _has_state_header(path)
        )
        assert offenders == [], (
            f"冒頭 {_STATE_HEADER_HEAD_CHARS} 文字にラベル付き状態行"
            f"（「状態:」/「ステータス:」）が無い docs/features 文書: {offenders} "
            "（規約は docs/development_checklist.md §5-2）"
        )

    def test_legacy_allowlist_has_no_stale_entries(self):
        stale = sorted(
            name
            for name in _STATE_HEADER_LEGACY_ALLOWLIST
            if not (FEATURES_DIR / name).exists() or _has_state_header(FEATURES_DIR / name)
        )
        assert stale == [], (
            f"状態行が付いた（または削除された）のに allowlist に残っている: {stale} "
            "（allowlist は減らす方向のみ — 解消したら削除すること）"
        )
