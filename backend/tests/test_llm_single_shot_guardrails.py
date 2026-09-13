"""単発 LLM 呼び出し共通基盤のガードレール。

正本:

- JSON 取り出し（フェンス除去・最外ブレース・LaTeX 修復・切り詰め復元）
  … ``core/llm_worker/single_shot.py``（worker 系統の
  ``core/llm_worker/client.py::parse_json_response`` と2本立て）
- U層計測つき embedding … ``core/llm_worker/embedding.py``
- 同期 API の日次上限 → 429 … ``api/quota.py``

固定する内容:

1. ``core/`` ・ ``api/`` 配下の**どのファイルも**、LLM 応答のフェンスを自前で
   剥がさない（allowlist に理由付きで挙げた正本だけが剥がす）。
2. 共通実装は「注入された LLM 関数を呼ぶ」設計を維持する（呼び出し側モジュールの
   ``generate_text`` を各テストが monkeypatch できる面を壊さない）。
3. embedding 共通実装は ``core.llm`` を遅延 import し ``usage_context`` 配下で呼ぶ。
4. 429 の共通実装は数値を返さない（I2 = 上限値・残数を detail に載せない）。
5. コピーが残っていないこと（撤去したデッドコードの名前が復活していないこと）。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from tests.guardrail_helpers import assert_source_forbids, extract_function_source  # noqa: E402

CORE_DIR = BACKEND / "core"
API_DIR = BACKEND / "api"

SINGLE_SHOT = CORE_DIR / "llm_worker" / "single_shot.py"
EMBEDDING = CORE_DIR / "llm_worker" / "embedding.py"
QUOTA = API_DIR / "quota.py"

#: 自前のフェンス処理を許すファイルと、その理由。
#: **新しい許可を足すときは、なぜ共通実装に委譲できないのかを書くこと。**
FENCE_ALLOWLIST: dict[Path, str] = {
    CORE_DIR / "llm_worker" / "client.py":
        "worker 系統の正本 parse_json_response（既存の外部シグネチャを保つ）",
    SINGLE_SHOT:
        "単発呼び出しの正本 extract_json / strip_code_fence",
    CORE_DIR / "help_kb" / "validator.py":
        "LLM 応答ではなく docs/manual の markdown を検証する（コードフェンス行の検出）",
}

#: LLM 応答テキストからフェンスを剥がす典型パターン。
_FENCE_PATTERNS = (
    re.compile(r'startswith\(\s*"""?`{3}'),
    re.compile(r"startswith\(\s*'''?`{3}"),
    re.compile(r'removeprefix\(\s*"`{3}'),
    re.compile(r'removesuffix\(\s*"`{3}'),
    re.compile(r'endswith\(\s*"`{3}'),
    re.compile(r'split\(\s*"`{3}"'),
)


def _python_sources(*dirs: Path) -> list[Path]:
    paths: list[Path] = []
    for directory in dirs:
        paths.extend(
            p for p in sorted(directory.rglob("*.py")) if "__pycache__" not in p.parts
        )
    return paths


class TestSingleFenceStripper:
    def test_no_ad_hoc_fence_stripping_outside_the_allowlist(self):
        offending: list[str] = []
        for path in _python_sources(CORE_DIR, API_DIR):
            if path in FENCE_ALLOWLIST:
                continue
            src = path.read_text(encoding="utf-8")
            for pattern in _FENCE_PATTERNS:
                for match in pattern.finditer(src):
                    line = src[: match.start()].count("\n") + 1
                    offending.append(f"{path.relative_to(BACKEND)}:{line}:{match.group()!r}")
        assert offending == [], (
            "LLM 応答のフェンス除去を自前で書いている箇所がある。"
            "core/llm_worker/single_shot.py::extract_json / strip_code_fence へ委譲すること: "
            f"{offending}"
        )

    def test_allowlist_entries_exist_and_are_justified(self):
        for path, reason in FENCE_ALLOWLIST.items():
            assert path.is_file(), f"allowlist の対象が存在しない: {path}"
            assert reason.strip(), f"allowlist に理由が無い: {path}"

    def test_json_loads_of_llm_text_goes_through_extract_json(self):
        """LLM 応答テキストの ``json.loads(..., strict=False)`` が残っていないこと。

        ``strict=False`` は移行元のコピー群の指紋（LLM 応答を緩く読むための緩和）。
        DB の JSONB や設定ファイルの読み込みには使われていないため、これが core/ ・
        api/ に現れたら共通実装への委譲漏れとみなす。
        """
        offending: list[str] = []
        for path in _python_sources(CORE_DIR, API_DIR):
            if path in (SINGLE_SHOT, CORE_DIR / "llm_worker" / "client.py"):
                continue
            src = path.read_text(encoding="utf-8")
            if "strict=False" in src:
                offending.append(str(path.relative_to(BACKEND)))
        assert offending == [], f"json.loads(..., strict=False) の残存: {offending}"


class TestInjectedLLMFunctions:
    """共通実装は LLM 関数を注入で受ける（呼び出し側の monkeypatch 面を保つ）。"""

    def test_single_shot_does_not_import_core_llm_at_module_level(self):
        tree = ast.parse(SINGLE_SHOT.read_text(encoding="utf-8"))
        module_level = [
            node
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        for node in module_level:
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            assert not any(n.startswith("core.llm") for n in names), (
                "single_shot.py がモジュールレベルで core.llm を掴むと、呼び出し側の "
                "generate_text を patch するテストが素通りする"
            )

    def test_single_shot_does_not_import_fastapi_or_sqlalchemy(self):
        assert_source_forbids(
            SINGLE_SHOT.read_text(encoding="utf-8"),
            ["fastapi", "sqlalchemy", "get_session"],
            context="core/llm_worker/single_shot.py",
        )

    def test_call_functions_are_parameters(self):
        src = SINGLE_SHOT.read_text(encoding="utf-8")
        assert "def json_call(" in src and "call: Callable" in src
        assert "def structured_call(" in src and "structured_fn: Callable" in src
        assert "text_fn: Callable" in src

    def test_repair_is_at_most_one_extra_call(self):
        """修復再呼び出しは高々1回（無限リトライを作らない）。"""
        body = extract_function_source(SINGLE_SHOT.read_text(encoding="utf-8"), "json_call")
        assert "attempts.append(" in body
        assert "while " not in body, "json_call にループでの再試行を持ち込まない"


class TestEmbeddingHelper:
    def test_lazy_import_and_usage_context(self):
        src = EMBEDDING.read_text(encoding="utf-8")
        body = extract_function_source(src, "embed_with_context")
        assert "from core.llm import generate_embeddings" in body, "遅延 import を関数内に保つ"
        assert "usage_context(feature" in body
        assert "generate_embeddings(" in body

    def test_no_module_level_llm_import(self):
        tree = ast.parse(EMBEDDING.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("core.llm"), (
                    "embedding.py は core.llm を関数内で遅延 import する"
                )

    def test_does_not_import_fastapi(self):
        assert_source_forbids(
            EMBEDDING.read_text(encoding="utf-8"),
            ["fastapi"],
            context="core/llm_worker/embedding.py",
        )


class TestQuotaHelper:
    def test_raises_429_without_numbers(self):
        src = QUOTA.read_text(encoding="utf-8")
        body = extract_function_source(src, "consume_daily_quota")
        assert "status_code=429" in body
        assert "detail=message" in body, "文言は呼び出し側の事実文をそのまま使う"
        # 上限値・残数を detail に混ぜない（I2 数値非表示）。
        assert "limit}" not in body and "{limit" not in body

    def test_consumes_after_state_dedupe(self):
        """``quota_state`` があれば同一リクエスト内の重複消費を防ぐ。"""
        body = extract_function_source(QUOTA.read_text(encoding="utf-8"), "consume_daily_quota")
        assert 'quota_state.get("consumed")' in body
        assert 'quota_state["consumed"] = True' in body

    def test_callers_delegate(self):
        """既存3経路が共通実装へ委譲していること（インラインの再実装を戻さない）。"""
        for rel in (
            "api/routes/learning.py",
            "api/routes/admin.py",
            "api/routes/lecture_studio/_shared.py",
        ):
            src = (BACKEND / rel).read_text(encoding="utf-8")
            assert "consume_daily_quota(" in src, rel
            assert "daily_key=(today_str()" not in src, f"{rel}: 日次上限の自前実装が残っている"


class TestDeadCodeStaysDeleted:
    """撤去したデッドコード（呼び出し元ゼロ）が復活していないこと。"""

    REMOVED_NAMES = (
        "extract_theory_components_from_chunk",
        "generate_missing_link_suggestions",
        "build_student_graph",
        "LLMStructuredOutputError",
    )

    def test_names_are_gone(self):
        offending: list[str] = []
        for path in _python_sources(CORE_DIR, API_DIR):
            src = path.read_text(encoding="utf-8")
            for name in self.REMOVED_NAMES:
                if f"def {name}" in src or f"class {name}" in src:
                    offending.append(f"{path.relative_to(BACKEND)}:{name}")
        assert offending == [], f"撤去済みのデッドコードが復活している: {offending}"

    def test_modules_are_gone(self):
        assert not (CORE_DIR / "chat.py").exists(), (
            "core/chat.py は呼び出し元ゼロで撤去済み。RAG 検索の正本は "
            "services.search_chunks_with_metadata（可視性ゲート必須）"
        )
        assert not (CORE_DIR / "graphs").exists(), (
            "core/graphs/（LangGraph StudentGraph）は本番ルート未接続のまま撤去済み"
        )
