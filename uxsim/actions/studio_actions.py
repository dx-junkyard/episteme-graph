"""第 25 周 面 D（原稿スタジオ → 学生の受講）の行為。

対象: 原稿スタジオのコース選択・授業用ドラフト（教材 / 本文説明）の編集と保存・``===`` の区切り・
スライド分割のプレビュー（``POST /api/admin/lecture-studio/preview-split``）・AI 書き換え・右ペイン
（根拠リンク / つまづき / 図の提案）・音声生成ボタンの準備状態の読み（生成はしない）・教材図スタジオ
（提案 → 1 ターン → 採用）、学生側の教材の図。学生の ``learning.lecture.sequence`` /
``learning.lecture.audio_status`` は既存の行為を使う（実行と投影は ``uxsim/runner/studio_exec.py``）。

行為にしないもの（理由は ``uxsim/runs/round25/design_studio.md``）:
- 音声生成（``POST /courses/{id}/lecture-audio/generate``）— TTS は bridge で肩代わりできない。
- 読み上げ言語の切り替え — 画面では音声生成モーダルの中にしかなく、原稿再生成 → 音声生成のチェーンを起こす。
- 原稿の一括生成・パイプラインの再実行 — LLM を大量に呼ぶ（この周の予算に入らない）。

契約の正本: ``backend/api/routes/lecture_studio/{scripts,topics}.py`` / ``routes/teaching_figures.py`` /
``routes/lecture.py`` と UI ``frontend/public/js/admin-lecture-studio.js`` / ``admin-figure-studio.js`` /
``app.js``（レクチャーモード）。マニュアルは ``docs/manual/teacher/14-admin-lecture-studio.md``。
"""
from __future__ import annotations

from uxsim.actions import registry as _registry
from uxsim.actions.registry import Action, _a

_TE = "teacher/14-admin-lecture-studio.md#"
_ST = "student/02-student.md#"
_PREVIEW = ("POST", "/api/admin/lecture-studio/preview-split")
_STRUCTURE = ("GET", "/api/admin/courses/{course_id}/lecture-studio/course-structure")
_SCRIPTS = ("GET", "/api/admin/courses/{course_id}/lecture-scripts")
_SETTINGS = ("GET", "/api/admin/courses/{course_id}/lecture-studio/settings")
_TOPIC_PUT = ("PUT", "/api/admin/courses/{course_id}/lecture-studio/course-topics/{topic_id}")
_TURN = ("POST", "/api/admin/courses/{course_id}/figure-studio/turn")
_SUGGESTIONS = ("GET", "/api/admin/courses/{course_id}/topics/{topic_id}/figure-suggestions")

ACTIONS: list[Action] = [
    _a("admin.lecture_studio.open_course", "原稿スタジオでコースを選ぶ", "admin", "lecture-studio.course-select",
       _STRUCTURE, args={"course_id": "str?"}, extra_api=(_SCRIPTS, _SETTINGS), manual=_TE + "course-select",
       note="画面はコースを選ぶとコース構造・原稿一覧・設定（読み上げ言語）を読む。course_id を省くと"
            "コース管理で選んだ自分のコース"),
    _a("admin.lecture_studio.select_topic", "コースのトピックを選んで授業用ドラフトを開く", "admin",
       "lecture-studio.course-draft", _PREVIEW, args={"topic_hint": "str?", "prefer": "str?", "view": "str?"},
       precondition="原稿スタジオでコースを選んでいる", manual=_TE + "course-draft",
       note="topic_hint はトピックの題名の一部か一覧の番号（1 始まり）。省くと prefer に従う（long = 教材本文が"
            "いちばん長いトピック / first = 先頭のトピック。既定 first）。画面は開くとスライド枚数のインジケータ"
            "（受講画面と同じ自動ページ分割）を問い合わせる。view は edit（既定・編集タブ）| preview"),
    _a("admin.lecture_studio.insert_slide_marker", "「+ スライド区切りを挿入」を押す", "admin",
       "lecture-studio.insert-slide-marker-btn", _PREVIEW, args={"field": "str?", "after_paragraph": "int?"},
       precondition="授業用ドラフトを開いている", manual=_TE + "insert-slide-marker-btn",
       note="field は material（教材・既定）| spoken（本文説明）。after_paragraph は何番目の段落の後に入れるか"
            "（1 始まり・省くと、もう一方の欄に区切りがあればその位置に近い段落の境目、無ければ真ん中）。"
            "段落が 1 つしか無い欄では、もう一方の欄の区切りの位置に最も近い文の境目（。の後）に入れる。"
            "もう一方の欄には自動で入らない（画面と同じ）"),
    _a("admin.lecture_studio.edit_draft", "授業用ドラフトの欄を書き直す", "admin", "lecture-studio.course-draft",
       _PREVIEW, args={"field": "str", "append": "str?", "find": "str?", "replace": "str?"},
       precondition="授業用ドラフトを開いている", manual=_TE + "course-draft",
       note="field は material（教材）| spoken（本文説明）| key_concepts | cautions。append は末尾に段落を足す。"
            "find / replace は欄の中の文字列を置き換える（見つからなければ何も変えない）。保存はしない"),
    _a("admin.lecture_studio.save_topic", "「原稿を保存する」を押す（授業用ドラフト）", "admin", "lecture-studio.save-btn",
       _TOPIC_PUT, precondition="授業用ドラフトを開いている", manual=_TE + "save-btn",
       note="編集中の欄をそのまま保存する。保存するとこのトピックの生成済み音声は消える（サーバ側の挙動）"),
    _a("admin.lecture_studio.reattach_appendix", "［付録を付け直す］を押す（自動生成の付録をいまの根拠から作り直す）",
       "admin", "lecture-studio.reattach-appendix",
       ("POST", "/api/admin/courses/{course_id}/lecture-studio/course-topics/{topic_id}/appendix/reattach"),
       precondition="授業用ドラフトに「付録がいまの根拠と違う」事実文と［付録を付け直す］が出ている",
       manual=_TE + "reattach-appendix",
       note="確認の後、付け直した本文を編集欄に入れるだけ（保存はしない・LLM なし）。保存は［原稿を保存する］"),
    _a("admin.lecture_studio.rewrite_topic", "AI で書き換え（授業用ドラフト）に指示を出す", "admin",
       "lecture-studio.assistant-modal",
       ("POST", "/api/admin/courses/{course_id}/lecture-studio/course-topics/{topic_id}/draft/rewrite"),
       args={"prompt": "str", "model": "str?"}, extra_api=(_PREVIEW,), llm_cost="product",
       precondition="授業用ドラフトを開いている", manual=_TE + "assistant-modal",
       note="提案は欄に反映されるだけで保存はされない（画面: 提案を反映しました。保存すると確定します。）"),
    _a("admin.lecture_studio.chunk_slides", "チャンクの「スライド」タブで分割と負荷ラベルを見る", "admin",
       "lecture-studio.display-tab-slides", _PREVIEW, args={"chunk_hint": "str?"},
       precondition="原稿スタジオでコースを選んでいる", manual=_TE + "display-tabs",
       note="chunk_hint は原稿一覧の番号（1 始まり）か節名・本文の一部。省くと、トピックを開いている間はその"
            "トピックに紐づくチャンクの先頭（紐づくチャンクが無ければ描けない）、開いていなければ「===」を含む"
            "チャンク、無ければ表示テキストがいちばん長いチャンク。画面は文書 ID を添えてプレビューを問い合わせ、負荷の高い"
            "スライドにだけ「要素の相互作用」の段階ラベルを出す"),
    _a("admin.lecture_studio.right_pane_evidence", "右ペインを「根拠リンク」にする", "admin",
       "lecture-studio.stumble-tab-evidence", _STRUCTURE, precondition="授業用ドラフトを開いている",
       manual=_TE + "stumble-tabs", note="読み込み済みのトピックの根拠を並べるだけ（HTTP なし）"),
    _a("admin.lecture_studio.right_pane_stumble", "右ペインを「つまづき」にする", "admin",
       "lecture-studio.stumble-tab-stumble", ("GET", "/api/admin/documents/{document_id}/claims/stumble-summary"),
       precondition="授業用ドラフトを開いている", manual=_TE + "stumble-tabs",
       note="画面は選択中のチャンク（無ければ原稿一覧の先頭のチャンク）の文書で集計を読み、トピックの主張に絞る"),
    _a("admin.lecture_studio.audio_readiness", "コース設定メニューで「音声生成を実行する」の状態と理由を読む", "admin",
       "lecture-studio.audio-all-btn", _SCRIPTS, extra_api=(_STRUCTURE,),
       precondition="原稿スタジオでコースを選んでいる", manual=_TE + "audio-all-btn",
       note="ボタンが押せるか・押せないならメニュー内の理由と次の操作を読むだけ（音声は生成しない）。"
            "保存の後に読むときは画面と同じく原稿一覧とコース構造を読み直す"),
    _a("admin.figure_studio.suggestions", "右ペインを「図の提案」にして提案を見る", "admin",
       "lecture-studio.stumble-tab-figures", _SUGGESTIONS, precondition="授業用ドラフトを開いている",
       manual=_TE + "figure-suggestions"),
    _a("admin.figure_studio.suggestions_generate", "図の提案の「提案を生成」を押す", "admin",
       "lecture-studio.figure-suggestions-generate",
       ("POST", "/api/admin/courses/{course_id}/topics/{topic_id}/figure-suggestions/generate"),
       llm_cost="product", precondition="図の提案を開いている", manual=_TE + "figure-suggestions",
       note="まだ判断していない候補があれば作り直しの確認（差し替え済みになる）を経て生成する"),
    _a("admin.figure_studio.build_from_suggestion", "提案カードの「この図を作る」を押す", "admin",
       "lecture-studio.figure-suggestions", _TURN,
       args={"suggestion_hint": "str?"}, llm_cost="product",
       extra_api=(("POST", "/api/admin/courses/{course_id}/figure-suggestions/{suggestion_id}/accept"),),
       precondition="図の提案にカードがある", manual=_TE + "figure-suggestions",
       note="suggestion_hint は提案の番号（1 始まり）か理由・方針の一部。画面は教材図スタジオを開き、方針を"
            "入れた最初の依頼を自動で 1 回送り、提案を「作成中」にする"),
    _a("admin.figure_studio.turn", "教材図スタジオで AI に図を頼む（送信）", "admin", "lecture-studio.figure-studio-send",
       _TURN, args={"instruction": "str", "after_heading": "str?"}, llm_cost="product",
       precondition="教材図スタジオを開いている",
       manual=_TE + "figure-studio", note="1 回の送信で 1 回だけ AI に問い合わせる。会話と図はモーダルの中にだけ残る"),
    _a("admin.figure_studio.adopt", "教材図スタジオで「採用して挿入」を押す", "admin", "lecture-studio.figure-studio-adopt",
       ("POST", "/api/admin/courses/{course_id}/teaching-figures"), args={"after_heading": "str?"},
       extra_api=(_PREVIEW,),
       precondition="教材図スタジオに図のプレビューがある", manual=_TE + "figure-studio",
       note="図を保存してトピックに登録し、教材欄に ![[figure:図のID]] を入れる。入れる位置は after_heading"
            "（教材欄の見出しの一部か、見出しの番号 1 始まり）の直後 → 提案起点なら引用箇所 → 末尾の順"
            "（画面ではカーソルを置いた位置に入る）。"
            "受講者に届くのは「原稿を保存する」の後"),
    _a("admin.figure_studio.existing", "教材図スタジオの「既存の図から選ぶ」を開く", "admin",
       "lecture-studio.figure-studio-modal", ("GET", "/api/admin/courses/{course_id}/teaching-figures"),
       precondition="授業用ドラフトを開いている", manual=_TE + "figure-studio"),
    _a("learning.lecture.next", "レクチャーの ▶ で次のスライドへ送る", "learning", "material.lecture-toggle",
       ("GET", "/api/learning/lecture/courses/{course_id}/topics/{topic_id}/sequence"),
       args={"to": "str?"}, precondition="レクチャーを再生している", manual=_ST + "interactive-lecture",
       note="第 25 周 studio H16: 画面の送り（◀ ▶・音声の終わり・音声の無いスライドはタイマー送り）を 1 枚ずつ辿る。"
            "to は next（既定）| prev | last。秒数は出さない。HTTP は出さない（スライドは再生開始時に読み込み済み。api は読み込み元の宣言）"),
    _a("learning.lecture.figure_view", "教材の図を見る（図の画像が読み込まれる）", "learning",
       _ST + "material-element-anchor", ("GET", "/api/learning/courses/{course_id}/figures/{figure_id}/image"),
       args={"figure_hint": "str?"}, precondition="図を含む教材を読んでいる", manual=_ST + "material-element-anchor",
       note="画面は教材を描くと図の画像を自動で読み込む。figure_hint は図の番号（1 始まり）かキャプションの一部"),
]

# registry.py の末尾の 1 行（``# r25:studio``）が無い順序で読まれても行為が引けるように、ここでも合流する（冪等）。
_registry.REGISTRY.update({a.id: a for a in ACTIONS if a.id not in _registry.REGISTRY})
