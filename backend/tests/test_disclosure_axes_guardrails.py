"""可視性6軸カタログのガードレール（``docs/features/disclosure_axes_design.md`` §1）。

構造的に守るもの:

- core の純粋性（``core/disclosure_axes.py`` が FastAPI / sqlalchemy / LLM を掴まない）
- **DA1** 宣言の出所（``basis``）が実在のファイルを指すこと・``ai_touchpoints[].feature`` が
  U層の ``KNOWN_FEATURES`` の要素であること
- **DA2** provider 名をカタログ・フロントに焼き込まないこと
- **DA3** ``GET /api/disclosure`` が ``_get_current_user`` を使い ``_require_teacher`` を
  使わないこと（送っている当事者である学習者も読めなければ告知にならない）
- **DA4** 学習者向け文言の禁止語彙（help_kb の student denylist のミラーが真に上位集合）
- **DA5** 同意 UI が無いこと（書き込みメソッド・同意ボタン・確認モーダルを作らない）
- **DA6** 学習者向け spec が「外部 AI を通らない経路」を必ず挙げること
- 各対話 UI に事実文の担体があること・学習画面の1画面レイアウト規律
- nginx の2 location（`/api/indicators` と同じ事故形の回避）
- 全 ``label`` がマニュアルに逐語で現れること
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from types import MappingProxyType

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_forbids,
    collect_route_pairs,
)

from core import disclosure_axes as da  # noqa: E402

_CORE_SRC_PATH = BACKEND / "core" / "disclosure_axes.py"
_ROUTE_SRC_PATH = BACKEND / "api" / "routes" / "disclosure.py"
_MAIN_SRC_PATH = BACKEND / "api" / "main.py"
_JS_PATH = ROOT / "frontend" / "public" / "js" / "disclosure-note.js"
_NGINX_PATH = ROOT / "frontend" / "nginx.conf"
_INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
_ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
_STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
_DESIGN_DOC = ROOT / "docs" / "features" / "disclosure_axes_design.md"
_STUDENT_MANUAL = ROOT / "docs" / "manual" / "student" / "02-student.md"
_TEACHER_MANUAL = ROOT / "docs" / "manual" / "teacher" / "10-admin-common.md"

#: 事実文を置く各対話 UI（担体の有無を検査する。data_kind もあわせて固定する）。
_DIALOGUE_UI_CARRIERS = {
    "index.html": (_INDEX_HTML, "learning_chat"),
    "admin.html": (_ADMIN_HTML, "course_materials"),
    "discuss.js": (ROOT / "frontend" / "public" / "js" / "discuss.js", "learning_chat"),
    "admin-assistant.js": (
        ROOT / "frontend" / "public" / "js" / "admin-assistant.js", "course_materials",
    ),
    "deliberation.js": (
        ROOT / "frontend" / "public" / "js" / "deliberation.js", "course_materials",
    ),
    "admin-graph-review.js": (
        ROOT / "frontend" / "public" / "js" / "admin-graph-review.js", "course_materials",
    ),
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """HTML コメントと JS の行コメントを落とす（説明のための言及と実装を混同しない）。"""
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("//")
    )


_CORE_SRC = _read(_CORE_SRC_PATH)
_ROUTE_SRC = _read(_ROUTE_SRC_PATH)


# ---------------------------------------------------------------------------
# 1. core の純粋性
# ---------------------------------------------------------------------------


class TestCorePurity:
    def test_core_module_does_not_import_web_db_or_llm(self):
        assert_module_tree_does_not_import(
            [_CORE_SRC_PATH],
            ["fastapi", "sqlalchemy", "core.llm", "core.postgres", "openai"],
        )

    def test_core_module_holds_no_values(self):
        assert_source_forbids(
            _CORE_SRC,
            ["get_session", "sa_text", "SELECT ", "requests.", "httpx"],
            context="core/disclosure_axes.py（宣言のみ・値を持たない）",
        )

    def test_core_module_does_not_import_help_kb(self):
        """denylist は import ではなくミラー + 本テストで固定する（推移的な純粋性を守る）。

        docstring / コメントでの言及は説明として必要なので、**import の形**で検査する。
        """
        assert_module_tree_does_not_import([_CORE_SRC_PATH], ["core.help_kb", "help_kb"])

    def test_dataclass_defaults_stay_python311_compatible(self):
        """共有テーブル（``MappingProxyType``）はフィールドではなく ``ClassVar`` で持つ。

        本番コンテナは Python 3.11 で、``dataclasses`` が「不変デフォルト」を
        ``__hash__ is None`` で判定する。``mappingproxy`` が hashable になったのは
        3.12 以降なので、3.13 のローカル venv では通るのにコンテナ起動時だけ
        ``ValueError: mutable default ... mappingproxy`` で落ちる（2026-09-12）。
        フィールドに出さなければ版差にかからない。
        """
        import dataclasses

        for name, obj in vars(da).items():
            if not (isinstance(obj, type) and dataclasses.is_dataclass(obj)):
                continue
            for field in dataclasses.fields(obj):
                default = field.default
                if default is dataclasses.MISSING:
                    continue
                assert not isinstance(default, (dict, list, set, MappingProxyType)), (
                    f"{name}.{field.name}: 可変（3.11 で unhashable）な既定値は "
                    "dataclass フィールドに置けません（ClassVar にするか default_factory）"
                )

    def test_core_module_is_importable_standalone(self):
        import subprocess

        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); "
             "from core import disclosure_axes as da; da.validate_catalog(); "
             "assert 'fastapi' not in sys.modules; "
             "assert 'sqlalchemy' not in sys.modules; print('ok')" % str(BACKEND)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout


# ---------------------------------------------------------------------------
# 2. DA1 — 宣言の出所が現物を指す
# ---------------------------------------------------------------------------


class TestDeclarationBasis:
    def test_every_basis_path_exists(self):
        missing = sorted(
            f"{spec.data_kind} -> {entry}"
            for spec in da.all_data_kinds()
            for entry in spec.basis
            if not (ROOT / entry.split("::", 1)[0]).exists()
        )
        assert missing == [], (
            f"実在しないモジュールを出所として挙げています: {missing}"
            "（DA1 — 宣言は現物から確認できることだけ）"
        )

    def test_every_basis_symbol_exists_in_its_module(self):
        """``module::symbol`` 形式の symbol がそのファイルに現れること（綴りの検出）。"""
        missing: list[str] = []
        for spec in da.all_data_kinds():
            for entry in spec.basis:
                if "::" not in entry:
                    continue
                module, symbol = entry.split("::", 1)
                src = _read(ROOT / module)
                if symbol not in src:
                    missing.append(f"{spec.data_kind} -> {entry}")
        assert missing == [], f"出所のモジュールに無いシンボル: {missing}"

    def test_every_touchpoint_feature_is_a_known_usage_feature(self):
        """宣言した通過点が U層で計測されている feature を指すこと。"""
        from core.llm_usage.schema import KNOWN_FEATURES

        unknown = sorted(
            f"{spec.data_kind} -> {t.feature}"
            for spec in da.all_data_kinds()
            for t in spec.ai_touchpoints
            if t.feature not in KNOWN_FEATURES
        )
        assert unknown == [], (
            f"KNOWN_FEATURES に無い feature を通過点として宣言しています: {unknown}"
        )

    def test_design_doc_paths_exist(self):
        missing = sorted(
            f"{spec.data_kind} -> {spec.design_doc}"
            for spec in da.all_data_kinds()
            if not (ROOT / spec.design_doc).exists()
        )
        assert missing == [], f"存在しない設計書を指している spec: {missing}"

    def test_trace_registry_kinds_are_covered_by_the_trace_declaration(self):
        """痕跡の宣言が ``trace_registry`` の露出3宣言と食い違わないこと。

        - 教員向け集約の対象になり得る kind が1つでもあれば、痕跡の spec は
          「匿名の集計として担当教員に届く」と言っていなければならない。
        - 本人のみ可視の建前（PN-1）に反して「教員が個々の記録を読める」と書いていない。
        """
        from core import trace_registry

        spec = da.get_data_kind("learner_traces")
        assert spec is not None
        aggregated = [
            k for k, s in trace_registry.TRACE_KINDS.items()
            if s.teacher_dashboard or s.teacher_aggregations
        ]
        assert aggregated, "trace_registry に教員向け集約対象の kind が1つも無い（走査が壊れている）"
        assert "集計" in spec.aggregation and "担当教員" in spec.aggregation
        assert "あなただけ" in spec.audience

    def test_k_anonymity_is_referenced_not_redefined(self):
        """k の正本は ``core/privacy.py``。カタログ・ルーターに数値を書かない。"""
        from core import privacy

        assert privacy.K_ANONYMITY == 3
        assert "K_ANONYMITY = " not in _CORE_SRC
        assert "privacy.K_ANONYMITY" in _ROUTE_SRC
        for spec in da.all_data_kinds():
            assert "k=3" not in spec.aggregation, spec.data_kind


# ---------------------------------------------------------------------------
# 3. DA2 — 宛先は provider だけ・焼き込まない
# ---------------------------------------------------------------------------


class TestDestination:
    def test_provider_is_resolved_at_request_time(self):
        assert "llm_provider" in _ROUTE_SRC
        assert "provider_label" in _ROUTE_SRC

    def test_js_does_not_hardcode_the_fact_line_or_provider(self):
        """フロントに文言・provider 名を焼き込まない（サーバの note をそのまま描く）。"""
        js = _read(_JS_PATH)
        for label in set(da.PROVIDER_LABELS.values()):
            assert label not in js, f"disclosure-note.js に {label} が焼き込まれている"
        assert "送られます" not in js, "事実文がフロントに焼き込まれている（サーバが正本）"
        assert "item.note" in js

    def test_js_never_reads_numeric_fields(self):
        js = _read(_JS_PATH)
        forbidden = (".count", ".total", ".tokens", ".cost", ".score", ".confidence")
        offenders = sorted(term for term in forbidden if term in js)
        assert offenders == [], f"宣言から数値らしいフィールドを読んでいます: {offenders}"

    def test_core_declares_the_forbidden_terms(self):
        for term in ("gpt-", "トークン", "$"):
            assert term in da.FORBIDDEN_DESTINATION_TERMS


# ---------------------------------------------------------------------------
# 4. DA3 / DA5 — 全当事者に公開・書き込みを作らない
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registered_pairs() -> set[tuple[str, str]]:
    pytest.importorskip("fastapi")
    from api.main import app

    return collect_route_pairs(app)


class TestPublicAccess:
    def test_router_uses_current_user_not_role_gates(self):
        assert "Depends(_get_current_user)" in _ROUTE_SRC
        code = "\n".join(
            line for line in _ROUTE_SRC.splitlines() if not line.lstrip().startswith("#")
        )
        for term in ("Depends(_require_teacher)", "Depends(_require_system_admin)"):
            assert term not in code, (
                f"routes/disclosure.py が {term} を使っています"
                "（DA3: 送っている当事者である学習者も読めなければ告知にならない）"
            )
        for line in code.splitlines():
            if line.startswith(("from ", "import ")):
                assert "_require_teacher" not in line
                assert "_require_system_admin" not in line

    def test_router_is_read_only(self):
        for verb in (".post(", ".put(", ".patch(", ".delete("):
            assert f"@router{verb}" not in _ROUTE_SRC, (
                f"カタログはコードが正本 — API から書き換えられない（{verb} を作らない）"
            )

    def test_router_is_registered_in_main(self):
        src = _read(_MAIN_SRC_PATH)
        assert "from routes import disclosure as disclosure_routes" in src
        assert "app.include_router(disclosure_routes.router)" in src

    def test_route_prefix_is_not_under_admin(self):
        assert 'APIRouter(prefix="/api/disclosure"' in _ROUTE_SRC
        assert "/api/admin/disclosure" not in _ROUTE_SRC

    def test_only_get_methods_are_registered(self, registered_pairs):
        methods = {
            method for path, method in registered_pairs
            if path.startswith("/api/disclosure")
        }
        assert methods == {"GET"}, f"disclosure に GET 以外が登録されています: {methods}"

    def test_both_routes_are_registered(self, registered_pairs):
        paths = {path for path, _m in registered_pairs}
        assert "/api/disclosure" in paths
        assert "/api/disclosure/{data_kind}" in paths


class TestNoConsentUi:
    def test_js_has_no_consent_or_dismiss_controls(self):
        """DA5: 告知であって同意ではない。ボタン・チェックボックス・モーダルを作らない。

        （コメントでの言及は説明として必要なので、コメントを外した実装だけを見る。）
        """
        js = _strip_comments(_read(_JS_PATH))
        for term in ("同意", "承諾", "checkbox", "<button", 'createElement("button")',
                     "modal", "dialog"):
            assert term not in js, f"disclosure-note.js に同意/操作要素らしいもの: {term!r}"

    def test_carriers_are_plain_paragraphs(self):
        """担体の近傍に同意ボタン・チェックボックスを置かない。"""
        for name, (path, _kind) in _DIALOGUE_UI_CARRIERS.items():
            src = _strip_comments(_read(path))
            for m in re.finditer(r"data-disclosure-note|disclosure-note-slot", src):
                window = src[max(0, m.start() - 400): m.end() + 400]
                for term in ("同意", "承諾", 'type="checkbox"'):
                    assert term not in window, f"{name}: 担体の近傍に「{term}」がある"

    def test_js_fails_soft_and_does_not_poll(self):
        js = _read(_JS_PATH)
        assert ".catch(" in js
        assert "setInterval" not in js


# ---------------------------------------------------------------------------
# 5. DA4 / DA6 — 学習者向けの文言
# ---------------------------------------------------------------------------


class TestLearnerFacingText:
    def test_denylist_mirror_covers_help_kb(self):
        """ミラーが help_kb の student denylist の上位集合であること（黙った分裂の検出）。"""
        from core.help_kb.validator import STUDENT_DENYLIST

        missing = sorted(set(STUDENT_DENYLIST) - set(da.LEARNER_TEXT_DENYLIST))
        assert missing == [], (
            f"help_kb の student denylist に追加された語彙がミラーに無い: {missing}"
            "（core/disclosure_axes.py の LEARNER_TEXT_DENYLIST に足すこと）"
        )

    def test_no_learner_facing_text_contains_denylisted_terms(self):
        offenders: list[str] = []
        for spec in da.all_data_kinds():
            if not spec.learner_facing:
                continue
            for term in da.LEARNER_TEXT_DENYLIST:
                if any(term in text for text in spec._visible_texts()):
                    offenders.append(f"{spec.data_kind}:{term}")
        assert offenders == [], f"学習者向け文言の禁止語彙: {offenders}"

    def test_learner_facing_specs_declare_paths_without_ai(self):
        """DA6: 「送られる」だけを並べない。"""
        missing = sorted(
            spec.data_kind for spec in da.all_data_kinds()
            if spec.learner_facing and not spec.without_ai
        )
        assert missing == [], f"without_ai が空の学習者向け宣言: {missing}"

    def test_evaluation_axis_states_the_non_use(self):
        """原則5 の継承: 評価に使わないことを全 spec が明言する。"""
        for spec in da.all_data_kinds():
            assert "使いません" in spec.evaluation_use, spec.data_kind

    def test_no_alarming_or_pushy_wording(self):
        """原則12: 事実文であって警告・督促ではない。"""
        forbidden = ("警告", "注意してください", "危険", "必ず確認してください", "ただちに")
        offenders = [
            f"{spec.data_kind}:{term}"
            for spec in da.all_data_kinds()
            for term in forbidden
            if any(term in text for text in spec._visible_texts())
        ]
        assert offenders == [], f"警告・督促の語彙: {offenders}"


# ---------------------------------------------------------------------------
# 6. UI の担体・レイアウト規律・nginx
# ---------------------------------------------------------------------------


class TestFrontendCarriers:
    def test_module_is_exposed(self):
        js = _read(_JS_PATH)
        assert "window.DisclosureNote" in js
        for fn in ("init", "mount", "mountAll", "factLine", "invalidate"):
            assert fn + ":" in js

    @pytest.mark.parametrize("name", sorted(_DIALOGUE_UI_CARRIERS))
    def test_every_dialogue_ui_has_a_carrier(self, name):
        path, kind = _DIALOGUE_UI_CARRIERS[name]
        src = _read(path)
        has_static = f'data-disclosure-note="{kind}"' in src
        has_mount = "DisclosureNote.mount(" in src and f'"{kind}"' in src
        assert has_static or has_mount, (
            f"{name} に事実文の担体がありません（静的な data-disclosure-note か "
            "DisclosureNote.mount() のどちらかを置くこと）"
        )

    def test_scripts_are_loaded_before_their_consumers(self):
        index_html = _read(_INDEX_HTML)
        assert "/js/disclosure-note.js" in index_html
        assert index_html.index("/js/disclosure-note.js") < index_html.index("/js/app.js")
        admin_html = _read(_ADMIN_HTML)
        assert "/js/disclosure-note.js" in admin_html
        for consumer in ("/js/deliberation.js", "/js/admin.js?"):
            assert admin_html.index("/js/disclosure-note.js") < admin_html.index(consumer)

    def test_modules_are_injected_with_apifetch(self):
        app_js = _read(ROOT / "frontend" / "public" / "js" / "app.js")
        admin_js = _read(ROOT / "frontend" / "public" / "js" / "admin.js")
        assert "DisclosureNote.init({ apiFetch: apiFetch })" in app_js
        assert "DisclosureNote.init({ apiFetch: apiFetch })" in admin_js

    def test_learning_note_does_not_shrink(self):
        """学習画面は1画面レイアウト — 下段の事実文は縮まない（潰れて読めなくならない）。"""
        css = _read(_STYLES_CSS)
        for selector in (".disclosure-note", ".disclosure-note-slot"):
            block = css[css.index(selector + " {"):]
            block = block[: block.index("}")]
            assert "flex: 0 0 auto;" in block, f"{selector} が縮む設定になっている"

    def test_learning_carrier_sits_next_to_the_composer(self):
        html = _read(_INDEX_HTML)
        carrier = html.index('data-disclosure-note="learning_chat"')
        composer = html.index('<div class="ia">')
        assert carrier < composer, "事実文が composer より後ろにある"
        assert composer - carrier < 800, "事実文が入力欄から離れすぎている"

    def test_carriers_have_no_ui_anchor(self):
        """事実の段落であって操作要素ではない（アンカー3点セットの対象にしない）。"""
        for name, (path, _kind) in _DIALOGUE_UI_CARRIERS.items():
            src = _read(path)
            for m in re.finditer(r"data-disclosure-note|disclosure-note-slot", src):
                # 担体そのもののタグ（次の '>' まで）だけを見る。近傍の入力欄・送信
                # ボタンはアンカーを持っていて当然なので窓を広げない。
                end = src.find(">", m.end())
                tag = src[m.start(): end if end != -1 else m.end()]
                assert "data-ui-anchor" not in tag, f"{name}: 担体にアンカーが付いている"


class TestNginx:
    def test_both_locations_exist(self):
        conf = _read(_NGINX_PATH)
        assert "location /api/disclosure/ {" in conf
        assert "location = /api/disclosure {" in conf

    def test_locations_proxy_to_the_api_server(self):
        conf = _read(_NGINX_PATH)
        assert "proxy_pass http://api-server:8001/api/disclosure/;" in conf
        assert "proxy_pass http://api-server:8001/api/disclosure;" in conf


# ---------------------------------------------------------------------------
# 7. 文書との突合
# ---------------------------------------------------------------------------


class TestDocumentation:
    def test_design_doc_declares_the_invariants(self):
        doc = _read(_DESIGN_DOC)
        assert "状態:" in doc[:1500]
        for tag in ("DA1", "DA2", "DA3", "DA4", "DA5", "DA6"):
            assert tag in doc, f"設計書に {tag} が無い"

    def test_design_doc_lists_every_data_kind_and_axis(self):
        doc = _read(_DESIGN_DOC)
        for spec in da.all_data_kinds():
            assert spec.data_kind in doc, f"設計書に無い data_kind: {spec.data_kind}"
        for axis in da.AXES:
            assert axis.id in doc, f"設計書に無い軸: {axis.id}"

    def test_every_label_appears_verbatim_in_a_manual(self):
        blobs = [_read(_STUDENT_MANUAL), _read(_TEACHER_MANUAL)]
        missing = sorted(
            spec.label for spec in da.all_data_kinds()
            if not any(spec.label in blob for blob in blobs)
        )
        assert missing == [], (
            f"マニュアルに逐語で現れないデータ種別名: {missing}"
            "（student/02-student.md か teacher/10-admin-common.md に書く）"
        )

    def test_learner_facing_labels_are_in_the_student_manual(self):
        student = _read(_STUDENT_MANUAL)
        missing = sorted(
            spec.label for spec in da.all_data_kinds()
            if spec.learner_facing and spec.label not in student
        )
        assert missing == [], f"学生向けマニュアルに無いデータ種別名: {missing}"

    def test_manual_sections_have_explicit_anchors(self):
        assert "{#disclosure}" in _read(_STUDENT_MANUAL)
        assert "{#disclosure}" in _read(_TEACHER_MANUAL)

    def test_student_section_states_the_external_transfer(self):
        text = _read(_STUDENT_MANUAL)
        section = text[text.index("{#disclosure}"):]
        for phrase in ("外部の AI プロバイダ", "録音した音声", "成績評価には使いません",
                       "外部の AI を通らない", "持ち出せます"):
            assert phrase in section, f"学生向け節に「{phrase}」が無い"

    def test_student_manual_has_no_denylisted_terms(self):
        from core.help_kb.validator import STUDENT_DENYLIST

        text = _read(_STUDENT_MANUAL)
        offenders = sorted(term for term in STUDENT_DENYLIST if term in text)
        assert offenders == [], f"学生向けマニュアルの禁止語彙: {offenders}"

    def test_student_section_does_not_ask_for_consent(self):
        text = _read(_STUDENT_MANUAL)
        section = text[text.index("{#disclosure}"):]
        for term in ("同意して", "利用規約に同意", "同意が必要"):
            assert term not in section, f"学生向け節が同意を求めています: {term}"

    def test_api_doc_mentions_the_router(self):
        doc = _read(ROOT / "docs" / "backend" / "api.md")
        assert "routes/disclosure.py" in doc
        assert "/api/disclosure" in doc

    def test_claude_md_has_a_section(self):
        doc = _read(ROOT / "CLAUDE.md")
        assert "disclosure_axes" in doc
        assert "disclosure_axes_design.md" in doc
