"""原稿スタジオ 音声生成の準備確認フロー (Issue #491) の静的回帰テスト。

docs/features/lecture_audio_generation_readiness.md の受け入れ条件 (§5) / テスト (§6)
のうち、フロントエンド（admin.html / styles.css / admin-lecture-studio.js）側を静的に検証する。
API 側の 422 ガードは test_lecture_language.py::TestBatchGenerateAudioReadinessGate。
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _ls_function(name: str) -> str:
    """admin-lecture-studio.js から ``function <name>(`` の本体（次の関数定義まで）を粗く抽出する。"""
    js = read("frontend/public/js/admin-lecture-studio.js")
    marker = "function " + name + "("
    assert marker in js, f"{name} が admin-lecture-studio.js に存在しない"
    tail = js.split(marker, 1)[1]
    # 次の「2スペースインデントの関数定義」までを本体とみなす（このファイルの整形規約）。
    end = tail.find("\n  function ")
    return tail[: end if end != -1 else len(tail)]


# --- §3-1 / §5: モーダルは初期非表示、[hidden] を打ち消す ---------------------------------


def test_audio_lang_modal_hidden_rule_overrides_settings_modal_flex():
    css = read("frontend/public/css/styles.css")
    assert "#ls-audio-lang-modal[hidden]" in css
    block = css.split("#ls-audio-lang-modal[hidden]", 1)[1].split("}", 1)[0]
    assert "display: none !important;" in block


def test_audio_lang_modal_is_hidden_in_markup():
    html = read("frontend/public/admin.html")
    # モーダル要素は hidden 属性付きで宣言されている。
    assert 'id="ls-audio-lang-modal" class="ls-settings-modal" hidden' in html


# --- §3-1: モーダルは対象サマリ（コース名・スライド数）と既存音声数を表示する ------------------


def test_audio_lang_modal_shows_course_and_slide_summary():
    html = read("frontend/public/admin.html")
    assert 'id="ls-audio-lang-summary"' in html
    assert 'id="ls-audio-lang-status"' in html
    summary = _ls_function("lsRenderAudioLangSummary")
    assert "lsSelectedCourseTitle()" in summary
    assert "slide_count" in summary
    assert "対象スライド数" in summary


# --- §3-2: 未準備時に理由と次の操作を近傍へ示す --------------------------------------------


def test_readiness_note_elements_exist():
    html = read("frontend/public/admin.html")
    for anchor in (
        'id="ls-audio-readiness-note"',
        'id="ls-audio-readiness-reason"',
        'id="ls-audio-readiness-action"',
    ):
        assert anchor in html


def test_readiness_guidance_covers_all_states():
    guidance = _ls_function("lsAudioReadinessGuidance")
    # 各状態の「理由」文（§3-2 の表）。
    assert "音声化するコースを選択してください" in guidance  # no_course
    assert "コースの音声準備状態を確認しています" in guidance  # loading
    assert "スライドの準備が完了していません" in guidance  # content_incomplete
    assert "読み上げ原稿が未生成のスライドがあります" in guidance  # script_incomplete
    # 次の操作
    for action in ("コースを選択", "完了を待つ", "コース内容生成を行う", "原稿生成を行う"):
        assert action in guidance


# --- §2-3 / §5: 音声生成の有効化条件（全チャンクに spoken_text 等）--------------------------


def test_audio_readiness_state_requires_all_conditions():
    body = _ls_function("lsAudioReadinessState")
    assert 'if (!lsState.courseId) return "no_course";' in body
    assert 'if (lsState.generating) return "busy";' in body
    # 両方の非同期ロードが揃うまで「読み込み中」
    assert "!lsState.scriptsLoaded || !lsState.structureLoaded" in body
    assert '"loading"' in body
    assert "lsIsCourseContentComplete()" in body
    assert '"content_incomplete"' in body
    assert "lsAllChunksHaveSpokenText()" in body
    assert '"script_incomplete"' in body
    assert 'return "ready";' in body


def test_all_chunks_have_spoken_text_uses_status_not_polluted_field():
    body = _ls_function("lsAllChunksHaveSpokenText")
    # spoken_text フィールドは display_text へフォールバックするため status で判定する。
    assert '!== "ungenerated"' in body
    assert ".every(" in body


def test_audio_button_enabled_only_when_can_generate():
    js = read("frontend/public/js/admin-lecture-studio.js")
    assert 'lsSetMenuItemState("ls-audio-all-btn", lsCanGenerateAudio()' in js
    assert "lsRenderAudioReadinessNote();" in js
    cangen = _ls_function("lsCanGenerateAudio")
    assert 'lsAudioReadinessState() === "ready"' in cangen


# --- §3-1: モーダルを開けるのは有効な音声生成ボタン押下時だけ（fail-closed）------------------


def test_modal_opens_only_when_can_generate():
    open_body = _ls_function("lsOpenAudioLangModal")
    # 関数先頭の fail-closed ガード。
    assert "if (!lsCanGenerateAudio()) return;" in open_body
    # ボタン click ハンドラも同じガードを通してから開く。
    js = read("frontend/public/js/admin-lecture-studio.js")
    assert (
        "if (!lsCanGenerateAudio()) return;\n      lsCloseMenus();\n      lsOpenAudioLangModal();"
        in js
    )


# --- §2-2 / §5: コース切替時に前コースの状態をクリアし、モーダルを閉じる ----------------------


def test_course_switch_clears_state_and_closes_modal():
    js = read("frontend/public/js/admin-lecture-studio.js")
    # change ハンドラ（コース選択ブランチ）で前コース状態をクリアしてモーダルを閉じる。
    change = js.split('courseSelect.addEventListener("change"', 1)[1].split(
        "if (resetCourseBtn)", 1
    )[0]
    assert "lsState.chunks = [];" in change
    assert "lsState.courseStructure = null;" in change
    assert "lsState.scriptsLoaded = false;" in change
    assert "lsState.structureLoaded = false;" in change
    assert "lsCloseAudioLangModal();" in change


def test_load_scripts_discards_stale_response_and_marks_loaded():
    body = _ls_function("lsLoadScripts")
    assert "if (lsState.courseId !== courseId) return;" in body
    assert "lsState.scriptsLoaded = true;" in body


def test_load_structure_discards_stale_response_and_marks_loaded():
    body = _ls_function("lsLoadCourseStructure")
    assert "if (lsState.courseId !== courseId) return;" in body
    assert "lsState.structureLoaded = true;" in body


# --- §3-3: 同一言語は未生成のみ、別言語は原稿再生成の警告 -----------------------------------


def test_language_switch_shows_regeneration_warning():
    warn = _ls_function("lsUpdateAudioLangWarning")
    assert "原稿を" in warn and "再生成" in warn
    assert "既存の" in warn
