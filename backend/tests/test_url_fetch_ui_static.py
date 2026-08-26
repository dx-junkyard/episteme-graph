"""URL指定による教材取得（migration 070）のフロント静的ガードレール。

正本: `docs/features/url_material_upload_design.md`（UF1〜UF6）。

固定するのは以下:

- 教材管理タブの「URLから取得」リンク・モーダル・送信ボタンの担体（id / data-ui-anchor）が
  実在すること（管理UI 3点セットの第3点）。
- **許可リストが空のときに送信ボタンを無効化し、理由の事実文を添える**こと（UF1/UF2 の
  UI 面。無効化されたボタンだけを見せない = 管理画面の共通規約）。
- 202 受理後は既存のファイルアップロードと**同一の合流点** `handleUploadAccepted` へ
  入ること（UF5。URL 経路に第2のポーリング/一覧更新実装を作らない）。
- SYSTEM_ADMIN 向け許可ドメイン管理区画が「AIモデル」タブ（`#tab-llm-models`）へ
  append される実装であること（教材管理タブは SYSTEM_ADMIN では非表示のため）。

すべて静的解析（部分文字列・正規表現）。外部 API / 実 DOM は使わない。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT / "frontend" / "public"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"
ADMIN_HTML = FRONTEND_DIR / "admin.html"

# 実装済みの UI 論理アンカー（core/help_kb/admin_ui_anchors.py にも登録済み）。
MATERIALS_ANCHORS = (
    "materials.url-upload",
    "materials.url-upload-modal",
    "materials.url-upload-submit",
)
LLM_MODELS_ANCHORS = (
    "llm-models.url-fetch-domains",
    "llm-models.url-fetch-domain-add",
    "llm-models.url-fetch-domain-remove",
)


# 属性直書き（`data-ui-anchor="X"`）と setAttribute（`"data-ui-anchor", "X"`）の両形。
_ANCHOR_RE = re.compile(r'data-ui-anchor(?:="|",\s*")([^"]+)"')


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_function(src: str, name: str) -> str:
    marker = "function " + name
    assert marker in src, f"{name} が見つかりません"
    after = src.split(marker, 1)[1]
    # 次の同階層 function 定義の手前までを本体とみなす（admin.js は 2 スペースインデント）。
    return after.split("\n  function ")[0]


# ---------------------------------------------------------------------------
# ① 6アンカーがフロントソースに担体を持つ
# ---------------------------------------------------------------------------


class TestUiAnchorsPresent:
    def setup_method(self):
        self.blob = _read(ADMIN_HTML) + "\n" + _read(ADMIN_JS)
        self.anchors = _ANCHOR_RE.findall(self.blob)

    def test_all_six_anchors_have_a_carrier(self):
        missing = [
            anchor
            for anchor in MATERIALS_ANCHORS + LLM_MODELS_ANCHORS
            if anchor not in self.anchors
        ]
        assert missing == [], f"data-ui-anchor 担体が無いアンカー: {missing}"

    def test_url_upload_link_lives_in_admin_html(self):
        """アップロードゾーン内のリンクは静的 HTML 側（動的生成ではない）。"""
        html = _read(ADMIN_HTML)
        assert 'id="url-upload-link"' in html
        assert 'data-ui-anchor="materials.url-upload"' in html

    def test_each_anchor_used_once(self):
        """1属性1ID 規約: 同じ論理IDを複数の担体に付けない。"""
        for anchor in MATERIALS_ANCHORS + LLM_MODELS_ANCHORS:
            count = self.anchors.count(anchor)
            assert count == 1, f"{anchor} の担体が {count} 箇所"


# ---------------------------------------------------------------------------
# ② モーダルの要素 id
# ---------------------------------------------------------------------------


class TestUrlUploadModal:
    def setup_method(self):
        self.js = _read(ADMIN_JS)

    def test_modal_element_ids_exist(self):
        for element_id in ("url-upload-modal", "url-upload-input", "url-upload-submit"):
            assert f'"{element_id}"' in self.js, f"{element_id} が admin.js に見つかりません"

    def test_link_opens_modal_instead_of_fetching_immediately(self):
        body = _extract_function(self.js, "initUrlUpload")
        assert "url-upload-link" in body
        assert "openUrlUploadModal()" in body
        # リンク押下で直接 API を叩かない（確認の余地を残す）。
        assert "upload-from-url" not in body

    def test_submit_posts_to_upload_from_url(self):
        body = _extract_function(self.js, "submitUrlUpload")
        assert '"/admin/materials/upload-from-url"' in body
        assert '"POST"' in body


# ---------------------------------------------------------------------------
# ③ 許可リストが空のときの無効化 + 理由の事実文（UF1 / UF2 の UI 面）
# ---------------------------------------------------------------------------


class TestEmptyAllowlistDisablesSubmit:
    def setup_method(self):
        self.js = _read(ADMIN_JS)

    def test_submit_button_starts_disabled(self):
        """許可リストを確認する前は押せない（fail-closed の初期状態）。"""
        m = re.search(r'id="url-upload-submit"[^>]*', self.js)
        assert m, "url-upload-submit ボタンの定義が見つかりません"
        assert "disabled" in m.group(0)

    def test_empty_allowlist_disables_with_reason(self):
        body = _extract_function(self.js, "loadUrlUploadDomains")
        assert "URLからの取得は、管理者が取得先ドメインを許可すると利用できます。" in body
        # 空判定の分岐で「無効化 + 理由」を渡していること。
        assert "_urlUploadSetSubmitEnabled(false" in body

    def test_fetch_failure_also_disables_with_reason(self):
        body = _extract_function(self.js, "loadUrlUploadDomains")
        assert "許可ドメインを取得できませんでした。" in body

    def test_disable_helper_always_writes_reason_note(self):
        body = _extract_function(self.js, "_urlUploadSetSubmitEnabled")
        assert "url-upload-domains-note" in body
        assert "btn.disabled" in body

    def test_submit_refuses_while_disabled(self):
        """無効化されたボタンからの送信経路を塞ぐ（Enter キー経由の迂回防止）。"""
        body = _extract_function(self.js, "submitUrlUpload")
        assert "btn.disabled" in body


# ---------------------------------------------------------------------------
# ④ 受理後の合流点は既存アップロードと同一（UF5）
# ---------------------------------------------------------------------------


class TestAcceptedPathIsShared:
    def setup_method(self):
        self.js = _read(ADMIN_JS)

    def test_handle_upload_accepted_defined_once(self):
        assert self.js.count("function handleUploadAccepted") == 1

    def test_called_from_both_file_upload_and_url_upload(self):
        assert "handleUploadAccepted(" in _extract_function(self.js, "uploadFile")
        assert "handleUploadAccepted(" in _extract_function(self.js, "submitUrlUpload")

    def test_url_upload_does_not_reimplement_polling(self):
        """URL 経路が独自のポーリング/一覧更新を再実装していないこと。"""
        body = _extract_function(self.js, "submitUrlUpload")
        assert "setInterval" not in body
        assert "pollUploadStatus" not in body

    def test_url_upload_reuses_upload_options(self):
        """analyze_images / models は既存アップロードと同じ入力から引き継ぐ。"""
        body = _extract_function(self.js, "submitUrlUpload")
        assert "upload-analyze-images" in body
        assert "getUploadModels" in body


# ---------------------------------------------------------------------------
# ⑤ SYSTEM_ADMIN 向け許可ドメイン管理区画（AIモデルタブ末尾）
# ---------------------------------------------------------------------------


class TestUrlFetchDomainsSection:
    def setup_method(self):
        self.js = _read(ADMIN_JS)

    def test_section_builder_exists_and_targets_llm_models_tab(self):
        body = _extract_function(self.js, "ensureUrlFetchDomainsSection")
        assert 'getElementById("tab-llm-models")' in body
        assert "url-fetch-domains-section" in body
        assert 'data-ui-anchor", "llm-models.url-fetch-domains"' in body

    def test_section_build_is_idempotent(self):
        """既に append 済みなら作り直さない（AIモデルタブの再描画で重複しない）。"""
        body = _extract_function(self.js, "ensureUrlFetchDomainsSection")
        assert "section.parentNode === panel" in body

    def test_empty_list_states_the_consequence(self):
        """UF2: 空は異常ではなく初期状態。教員が使えない理由まで事実文で書く。"""
        body = _extract_function(self.js, "renderUrlFetchDomains")
        assert "登録するまで教員はURLからの取得を利用できません。" in body

    def test_remove_requires_confirmation_stating_the_effect(self):
        body = _extract_function(self.js, "urlFetchDomainRemove")
        assert "confirm(" in body
        assert "以後このドメインからのURL取得はできなくなります。" in body

    def test_domain_crud_uses_admin_endpoints(self):
        add_body = _extract_function(self.js, "urlFetchDomainAdd")
        remove_body = _extract_function(self.js, "urlFetchDomainRemove")
        assert '"/admin/url-fetch-domains"' in add_body
        assert '"/admin/url-fetch-domains/"' in remove_body
        assert '"DELETE"' in remove_body

    def test_server_detail_is_surfaced_not_replaced(self):
        """UF6: サーバの事実文をそのまま見せる（独自の推測文で上書きしない）。"""
        assert "function _urlFetchDetailText" in self.js
        for fn in ("submitUrlUpload", "urlFetchDomainAdd", "urlFetchDomainRemove"):
            assert "_urlFetchDetailText(err)" in _extract_function(self.js, fn), fn
