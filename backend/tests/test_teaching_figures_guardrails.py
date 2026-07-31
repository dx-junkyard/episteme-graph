"""教材図スタジオ（Teaching Figure Studio）ガードレールの自動テスト。

設計の正本は ``docs/features/teaching_figure_studio_design.md`` §10。**単体テストの再演では
なく**「将来の改変で不変条項（FG1〜FG9）が壊れたら落ちる」構造的検査（grep / AST /
関数ソースの順序）に限定する。挙動レベルの検証は既存の
``test_teaching_figures_sanitizer.py`` / ``test_teaching_figures_store.py`` /
``test_teaching_figures_api.py`` / ``test_figure_studio_ui_static.py`` が担う。

本ファイルが固定する項目:

1.  ``core/teaching_figures/`` が FastAPI / api 層を import しない（開発ルール2）
2.  ``routes/teaching_figures.py`` に DELETE ルートが無い（FG7: 行削除 API を作らない）
3.  サニタイザに stdlib ``xml.etree`` フォールバックが無く、``<!DOCTYPE`` / ``<!ENTITY``
    はパース前に拒否される（FG3）
4.  プロンプトに実データ捏造禁止（FG5）と grounding 制約が入っている
5.  ``suggest.py`` に ``anchor_excerpt`` の逐語（verbatim）検査がある（捏造ガード）
5b. candidate-only: 採用・採択の書き込みプリミティブが所有者ゲート付きの route から
    しか呼ばれない（FG2: 教員操作なしに adopted / accepted にならない）
6.  k-匿名を再定義せず、学習者の個人行テーブルを直接 SELECT しない（FG8）
7.  confidence の生値が API 応答構築コードに現れない（FG8）
8.  SVG 配信の2エンドポイントが nosniff + CSP sandbox の単一定数を通る（FG3）
9.  状態語彙（図 / 提案）が ``schema.py`` と route・migration 063 で一致している
9b. ルータが ``/api/admin`` にマウントされ、上限が設定値・環境変数で外に出ている（FG6）
10. 教材図のマージが document 走査の早期 return より前にある（§7.1）
11. 管理UI 3点セット健全性（``admin-figure-studio.js`` が網羅検査の走査対象）

外部サービス（PostgreSQL / MinIO / OpenAI）への実接続は行わない。
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

_TF_DIR = BACKEND / "core" / "teaching_figures"
_ROUTE_SRC = (BACKEND / "api" / "routes" / "teaching_figures.py").read_text(encoding="utf-8")
_LEARNING_SRC = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
_LECTURE_SRC = (BACKEND / "api" / "routes" / "lecture.py").read_text(encoding="utf-8")
_SANITIZER_SRC = (_TF_DIR / "sanitizer.py").read_text(encoding="utf-8")
_SUGGEST_SRC = (_TF_DIR / "suggest.py").read_text(encoding="utf-8")
_SIGNALS_SRC = (_TF_DIR / "signals.py").read_text(encoding="utf-8")
_STORE_SRC = (_TF_DIR / "store.py").read_text(encoding="utf-8")
_GENERATOR_SRC = (_TF_DIR / "generator.py").read_text(encoding="utf-8")
_MIGRATION_SQL = read_migration_sql(BACKEND, 63)


def _code_only(src: str) -> str:
    """コメントと docstring を除いたソース（**通常の文字列リテラルは残す**）。

    「実装が禁止語彙を参照していない」ことを検査したいのに、設計意図を説明する
    docstring・コメントの中の語で落ちる（＝ドキュメントを書くほど落ちる）のを避ける。
    一方で ``"concept_map"`` のような**ハードコードされた語彙リテラル**は検出できる
    必要があるため、文字列リテラルは docstring（文の先頭に現れる文字列）だけを落とす。

    コメント・docstring の文字だけを空白へ潰すので、残りのコードは**位置も隣接関係も
    元のまま**（``"foo("`` のような呼び出し形での検査がそのまま使える）。
    """
    lines = src.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    chars = list(src)

    def _blank(start: tuple[int, int], end: tuple[int, int]) -> None:
        begin = offsets[start[0] - 1] + start[1]
        finish = offsets[end[0] - 1] + end[1]
        for i in range(begin, min(finish, len(chars))):
            if chars[i] != "\n":
                chars[i] = " "

    at_statement_start = True
    for token in tokenize.generate_tokens(io.StringIO(src).readline):
        if token.type == tokenize.COMMENT or (
            token.type == tokenize.STRING and at_statement_start
        ):
            _blank(token.start, token.end)
        if token.type in (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT):
            at_statement_start = True
        elif token.type not in (tokenize.ENCODING, tokenize.COMMENT):
            at_statement_start = False
    return "".join(chars)


def _module_constants(src: str) -> dict:
    """モジュール直下の「名前 = リテラル」代入を取り出す（import せずに読む）。

    型注釈付き（``NAME: dict[str, str] = {...}``）と、既出の定数名だけで構成された
    タプル・リスト（``_FIGURE_STATUSES = (STATUS_DRAFT, ...)``）も解決する。
    """
    constants: dict = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        else:
            continue
        if not isinstance(target, ast.Name) or value is None:
            continue
        try:
            constants[target.id] = ast.literal_eval(value)
            continue
        except ValueError:
            pass
        if isinstance(value, (ast.Tuple, ast.List)) and all(
            isinstance(e, ast.Name) and e.id in constants for e in value.elts
        ):
            resolved = [constants[e.id] for e in value.elts]  # type: ignore[union-attr]
            constants[target.id] = (
                tuple(resolved) if isinstance(value, ast.Tuple) else resolved
            )
    return constants


def _functions(src: str) -> dict[str, str]:
    """``def`` ごとのソース断片（ネストした def も名前で引ける）。"""
    tree = ast.parse(src)
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = ast.get_source_segment(src, node) or ""
    return found


def _router_methods(src: str) -> set[str]:
    """``@router.<method>(`` デコレータの HTTP メソッド集合。"""
    return set(re.findall(r"@router\.(get|post|put|patch|delete)\(", src))


def _sql_table_names(src: str) -> set[str]:
    """ソース中の SQL 文字列が触るテーブル名（FROM / JOIN / INTO / UPDATE の直後）。"""
    names: set[str] = set()
    for pattern in (
        r"\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        r"\bJOIN\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        r"\bINTO\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        r"\bUPDATE\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    ):
        names.update(re.findall(pattern, src))
    return names


def _check_vocabulary(sql: str, column: str) -> list[str]:
    """``CHECK (<column> IN ('a','b',...))`` の語彙を出現順に返す。"""
    match = re.search(
        rf"CHECK\s*\(\s*{column}\s+IN\s*\(([\s\S]*?)\)\s*\)", sql
    )
    assert match, f"CHECK constraint for {column} not found in migration 063"
    return re.findall(r"'([^']*)'", match.group(1))


# ===========================================================================
# 1. core/teaching_figures/ が FastAPI / api 層を import しない（開発ルール2）
# ===========================================================================


class TestCoreIsFrameworkFree:
    def test_no_fastapi_or_api_layer_imports(self):
        assert_module_tree_does_not_import(
            _TF_DIR,
            ("fastapi", "starlette", "api", "routes", "dependencies", "services", "main"),
        )

    def test_core_does_not_open_its_own_db_session(self):
        """セッションは呼び出し側が管理する（採用時のトピック登録と同一トランザクション）。"""
        assert_module_tree_forbids(
            _TF_DIR, ("core.postgres", "get_session(", "SessionLocal")
        )

    def test_core_does_not_read_the_analysis_pipeline_layer(self):
        """FG1 A層非改変: ``src/episteme_graph/agents/`` を import しない。

        A層テーブル（``document_figures`` 等）を読まないことは
        :meth:`TestLearnerDataEgress.test_only_expected_tables_appear_in_sql` が
        SQL 側で固定する（ここは import 面のみ）。
        """
        assert_module_tree_does_not_import(
            _TF_DIR, ("episteme_graph", "core.document_pipeline")
        )


# ===========================================================================
# 2. 行削除 API が無い（FG7）
# ===========================================================================


class TestNoDeleteRoutes:
    def test_router_declares_no_delete_method(self):
        methods = _router_methods(_ROUTE_SRC)
        assert "delete" not in methods, "teaching figures router must not expose DELETE"
        assert methods == {"get", "post", "patch"}, (
            f"unexpected HTTP methods in teaching figures router: {sorted(methods)}"
        )

    def test_route_layer_issues_no_sql_delete(self):
        """削除は store の孤児掃除（コース物理削除に同乗）だけ。route からは発行しない。"""
        assert_source_forbids(
            _ROUTE_SRC, ("DELETE FROM", "delete_figures_for_course"),
            context="routes/teaching_figures.py",
        )

    def test_store_delete_is_scoped_to_course_purge(self):
        """store の DELETE は ``delete_figures_for_course`` 内のみ（2文: 図 + 提案）。"""
        assert _STORE_SRC.count("DELETE FROM") == 2
        purge = extract_function_source(_STORE_SRC, "delete_figures_for_course")
        assert purge.count("DELETE FROM") == 2


# ===========================================================================
# 3. サニタイザ: lxml 固定 + DOCTYPE/ENTITY の事前拒否（FG3）
# ===========================================================================


class TestSanitizerHasNoUnsafeFallback:
    def test_no_stdlib_xml_etree_fallback(self):
        """stdlib ``xml.etree`` はエンティティ攻撃に脆弱なのでフォールバックを持たない。"""
        assert_source_forbids(
            _SANITIZER_SRC,
            (
                "import xml.etree",
                "from xml.etree",
                "import xml.dom",
                "from xml.dom",
                "xml.sax",
                "minidom",
                "ElementTree",
                "try:\n    from lxml",
            ),
            context="core/teaching_figures/sanitizer.py",
        )

    def test_lxml_is_the_only_parser(self):
        assert "from lxml import etree" in _SANITIZER_SRC

    def test_parser_disables_entities_dtd_and_network(self):
        parser = _SANITIZER_SRC[_SANITIZER_SRC.index("etree.XMLParser(") :][:400]
        for flag in (
            "resolve_entities=False",
            "load_dtd=False",
            "no_network=True",
            "huge_tree=False",
        ):
            assert flag in parser, f"XMLParser must set {flag}"

    def test_doctype_and_entity_are_rejected_before_parsing(self):
        """``<!DOCTYPE`` / ``<!ENTITY`` の検査がパーサ呼び出しより手前にある。"""
        tokens = _module_constants(_SANITIZER_SRC)["_FORBIDDEN_RAW_TOKENS"]
        assert set(tokens) == {"<!doctype", "<!entity"}
        body = extract_function_source(_SANITIZER_SRC, "sanitize_svg")
        check_at = body.index("_FORBIDDEN_RAW_TOKENS")
        parse_at = body.index("etree.fromstring")
        assert check_at < parse_at, (
            "DOCTYPE / ENTITY の事前拒否はパース前に行う（billion laughs / XXE の入口）"
        )

    def test_rejection_is_an_exception_not_a_silent_strip(self):
        """除去して黙って保存する経路を作らない（拒否 → 422 の fail-closed）。"""
        assert_source_forbids(
            _SANITIZER_SRC, ("element.remove(", ".drop_tree(", "attrib.pop("),
            context="core/teaching_figures/sanitizer.py",
        )
        assert "raise SvgRejected(" in _SANITIZER_SRC


# ===========================================================================
# 4. プロンプトの捏造禁止制約（FG5）
# ===========================================================================


class TestPromptConstraints:
    def test_data_plot_constraint_forbids_fabricated_values(self):
        from core.teaching_figures import prompt

        assert "実測値・具体的な数値目盛りを捏造してはならない" in prompt.DATA_PLOT_CONSTRAINT
        assert "data_plot_schematic" in prompt.DATA_PLOT_CONSTRAINT

    def test_grounding_constraint_forbids_ungrounded_relations(self):
        from core.teaching_figures import prompt

        assert "資料に無い" in prompt.GROUNDING_CONSTRAINT

    def test_turn_instruction_carries_both_constraints(self):
        """生成・修正の全ターンが2つの制約を必ず含む（片方だけ落ちる改変を検出）。"""
        from core.teaching_figures import prompt

        instruction = prompt.build_turn_instruction()
        assert prompt.DATA_PLOT_CONSTRAINT in instruction
        assert prompt.GROUNDING_CONSTRAINT in instruction
        assert prompt.SVG_CONSTRAINT in instruction

    def test_turn_content_always_includes_the_instruction(self):
        from core.teaching_figures import prompt

        content = prompt.build_turn_content(
            grounding="", current_svg="", user_instruction="熱平衡の図を描いて"
        )
        assert prompt.DATA_PLOT_CONSTRAINT in content
        assert prompt.GROUNDING_CONSTRAINT in content

    def test_suggestion_prompt_requires_verbatim_excerpt(self):
        from core.teaching_figures import prompt

        content = prompt.build_suggestion_content(topic_title="t", source_text="本文")
        assert "逐語" in content
        assert "要約・翻訳・言い換えは禁止" in content

    def test_suggestion_prompt_states_learner_signals_are_aggregated(self):
        """FG8: 学習者情報はレンジ・段階ラベルの集計であることをプロンプトでも明示する。"""
        from core.teaching_figures import prompt

        content = prompt.build_suggestion_content(
            topic_title="t", source_text="本文", signal_lines=["この主張で…3-5人"]
        )
        assert "k-匿名" in content
        assert "個々の学習者について推測・言及しないでください" in content


# ===========================================================================
# 5. anchor_excerpt の逐語（verbatim）検査（捏造ガード）
# ===========================================================================


class TestAnchorExcerptVerbatimGuard:
    def test_validator_compares_against_normalized_source_text(self):
        body = extract_function_source(_SUGGEST_SRC, "validate_suggestions")
        assert "normalize_for_quote_match(source_text)" in _SUGGEST_SRC
        assert "normalize_for_quote_match(excerpt) not in haystack" in body

    def test_non_verbatim_excerpt_is_dropped(self):
        from core.teaching_figures.suggest import _SuggestionsOutput, validate_suggestions

        parsed = _SuggestionsOutput.model_validate(
            {
                "suggestions": [
                    {
                        "anchor_excerpt": "本文に無い言い換えの引用文です",
                        "gap_reason": "関係が追いづらい",
                        "figure_kind": "concept_map",
                        "figure_brief": "A と B の関係を描く",
                        "confidence": 0.9,
                    }
                ]
            }
        )
        rows, errors = validate_suggestions(
            parsed,
            course_id="c",
            topic_id="t",
            source_text="ここには別のことが書かれている本文があります",
            signal_lines=[],
            max_items=4,
        )
        assert rows == []
        assert errors and "逐語部分文字列ではありません" in errors[0]

    def test_verbatim_excerpt_survives_whitespace_normalization(self):
        from core.teaching_figures.suggest import _SuggestionsOutput, validate_suggestions

        parsed = _SuggestionsOutput.model_validate(
            {
                "suggestions": [
                    {
                        "anchor_excerpt": "熱平衡 と 定常状態 の違い",
                        "gap_reason": "用語の関係が追いづらい",
                        "figure_kind": "comparison",
                        "figure_brief": "2状態を対比して描く",
                        "confidence": 0.6,
                    }
                ]
            }
        )
        rows, errors = validate_suggestions(
            parsed,
            course_id="c",
            topic_id="t",
            source_text="本節では 熱平衡\n と 定常状態\tの違いを扱う。",
            signal_lines=[],
            max_items=4,
        )
        assert errors == []
        assert len(rows) == 1
        assert rows[0]["confidence"] == 0.6

    def test_out_of_vocabulary_figure_kind_is_dropped(self):
        from core.teaching_figures.suggest import _SuggestionsOutput, validate_suggestions

        parsed = _SuggestionsOutput.model_validate(
            {
                "suggestions": [
                    {
                        "anchor_excerpt": "熱平衡と定常状態の違い",
                        "gap_reason": "関係が追いづらい",
                        "figure_kind": "photo_realistic_render",
                        "figure_brief": "描く",
                        "confidence": 0.9,
                    }
                ]
            }
        )
        rows, errors = validate_suggestions(
            parsed,
            course_id="c",
            topic_id="t",
            source_text="熱平衡と定常状態の違いを扱う。",
            signal_lines=[],
            max_items=4,
        )
        assert rows == []
        assert errors and "figure_kind" in errors[0]

    def test_suggest_never_writes_to_the_database(self):
        """FG2: 候補の生成は組み立てるだけ（保存は route → store.create_suggestions）。"""
        assert_source_forbids(
            _SUGGEST_SRC,
            ("INSERT INTO", "UPDATE ", "DELETE FROM"),
            context="core/teaching_figures/suggest.py",
        )
        assert_source_forbids(
            _code_only(_SUGGEST_SRC),
            ("create_suggestions", "commit(", "session.execute("),
            context="core/teaching_figures/suggest.py (code only)",
        )


# ===========================================================================
# 5b. candidate-only: 教員操作なしに adopted / accepted にならない（FG2）
# ===========================================================================


class TestCandidateOnly:
    #: 図の採用・提案の確定を起こしうる書き込みプリミティブ。
    _WRITE_PRIMITIVES = (
        "create_teaching_figure(",
        "update_teaching_figure(",
        "set_suggestion_status(",
    )

    def test_write_primitives_are_only_called_from_the_teaching_figures_router(self):
        """AI 側（generator / suggest）や他レイヤーから採用が起きる経路を作らない。"""
        callers: list[str] = []
        for path in sorted((BACKEND / "core").rglob("*.py")) + sorted(
            (BACKEND / "api").rglob("*.py")
        ):
            if path == _TF_DIR / "store.py":
                continue  # 定義元
            code = _code_only(path.read_text(encoding="utf-8"))
            if any(primitive in code for primitive in self._WRITE_PRIMITIVES):
                callers.append(str(path.relative_to(BACKEND)))
        assert callers == ["api/routes/teaching_figures.py"], (
            f"teaching figure write primitives called from unexpected modules: {callers}"
        )

    def test_every_write_call_site_passes_the_owner_gate(self):
        """書き込み系は必ず ``course_data_for_owner``（所有者 / SYSTEM_ADMIN）を通る。"""
        offending: list[str] = []
        for name, body in _functions(_ROUTE_SRC).items():
            code = _code_only(body)
            if not any(primitive in code for primitive in self._WRITE_PRIMITIVES):
                continue
            if "course_data_for_owner" not in code:
                offending.append(name)
        assert offending == [], (
            f"teaching figure write path without an owner gate: {offending}"
        )

    def test_ai_modules_never_name_the_adopted_state(self):
        """生成・提案の core モジュールは配信状態（adopted / accepted）を扱わない。"""
        for path in (_TF_DIR / "generator.py", _TF_DIR / "suggest.py"):
            assert_source_forbids(
                _code_only(path.read_text(encoding="utf-8")),
                ("FIGURE_STATUS_ADOPTED", "SUGGESTION_STATUS_ACCEPTED", "adopted", "accepted"),
                context=str(path.name),
            )

    def test_suggestion_insert_never_accepts_a_caller_supplied_status(self):
        import inspect

        from core.teaching_figures.store import create_suggestions

        params = inspect.signature(create_suggestions).parameters
        assert "status" not in params
        body = extract_function_source(_STORE_SRC, "create_suggestions")
        assert '"status": schema.SUGGESTION_STATUS_CANDIDATE' in body


# ===========================================================================
# 6. k-匿名の再定義なし / 個人行テーブルを読まない（FG8）
# ===========================================================================


class TestLearnerDataEgress:
    #: 学習者の個人単位の行を持つテーブル（教材図スタジオは既存の k-匿名集約の
    #: 戻り値だけを読むので、これらを直接 SELECT してはならない）。
    _PERSONAL_ROW_TABLES = (
        "interest_traces",
        "learner_reconstructions",
        "learning_chat_history",
        "learning_states",
        "personal_graph",
        "reconstruction_items",
        "user_notifications",
    )

    def test_no_personal_row_table_is_queried(self):
        assert_module_tree_forbids(_TF_DIR, self._PERSONAL_ROW_TABLES)

    def test_only_expected_tables_appear_in_sql(self):
        """SQL が触るテーブルは自層の2枚 + claim ラベル解決の theory_claims のみ。"""
        tables: set[str] = set()
        for path in sorted(_TF_DIR.rglob("*.py")):
            tables |= _sql_table_names(path.read_text(encoding="utf-8"))
        assert tables <= {
            "course_teaching_figures",
            "teaching_figure_suggestions",
            "theory_claims",
        }, f"unexpected tables referenced from core/teaching_figures: {sorted(tables)}"

    def test_k_anonymity_is_not_redefined(self):
        """k=3・件数レンジの正本は ``core/privacy.py``（各集約モジュールが委譲済み）。"""
        assert_module_tree_forbids(
            _TF_DIR,
            (
                "K_ANONYMITY =",
                "K_ANONYMITY=",
                "def meets_k_anonymity",
                "def bucket_count_range",
                "MIN_COHORT",
            ),
        )

    def test_signal_reader_only_consumes_existing_aggregates(self):
        """学習者信号は R層 stumble / D層 naive_signal の戻り値からのみ組み立てる。"""
        assert "from core.reconstruction.stumble import get_stumble_summary" in _SIGNALS_SRC
        assert "from core.doubt.naive_signal import aggregate_naive_signals" in _SIGNALS_SRC
        # 段階ラベル・件数レンジは集約側が既に文字列化している（生数値を再計算しない）。
        assert_source_forbids(
            _SIGNALS_SRC, ("float(", "/ total", "round("), context="signals.py"
        )

    def test_signal_lines_are_the_only_learner_data_passed_to_the_llm(self):
        """提案プロンプトへ渡る学習者由来の材料は ``signal_lines`` だけ（FG8 の絞り口）。"""
        body = extract_function_source(_SUGGEST_SRC, "generate_suggestions")
        assert "signal_lines=lines" in body
        # 学習者データの供給口は呼び出し側（route）が渡す signal_lines のみ。
        # suggest 自身が集約関数を呼ぶ経路を作らない（k-匿名通過前の値に触れない）。
        assert_source_forbids(
            _code_only(_SUGGEST_SRC),
            ("aggregate_naive_signals", "get_stumble_summary", "topic_gap_signals"),
            context="core/teaching_figures/suggest.py (code only)",
        )


# ===========================================================================
# 7. confidence の生値を返さない（FG8）
# ===========================================================================


class TestConfidenceNeverLeaks:
    _RAW_KEY_RE = re.compile(r"[\"']confidence[\"']\s*:")

    def test_store_row_projection_emits_label_only(self):
        body = extract_function_source(_STORE_SRC, "_row_to_suggestion")
        assert "confidence_label" in body
        assert not self._RAW_KEY_RE.search(body), (
            "_row_to_suggestion must not put a raw confidence value in the response dict"
        )

    def test_route_response_projection_emits_label_only(self):
        body = extract_function_source(_ROUTE_SRC, "_suggestion_response")
        assert "confidence_label" in body
        assert not self._RAW_KEY_RE.search(body)

    def test_route_never_reads_the_raw_confidence_column(self):
        assert_source_forbids(
            _ROUTE_SRC,
            ('get("confidence")', '["confidence"]', "confidence=", "row.confidence"),
            context="routes/teaching_figures.py",
        )

    def test_figure_summary_carries_no_confidence(self):
        body = extract_function_source(_ROUTE_SRC, "_figure_summary")
        assert "confidence" not in body

    def test_quota_messages_contain_no_numbers(self):
        """FG8: 上限超過の事実文に回数・残数の数値を出さない。"""
        for fn in ("consume_figure_studio_quota", "consume_figure_suggest_quota"):
            body = extract_function_source(_ROUTE_SRC, fn)
            detail = re.search(r'detail="([^"]*)"', body)
            assert detail, f"{fn} must return a factual 429 message"
            assert not re.search(r"\d", detail.group(1)), (
                f"{fn}: 429 message must not contain numbers: {detail.group(1)}"
            )
            assert "429" in body


# ===========================================================================
# 8. SVG 配信のセキュリティヘッダ（FG3 第3防御）
# ===========================================================================


class TestSvgResponseHeaders:
    def test_headers_constant_has_nosniff_and_csp_sandbox(self):
        headers = _module_constants(_ROUTE_SRC)["SVG_RESPONSE_HEADERS"]
        assert headers.get("X-Content-Type-Options") == "nosniff"
        csp = headers.get("Content-Security-Policy") or ""
        assert "default-src 'none'" in csp
        assert "sandbox" in csp

    def test_headers_are_applied_only_for_svg_media_type(self):
        body = extract_function_source(_ROUTE_SRC, "figure_image_response")
        assert "SVG_RESPONSE_HEADERS" in body
        assert "SVG_CONTENT_TYPE" in body

    def test_headers_are_defined_in_exactly_one_place(self):
        """定義の二重化を防ぐ（学習者向けは import して使う）。"""
        assert "X-Content-Type-Options" not in _LEARNING_SRC
        assert "X-Content-Type-Options" not in _LECTURE_SRC
        assert "from routes.teaching_figures import" in _LEARNING_SRC
        assert "figure_image_response" in _LEARNING_SRC

    def test_both_delivery_endpoints_build_responses_through_the_helper(self):
        teacher = extract_function_source(_ROUTE_SRC, "get_course_teaching_figure_image")
        learner = extract_function_source(_LEARNING_SRC, "get_course_figure_image")
        for name, body in (("admin", teacher), ("learning", learner)):
            assert "figure_image_response(" in body, (
                f"{name} figure delivery must go through figure_image_response()"
            )
            assert not re.search(r"(?<!figure_image_)\bResponse\(", body), (
                f"{name} figure delivery must not construct a Response directly "
                "(headers would be skipped)"
            )

    def test_learner_delivery_requires_adopted_status(self):
        """FG4: draft / retired は学習者に出ない（4条件 AND の1つ）。"""
        body = extract_function_source(_LEARNING_SRC, "get_course_figure_image")
        assert "TEACHING_FIGURE_STATUS_ADOPTED" in body
        assert "_course_references_figure(course_data, figure_id)" in body
        assert body.count("status_code=404") >= 4


# ===========================================================================
# 9. 状態語彙の正本一致（schema.py ⇄ route ⇄ migration 063）
# ===========================================================================


class TestVocabularyStaysInSync:
    def test_route_status_constants_match_schema(self):
        from core.teaching_figures import schema

        constants = _module_constants(_ROUTE_SRC)
        assert constants["STATUS_DRAFT"] == schema.FIGURE_STATUS_DRAFT
        assert constants["STATUS_ADOPTED"] == schema.FIGURE_STATUS_ADOPTED
        assert constants["STATUS_RETIRED"] == schema.FIGURE_STATUS_RETIRED
        assert tuple(constants["_FIGURE_STATUSES"]) == schema.FIGURE_STATUSES
        assert constants["SUGGESTION_STATUS_CANDIDATE"] == schema.SUGGESTION_STATUS_CANDIDATE
        assert constants["SUGGESTION_STATUS_ACCEPTED"] == schema.SUGGESTION_STATUS_ACCEPTED
        assert constants["SUGGESTION_STATUS_DISMISSED"] == schema.SUGGESTION_STATUS_DISMISSED

    def test_route_reads_figure_kinds_from_schema(self):
        """図タイプ語彙は route にリテラル散在させず schema から import する。"""
        assert "FIGURE_KINDS" in _ROUTE_SRC
        from core.teaching_figures import schema

        # 語彙の実リテラルが route に散在していないこと（`other` は「分類を捏造しない」
        # 既定値として明示的に許可する）。
        forbidden = tuple(k for k in schema.FIGURE_KINDS if k != schema.FIGURE_KIND_OTHER)
        assert_source_forbids(
            _code_only(_ROUTE_SRC), forbidden, context="routes/teaching_figures.py (code only)"
        )

    def test_migration_check_constraints_match_schema(self):
        from core.teaching_figures import schema

        figures_sql, suggestions_sql = _MIGRATION_SQL.split(
            "CREATE TABLE IF NOT EXISTS teaching_figure_suggestions"
        )
        assert tuple(_check_vocabulary(figures_sql, "figure_kind")) == schema.FIGURE_KINDS
        assert tuple(_check_vocabulary(figures_sql, "status")) == schema.FIGURE_STATUSES
        assert tuple(_check_vocabulary(suggestions_sql, "signal_basis")) == schema.SIGNAL_BASES
        assert (
            tuple(_check_vocabulary(suggestions_sql, "status")) == schema.SUGGESTION_STATUSES
        )

    def test_migration_has_no_foreign_key_to_courses_and_no_cascade_delete(self):
        """course_id / topic_id に FK を張らない（既存慣例。孤児掃除は明示 DELETE）。"""
        assert "REFERENCES learning_courses" not in _MIGRATION_SQL
        assert _MIGRATION_SQL.count("REFERENCES users(id) ON DELETE SET NULL") == 2

    def test_audit_entity_type_comes_from_the_catalog(self):
        from core.schema import AUDIT_ENTITY_TEACHING_FIGURE, AUDIT_ENTITY_TYPES

        assert AUDIT_ENTITY_TEACHING_FIGURE in AUDIT_ENTITY_TYPES
        assert "from core.schema import AUDIT_ENTITY_TEACHING_FIGURE" in _ROUTE_SRC
        assert f'"{AUDIT_ENTITY_TEACHING_FIGURE}"' not in _ROUTE_SRC

    def test_llm_features_are_registered_in_the_usage_catalog(self):
        """U層 feature 語彙（``admin:figure_studio`` / ``admin:figure_suggest``）の登録。"""
        from core.llm_usage.schema import KNOWN_FEATURES
        from core.teaching_figures.generator import FEATURE_FIGURE_STUDIO
        from core.teaching_figures.suggest import FEATURE_FIGURE_SUGGEST

        assert FEATURE_FIGURE_STUDIO in KNOWN_FEATURES
        assert FEATURE_FIGURE_SUGGEST in KNOWN_FEATURES

    def test_model_scene_resolution_is_vision_free_and_fail_closed(self):
        """M層 scene は ``figure_studio``（route が fail-closed 検証を通す）。"""
        from core import llm_policy
        from core.teaching_figures.generator import FEATURE_FIGURE_STUDIO
        from core.teaching_figures.suggest import FEATURE_FIGURE_SUGGEST

        assert llm_policy.scene_for_feature(FEATURE_FIGURE_STUDIO) == (
            llm_policy.SCENE_FIGURE_STUDIO
        )
        assert llm_policy.scene_for_feature(FEATURE_FIGURE_SUGGEST) == (
            llm_policy.SCENE_FIGURE_STUDIO
        )
        assert "validate_model_for_scene(llm_policy.SCENE_FIGURE_STUDIO" in _ROUTE_SRC


# ===========================================================================
# 9b. ルータ登録と設定（実パス /api/admin/... + 上限の環境変数化）
# ===========================================================================


class TestRouterRegistrationAndSettings:
    _MAIN_SRC = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")
    _ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")

    def test_router_is_mounted_under_api_admin(self):
        assert "from routes.teaching_figures import router as _teaching_figures_router" in (
            self._MAIN_SRC
        )
        assert (
            'app.include_router(_teaching_figures_router, prefix="/api/admin")'
            in self._MAIN_SRC
        )

    def test_router_paths_are_relative_to_the_mount_prefix(self):
        """ルータ側にプレフィックスを二重に書かない（実パスが /api/admin/api/... になる）。"""
        paths = re.findall(r'@router\.(?:get|post|patch)\(\s*"([^"]+)"', _ROUTE_SRC)
        assert paths, "no routes found"
        assert all(p.startswith("/courses/") for p in paths), paths

    def test_cost_and_size_limits_come_from_settings_not_literals(self):
        from core.config import get_settings

        settings = get_settings()
        for name in (
            "figure_studio_llm_model",
            "figure_studio_max_calls_per_day",
            "figure_suggest_max_calls_per_day",
            "teaching_figure_max_svg_bytes",
            "teaching_figure_max_suggestions",
        ):
            assert hasattr(settings, name), f"missing setting: {name}"

    def test_limits_are_documented_in_env_example(self):
        for key in (
            "FIGURE_STUDIO_MAX_CALLS_PER_DAY",
            "FIGURE_SUGGEST_MAX_CALLS_PER_DAY",
            "TEACHING_FIGURE_MAX_SVG_BYTES",
            "TEACHING_FIGURE_MAX_SUGGESTIONS",
        ):
            assert key in self._ENV_EXAMPLE, f"{key} is not documented in .env.example"

    def test_both_llm_endpoints_are_rate_limited(self):
        """FG6: 対話と提案生成それぞれに独立した day-only ゲートを通す。"""
        turn = extract_function_source(_ROUTE_SRC, "figure_studio_turn")
        suggest = extract_function_source(_ROUTE_SRC, "generate_course_figure_suggestions")
        assert "consume_figure_studio_quota(" in turn
        assert "consume_figure_suggest_quota(" in suggest
        # 対話・提案の LLM 実行はそれぞれ1コール（+ 内部の修復1回）に留める。
        assert turn.count("run_figure_turn(") == 1
        assert suggest.count("generate_suggestions(") == 1


# ===========================================================================
# 10. 教材図のマージ順（§7.1: 早期 return より前）
# ===========================================================================


class TestTeachingFigureMergeOrder:
    def test_merge_happens_before_the_document_early_return(self):
        """ソース教材の無いコースでも生成図が解決できる（後置すると解決不能になる）。"""
        body = extract_function_source(_LECTURE_SRC, "_course_figures_index")
        merge_at = body.index("_load_course_teaching_figures(")
        early_return_at = body.index("if not document_ids:")
        assert merge_at < early_return_at, (
            "教材図のマージは document 走査の早期 return より前に置く（設計書 §7.1）"
        )

    def test_db_failure_path_keeps_the_merged_teaching_figures(self):
        body = extract_function_source(_LECTURE_SRC, "_course_figures_index")
        except_block = body[body.index("except Exception:") :]
        assert "return figures_by_id" in except_block, (
            "抽出図の取得に失敗しても教材図のマージ結果は返す（fail-soft）"
        )

    def test_learner_and_admin_indexes_share_one_resolution_rule(self):
        """解決規則を二重実装しない（image_url の組み立てだけ注入で差し替える）。"""
        for fn in ("_load_course_figures_by_id", "load_course_figures_index_for_admin"):
            body = extract_function_source(_LECTURE_SRC, fn)
            assert "_course_figures_index(" in body, f"{fn} must delegate to the shared index"

    def test_adopted_only_reaches_the_delivery_map(self):
        body = extract_function_source(_STORE_SRC, "adopted_figures_map")
        assert "schema.FIGURE_STATUS_ADOPTED" in body
        assert "svg_source" not in body, "配信マップに SVG 本体を載せない（画像 API 経由）"


# ===========================================================================
# 11. 管理UI 3点セット健全性
# ===========================================================================


class TestAdminUiThreePointSet:
    _INSPECT_TEST = BACKEND / "tests" / "test_admin_help_inspect_ui_static.py"
    _FIGURE_STUDIO_JS = ROOT / "frontend" / "public" / "js" / "admin-figure-studio.js"

    def test_figure_studio_js_exists(self):
        assert self._FIGURE_STUDIO_JS.exists()

    def test_figure_studio_js_is_scanned_by_the_anchor_coverage_test(self):
        """走査対象に無いと data-ui-anchor の双方向網羅検査を素通りしてしまう。"""
        src = self._INSPECT_TEST.read_text(encoding="utf-8")
        sources_block = src[src.index("_ADMIN_FRONTEND_SOURCES") :]
        sources_block = sources_block[: sources_block.index("]")]
        assert "admin-figure-studio.js" in sources_block

    def test_figure_studio_ui_anchors_are_registered(self):
        from core.help_kb.admin_ui_anchors import KNOWN_ADMIN_UI_ANCHOR_IDS

        used = set(re.findall(r'data-ui-anchor="([^"]+)"', self._FIGURE_STUDIO_JS.read_text(encoding="utf-8")))
        unknown = sorted(a for a in used if a not in KNOWN_ADMIN_UI_ANCHOR_IDS)
        assert unknown == [], f"未登録の data-ui-anchor: {unknown}"
