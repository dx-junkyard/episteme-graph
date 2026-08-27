"""管理画面「？使い方」（インスペクト・モード）の UI 論理アンカー表（正本）。

共通規約: help-conventions.md（本タスクの実装エージェント間の合意文書）。学習画面側の
同型実装 ``core/help_kb/ui_anchors.py``（設計正本
``docs/features/learning_ui_inspect_hover_design.md``）を admin 向けに踏襲する。

管理画面ヘッダーの「❓ 使い方」（インスペクト・モード）を ON にした状態で UI 部品
（教材の中身ではなくシステムの部品）にホバーしたときに出す、マニュアル節ツールチップの
参照表。値は必ず ``docs/manual/teacher/`` または ``docs/manual/system_admin/`` に実在する
節だけを指す（``docs/manual/student/`` の参照は構造的に禁止 — student 索引は本モジュールから
一切参照しない）。

ロールの見える範囲は学習側と同じ上位継承パターン: TEACHER は teacher/ のみ、
SYSTEM_ADMIN は teacher/ + system_admin/ の両方を解決する（fail-closed。SYSTEM_ADMIN
限定タブの節は teacher ロールには一切配信しない）。

対応する節がまだ無い（＝マニュアルがまだこの UI 要素を説明していない）論理アンカーは
``ADMIN_UI_ANCHORS`` に **入れない**（``KNOWN_ADMIN_UI_ANCHOR_IDS`` にのみ登録し、
no_hit 経路で需要を計測する）。現状は全アンカーがマップ済み
（正確な件数は ``backend/tests/test_admin_help_ui_anchors.py`` が正）
（版の管理モーダルの発行/削除予約ボタンは versioning.* が正 — course-management 側の
重複IDは DOM 担体を持てないため収載しない。節自体は 13-admin-course-management.md に残る）。

FastAPI を import しない（core 層の規約）。
"""

from __future__ import annotations

from typing import Optional

from . import manual as _manual

# ---------------------------------------------------------------------------
# 候補アンカーID全集合（マップ済み + 未整備）。
#
# ``ADMIN_UI_ANCHORS`` のキーだけでは「マップ済みのみ」になってしまい、まだ節が無い
# アンカーへの no_hit 記録を「未知のアンカーID」として弾いてしまう。
# ``POST /api/admin/assistant/help/ui-anchor-events`` はこの全集合に対して検証し、
# 既知の UI 部品からの記録であることだけを確認する（内容の有無は ``ADMIN_UI_ANCHORS``/
# ``resolve_admin_ui_anchors`` が別途判定する）。
# ---------------------------------------------------------------------------
KNOWN_ADMIN_UI_ANCHOR_IDS: frozenset[str] = frozenset(
    {
        "atlas.add-domain-toggle",
        "atlas.assist-toggle",
        "atlas.binding-course-select",
        "atlas.binding-new-domain",
        "atlas.binding-propose",
        "atlas.binding-save",
        "atlas.cartridge-select",
        "atlas.discard-draft",
        "atlas.domain-restore",
        "atlas.domain-retire",
        "atlas.freeze",
        "atlas.gap-accept",
        "atlas.gap-candidates",
        "atlas.gap-dismiss",
        "atlas.gap-dismissed-filter",
        "atlas.gap-draft-from-frozen",
        "atlas.gap-incorporate",
        "atlas.gap-restore",
        "atlas.generate",
        "atlas.refresh",
        "atlas.report-incorporate",
        "atlas.report-resolve",
        "atlas.reports-filter",
        "atlas.save-draft",
        "course-builder.approve-btn",
        "course-builder.atlas-binding-area",
        "course-builder.import-course-btn",
        "course-builder.material-card",
        "course-builder.material-filter",
        "course-builder.material-search",
        "course-builder.material-sort",
        "course-builder.model-chip",
        "course-builder.new-session-btn",
        "course-builder.send-btn",
        "course-builder.session-select",
        "course-management.atlas-binding-open",
        "course-management.course-focus-save",
        "course-management.course-model-save",
        "course-management.focus-btn",
        "course-management.llm-model-btn",
        "course-management.manage-btn",
        "course-management.perm-add",
        "course-management.quick-publish-btn",
        "course-management.refresh",
        "course-management.release-review-btn",
        "course-management.sharing-dashboard-btn",
        "course-management.version-btn",
        "course-management.visibility-apply",
        "deliberation.annotation-decide",
        "deliberation.chat-send",
        "deliberation.context-lens",
        "deliberation.decomposition",
        "deliberation.discussion-seed-group",
        "deliberation.explanation-body-edit",
        "deliberation.figure-expand",
        "deliberation.figure-reanalyze",
        "deliberation.identity-link-create",
        "deliberation.identity-link-decide",
        "deliberation.inventory-bulk-review",
        "deliberation.inventory-filter",
        "deliberation.mode-save",
        "deliberation.positioning-lenses",
        "deliberation.review-sort-toggle",
        "deliberation.standardization-assess",
        "discuss-observation.dump-targz",
        "discuss-observation.dump-zip",
        "discuss-observation.refresh",
        "doubt-atlas.assumption-confirm",
        "doubt-atlas.assumption-dismiss",
        "doubt-atlas.assumption-filter",
        "doubt-atlas.audit-run",
        "doubt-atlas.counterfactual-observation-toggle",
        "doubt-atlas.course-select",
        "doubt-atlas.falsification-candidate-decide",
        "doubt-atlas.falsification-record",
        "doubt-atlas.falsification-refresh",
        "doubt-atlas.load-recompute",
        "doubt-atlas.manage-challenge",
        "doubt-atlas.mining-run",
        "doubt-atlas.open-assumptions-reachable-filter",
        "doubt-atlas.open-assumptions-row",
        "doubt-atlas.plot-detail",
        "doubt-atlas.record-challenge",
        "doubt-atlas.record-scope",
        "doubt-atlas.record-verification-status",
        "doubt-atlas.refresh",
        "error-analysis.clear-selection",
        "error-analysis.copy-format",
        "error-analysis.copy-selected",
        "error-analysis.copy-visible",
        "error-analysis.include-info",
        "error-analysis.keyword-search",
        "error-analysis.refresh",
        "error-analysis.select-all",
        "error-analysis.select-row",
        "error-analysis.time-range",
        "groups.create-form",
        "groups.delete-btn",
        "groups.invite-btn",
        "groups.join-form",
        "groups.leave-btn",
        "groups.list",
        "groups.my-invitations",
        "groups.remove-btn",
        "groups.rotate-btn",
        "header.copilot",
        "header.danger-confirm",
        "header.delete-confirm",
        "header.inspect",
        "header.logout",
        "header.next-steps",
        "header.notifications",
        "header.tab-bar",
        "interest-dashboard.bridge-insights-section",
        "interest-dashboard.course-select",
        "interest-dashboard.dashboard-body",
        "interest-dashboard.refresh-btn",
        "knowledge-library.detail-deliberate",
        "knowledge-library.detail-freeze",
        "knowledge-library.detail-restore",
        "knowledge-library.detail-retire",
        "knowledge-library.detail-save",
        "knowledge-library.domains-list",
        "knowledge-library.entries-list",
        "knowledge-library.filter-type",
        "knowledge-library.include-retired",
        "knowledge-library.new-entry-btn",
        "knowledge-library.search",
        "lecture-studio.ai-assistant-btn",
        "lecture-studio.assistant-modal",
        "lecture-studio.audio-all-btn",
        "lecture-studio.audio-lang-modal",
        "lecture-studio.component-approve",
        "lecture-studio.component-deliberate",
        "lecture-studio.component-endorse",
        "lecture-studio.component-insert",
        "lecture-studio.component-open",
        "lecture-studio.component-reject",
        "lecture-studio.course-content-btn",
        "lecture-studio.course-draft",
        "lecture-studio.course-reset",
        "lecture-studio.course-select",
        "lecture-studio.display-tab-formulas",
        "lecture-studio.display-tab-preview",
        "lecture-studio.display-tab-script",
        "lecture-studio.display-tab-slides",
        "lecture-studio.evidence-context",
        "lecture-studio.evidence-group",
        "lecture-studio.evidence-tab-extract",
        "lecture-studio.evidence-tab-pdf",
        "lecture-studio.extract-claims-btn",
        "lecture-studio.extract-theory-btn",
        "lecture-studio.figure-studio-adopt",
        "lecture-studio.figure-studio-caption",
        "lecture-studio.figure-studio-insert",
        "lecture-studio.figure-studio-modal",
        "lecture-studio.figure-studio-restore",
        "lecture-studio.figure-studio-retire",
        "lecture-studio.figure-studio-send",
        "lecture-studio.figure-suggestions",
        "lecture-studio.figure-suggestions-generate",
        "lecture-studio.insert-figure",
        "lecture-studio.insert-slide-marker-btn",
        "lecture-studio.more-menu",
        "lecture-studio.nav-components",
        "lecture-studio.nav-course",
        "lecture-studio.nav-document",
        "lecture-studio.recon-review-btn",
        "lecture-studio.recon-review-sort",
        "lecture-studio.refresh-graph-btn",
        "lecture-studio.right-pane-toggle",
        "lecture-studio.save-btn",
        "lecture-studio.settings-btn",
        "lecture-studio.slide-wm-label",
        "lecture-studio.stumble-tab-evidence",
        "lecture-studio.stumble-tab-figures",
        "lecture-studio.stumble-tab-stumble",
        "lecture-studio.theory-tab-claims",
        "lecture-studio.theory-tab-graph",
        "lecture-studio.theory-tab-theory",
        "lecture-studio.work-tab-edit",
        "lecture-studio.work-tab-graph",
        "lecture-studio.work-tab-structure",
        "llm-models.add-stage-override",
        "llm-models.change-scene-model",
        "llm-models.reset-scene-model",
        "llm-models.stage-overrides",
        "llm-models.url-fetch-domain-add",
        "llm-models.url-fetch-domain-remove",
        "llm-models.url-fetch-domains",
        "llm-models.usage-link",
        "llm-usage.groupby",
        "llm-usage.refresh",
        "manual-editor.freeze",
        "manual-editor.refresh",
        "manual-editor.reload",
        "manual-editor.save",
        "manual-editor.seed",
        "manual-editor.select-file",
        "manual-editor.switch-to-db",
        "manual-editor.switch-to-files",
        "materials.analyze-images",
        "materials.arxiv-discovery",
        "materials.arxiv-discovery-ingest",
        "materials.arxiv-discovery-modal",
        "materials.arxiv-discovery-search",
        "materials.arxiv-discovery-subscribe",
        "materials.cost-forecast-note",
        "materials.export-modal",
        "materials.figure-deliberate",
        "materials.figure-overlay",
        "materials.figure-promote",
        "materials.figures-modal",
        "materials.landscape-modal",
        "materials.landscape-propose",
        "materials.library-entry-merge-target",
        "materials.library-entry-modal",
        "materials.llm-model-change",
        "materials.reanalyze-modal",
        "materials.refresh",
        "materials.revision-decision",
        "materials.revision-modal",
        "materials.revision-start",
        "materials.row-delete",
        "materials.row-estimate",
        "materials.row-figures",
        "materials.row-inventory",
        "materials.row-landscape",
        "materials.row-more-menu",
        "materials.row-pdf-reupload",
        "materials.row-pipeline-run",
        "materials.row-resume-analysis",
        "materials.row-retry-stage",
        "materials.row-seminar-brief",
        "materials.row-share",
        "materials.row-version",
        "materials.seminar-brief-modal",
        "materials.upload-zone",
        "materials.url-upload",
        "materials.url-upload-modal",
        "materials.url-upload-submit",
        "release-review.modal",
        "release-review.next",
        "release-review.publish",
        "schema-proposals.approve-canary-btn",
        "schema-proposals.approve-full-btn",
        "schema-proposals.canary-confirm-btn",
        "schema-proposals.canary-course-select",
        "schema-proposals.proposals-list",
        "schema-proposals.simulate-btn",
        "schema-proposals.simulation-result",
        "schema.analyze-btn",
        "schema.approve-btn",
        "schema.jobs-list",
        "schema.preds-list",
        "schema.proposals-list",
        "schema.refresh-btn",
        "schema.reject-btn",
        "schema.types-list",
        "students.student-form",
        "students.user-activity",
        "students.user-delete",
        "students.user-list",
        "students.user-reset",
        "students.user-suspend",
        "stumbles.course-select",
        "stumbles.refresh-btn",
        "stumbles.table",
        "system-stats.generate-audio",
        "system-stats.generate-script",
        "system-stats.refresh",
        "system-stats.run-analysis",
        "system-stats.sort-columns",
        "system-stats.teacher-filter",
        "teachers.teacher-form",
        "teachers.user-activity",
        "teachers.user-delete",
        "teachers.user-list",
        "teachers.user-reset",
        "teachers.user-suspend",
        "teachers.user-transfer",
        "versioning.cancel-deletion",
        "versioning.inbox",
        "versioning.inbox-adopt",
        "versioning.inbox-read",
        "versioning.publish",
        "versioning.schedule-deletion",
        "versioning.version-history",
    }
)

# ---------------------------------------------------------------------------
# 論理アンカーID -> "teacher/<file>.md#<anchor>" または "system_admin/<file>.md#<anchor>"。
#
# student/ を指す値は構造的に禁止（起動時 check_admin_ui_anchor_mappings が検出する）。
# タブ（data-tab 値）ごとにコメント区切りで並べる。manifest の正本は各 docs 実装
# エージェントが manifests/<tab>.json に書いたものを統合した
# scratchpad/merged-manifest.json（2026-07-29）。
# ---------------------------------------------------------------------------
ADMIN_UI_ANCHORS: dict[str, str] = {
    # --- atlas.* — 分野の地図タブ --------------------------------------------------------
    # 新しい分野マップを作る
    "atlas.add-domain-toggle": "teacher/17-admin-atlas.md#add-domain",
    # AIと部分修正
    "atlas.assist-toggle": "teacher/17-admin-atlas.md#assist",
    # コース配置のコース選択
    "atlas.binding-course-select": "teacher/17-admin-atlas.md#binding-propose",
    # このコースから新しい分野マップを作る
    "atlas.binding-new-domain": "teacher/17-admin-atlas.md#binding-new-domain",
    # トピックの対応案を作る
    "atlas.binding-propose": "teacher/17-admin-atlas.md#binding-propose",
    # この対応で反映
    "atlas.binding-save": "teacher/17-admin-atlas.md#binding-save",
    # 分野を選択...
    "atlas.cartridge-select": "teacher/17-admin-atlas.md#cartridge-select",
    # 下書きを破棄
    "atlas.discard-draft": "teacher/17-admin-atlas.md#discard-draft",
    # 復帰する
    "atlas.domain-restore": "teacher/17-admin-atlas.md#domain-restore",
    # この分野を廃止する
    "atlas.domain-retire": "teacher/17-admin-atlas.md#domain-retire",
    # 公開前チェック
    "atlas.freeze": "teacher/17-admin-atlas.md#freeze",
    # 論文の解析から見つかった候補 — 採用
    "atlas.gap-accept": "teacher/17-admin-atlas.md#gap-accept",
    # 論文の解析から見つかった候補（グループ全体）
    "atlas.gap-candidates": "teacher/17-admin-atlas.md#gap-candidates",
    # 却下…（理由必須）
    "atlas.gap-dismiss": "teacher/17-admin-atlas.md#gap-dismiss",
    # 見送り済みも表示
    "atlas.gap-dismissed-filter": "teacher/17-admin-atlas.md#gap-dismissed-filter",
    # 現在の版から次版の下書きを作る
    "atlas.gap-draft-from-frozen": "teacher/17-admin-atlas.md#gap-draft-from-frozen",
    # 次版の下書きに取り込む…
    "atlas.gap-incorporate": "teacher/17-admin-atlas.md#gap-incorporate",
    # 見送りから戻す
    "atlas.gap-restore": "teacher/17-admin-atlas.md#gap-restore",
    # 次版の編集を始める
    "atlas.generate": "teacher/17-admin-atlas.md#generate",
    # 更新
    "atlas.refresh": "teacher/17-admin-atlas.md#refresh",
    # 次版で対応済みにする
    "atlas.report-incorporate": "teacher/17-admin-atlas.md#report-incorporate",
    # 採用（次版へ）／見送り（理由つき）／重複統合
    "atlas.report-resolve": "teacher/17-admin-atlas.md#report-resolve",
    # 修正報告の表示フィルタ
    "atlas.reports-filter": "teacher/17-admin-atlas.md#reports-filter",
    # 次版を保存
    "atlas.save-draft": "teacher/17-admin-atlas.md#save-draft",

    # --- course-builder.* — コースビルダータブ ---------------------------------------------
    # 承認してコースを登録
    "course-builder.approve-btn": "teacher/12-admin-course-builder.md#approve-btn",
    # 次のステップ: 学習マップの割り当て（分野の地図への配置・提案）
    "course-builder.atlas-binding-area": "teacher/12-admin-course-builder.md#atlas-binding-after-register",
    # 既存コースを読込
    "course-builder.import-course-btn": "teacher/12-admin-course-builder.md#import-course-btn",
    # 教材カード
    "course-builder.material-card": "teacher/12-admin-course-builder.md#material-card",
    # すべて / コース未作成 / 直近の生成 / 解析完了
    "course-builder.material-filter": "teacher/12-admin-course-builder.md#material-filter",
    # ファイル名・タイトルで検索…
    "course-builder.material-search": "teacher/12-admin-course-builder.md#material-search",
    # 並び替え（新しい順 / 直近の生成順 / タイトル順 / ボリューム順）
    "course-builder.material-sort": "teacher/12-admin-course-builder.md#material-sort",
    # AIモデル（コース構築チャット）
    "course-builder.model-chip": "teacher/12-admin-course-builder.md#model-chip",
    # + 新規
    "course-builder.new-session-btn": "teacher/12-admin-course-builder.md#new-session-btn",
    # 送信
    "course-builder.send-btn": "teacher/12-admin-course-builder.md#send-btn",
    # 過去のセッションを選択
    "course-builder.session-select": "teacher/12-admin-course-builder.md#session-select",

    # --- course-management.* — コース管理タブ --------------------------------------------
    # 学習マップ編集
    "course-management.atlas-binding-open": "teacher/13-admin-course-management.md#atlas-binding-open",
    # 議論テーマモーダルの「保存」
    "course-management.course-focus-save": "teacher/13-admin-course-management.md#course-focus-save",
    # 保存
    "course-management.course-model-save": "teacher/13-admin-course-management.md#course-model-save",
    # 議論テーマ
    "course-management.focus-btn": "teacher/13-admin-course-management.md#focus-btn",
    # AIモデル
    "course-management.llm-model-btn": "teacher/13-admin-course-management.md#llm-model-btn",
    # 共有設定
    "course-management.manage-btn": "teacher/13-admin-course-management.md#manage-btn",
    # 追加
    "course-management.perm-add": "teacher/13-admin-course-management.md#perm-add",
    # 公開する / 公開を止める
    "course-management.quick-publish-btn": "teacher/13-admin-course-management.md#quick-publish-btn",
    # 更新
    "course-management.refresh": "teacher/13-admin-course-management.md#refresh",
    # 確認して公開（リリース前の確認ウィザードを開く）
    "course-management.release-review-btn": "teacher/13-admin-course-management.md#release-review-btn",
    # 共有ダッシュボード
    "course-management.sharing-dashboard-btn": "teacher/13-admin-course-management.md#sharing-dashboard-btn",
    # 版の管理
    "course-management.version-btn": "teacher/13-admin-course-management.md#version-btn",
    # 適用
    "course-management.visibility-apply": "teacher/13-admin-course-management.md#visibility-apply",

    # --- deliberation.* — 深く検討モーダル（要素検討ワークスペース, W層。図・図要素モーダル/原稿スタジオ/教材管理から開く横断UI） -
    # 候補注釈を確定・却下する
    "deliberation.annotation-decide": "teacher/24-admin-deliberation.md#annotation-decide",
    # 対話で質問する（送信）
    "deliberation.chat-send": "teacher/24-admin-deliberation.md#chat-send",
    # 上位・中心・下位レーンとパンくず中心移動
    "deliberation.context-lens": "teacher/24-admin-deliberation.md#context-lens",
    # 内訳・同定
    "deliberation.decomposition": "teacher/24-admin-deliberation.md#decomposition",
    # 説明レビューキュー：この論文の議論のきっかけ（グループ全体）
    "deliberation.discussion-seed-group": "teacher/24-admin-deliberation.md#discussion-seed-group",
    # 説明レビューキュー：本文を編集 / 本文を保存
    "deliberation.explanation-body-edit": "teacher/24-admin-deliberation.md#explanation-body-edit",
    # 拡大表示（図要素のみ）
    "deliberation.figure-expand": "teacher/24-admin-deliberation.md#figure-expand",
    # AIで図を再解析
    "deliberation.figure-reanalyze": "teacher/24-admin-deliberation.md#figure-reanalyze",
    # 共通部品と結びつける
    "deliberation.identity-link-create": "teacher/24-admin-deliberation.md#identity-link-create",
    # 同一性リンクを確定・却下する
    "deliberation.identity-link-decide": "teacher/24-admin-deliberation.md#identity-link-decide",
    # 説明レビューキュー：選択した項目を承認・却下
    "deliberation.inventory-bulk-review": "teacher/24-admin-deliberation.md#inventory-bulk-review",
    # 検出要素の一覧: 種別・キーワードで絞り込む
    "deliberation.inventory-filter": "teacher/24-admin-deliberation.md#inventory-filter",
    # 保存（提示モード、図要素のみ）
    "deliberation.mode-save": "teacher/24-admin-deliberation.md#mode-save",
    # 文脈的位置づけ（4つのレンズ）
    "deliberation.positioning-lenses": "teacher/24-admin-deliberation.md#positioning-lenses",
    # 説明レビューキュー：並び順（負荷の高い順）トグル
    "deliberation.review-sort-toggle": "teacher/24-admin-deliberation.md#review-sort-toggle",
    # 標準化度を評価
    "deliberation.standardization-assess": "teacher/24-admin-deliberation.md#standardization-assess",

    # --- discuss-observation.* — discuss 観測基盤タブ（SYSTEM_ADMIN 限定） ------------------
    # ダンプ (tar.gz)
    "discuss-observation.dump-targz": "system_admin/15-admin-discuss-observation.md#dump-targz",
    # ダンプ (zip)
    "discuss-observation.dump-zip": "system_admin/15-admin-discuss-observation.md#dump-zip",
    # 取得
    "discuss-observation.refresh": "system_admin/15-admin-discuss-observation.md#refresh",

    # --- doubt-atlas.* — 前提の地図タブ（D層） ----------------------------------------------
    # 確定／文面を訂正して確定
    "doubt-atlas.assumption-confirm": "teacher/18-admin-doubt-atlas.md#assumption-confirm",
    # 却下
    "doubt-atlas.assumption-dismiss": "teacher/18-admin-doubt-atlas.md#assumption-dismiss",
    # 前提候補の表示フィルタ
    "doubt-atlas.assumption-filter": "teacher/18-admin-doubt-atlas.md#assumption-filter",
    # コーパス監査実行
    "doubt-atlas.audit-run": "teacher/18-admin-doubt-atlas.md#audit-run",
    # 観測を仮に倒す
    "doubt-atlas.counterfactual-observation-toggle": "teacher/18-admin-doubt-atlas.md#counterfactual-observation-toggle",
    # コースを選択...
    "doubt-atlas.course-select": "teacher/18-admin-doubt-atlas.md#course-select",
    # 確認して記帳／見送る（覆る条件の候補）
    "doubt-atlas.falsification-candidate-decide": "teacher/18-admin-doubt-atlas.md#falsification-candidate-decide",
    # 覆る条件を記帳する
    "doubt-atlas.falsification-record": "teacher/18-admin-doubt-atlas.md#falsification-record",
    # AIに候補を出してもらう（覆る条件）
    "doubt-atlas.falsification-refresh": "teacher/18-admin-doubt-atlas.md#falsification-refresh",
    # 負荷再計算
    "doubt-atlas.load-recompute": "teacher/18-admin-doubt-atlas.md#load-recompute",
    # 取り下げ／検証提案にする
    "doubt-atlas.manage-challenge": "teacher/18-admin-doubt-atlas.md#manage-challenge",
    # 前提マイニング実行
    "doubt-atlas.mining-run": "teacher/18-admin-doubt-atlas.md#mining-run",
    # 到達可能な反証条件がある項目だけ表示
    "doubt-atlas.open-assumptions-reachable-filter": "teacher/18-admin-doubt-atlas.md#open-assumptions-reachable-filter",
    # 未検証合意リストの行（クリックで台帳詳細）
    "doubt-atlas.open-assumptions-row": "teacher/18-admin-doubt-atlas.md#open-assumptions",
    # 散布図の点（クリックで台帳詳細）
    "doubt-atlas.plot-detail": "teacher/18-admin-doubt-atlas.md#plot-detail",
    # 疑義を残す
    "doubt-atlas.record-challenge": "teacher/18-admin-doubt-atlas.md#record-challenge",
    # スコープを記帳
    "doubt-atlas.record-scope": "teacher/18-admin-doubt-atlas.md#record-scope",
    # 検証状態を記帳する
    "doubt-atlas.record-verification-status": "teacher/18-admin-doubt-atlas.md#record-verification-status",
    # 再読込
    "doubt-atlas.refresh": "teacher/18-admin-doubt-atlas.md#refresh",

    # --- error-analysis.* — エラーログタブ（SYSTEM_ADMIN 限定） ------------------------------
    # 選択解除
    "error-analysis.clear-selection": "system_admin/12-admin-error-analysis.md#clear-selection",
    # コピー形式（AI解析用Markdown/JSON/プレーンテキスト）
    "error-analysis.copy-format": "system_admin/12-admin-error-analysis.md#copy-format",
    # 選択したログをコピー
    "error-analysis.copy-selected": "system_admin/12-admin-error-analysis.md#copy-selected",
    # 表示中すべてをコピー
    "error-analysis.copy-visible": "system_admin/12-admin-error-analysis.md#copy-visible",
    # INFOを含める
    "error-analysis.include-info": "system_admin/12-admin-error-analysis.md#include-info",
    # キーワードで検索する
    "error-analysis.keyword-search": "system_admin/12-admin-error-analysis.md#keyword-search",
    # 更新
    "error-analysis.refresh": "system_admin/12-admin-error-analysis.md#refresh",
    # 表示中を選択
    "error-analysis.select-all": "system_admin/12-admin-error-analysis.md#select-all",
    # ログを選択する（各行のチェックボックス）
    "error-analysis.select-row": "system_admin/12-admin-error-analysis.md#select-row",
    # 期間を選ぶ
    "error-analysis.time-range": "system_admin/12-admin-error-analysis.md#time-range",

    # --- groups.* — グループタブ --------------------------------------------------------
    # グループを作成
    "groups.create-form": "teacher/15-admin-groups.md#create-form",
    # グループを削除
    "groups.delete-btn": "teacher/15-admin-groups.md#delete-btn",
    # ユーザーを直接招待
    "groups.invite-btn": "teacher/15-admin-groups.md#invite-btn",
    # 招待コードで参加
    "groups.join-form": "teacher/15-admin-groups.md#join-form",
    # 退会
    "groups.leave-btn": "teacher/15-admin-groups.md#leave-btn",
    # 所属グループ一覧
    "groups.list": "teacher/15-admin-groups.md#list",
    # 未承諾の招待
    "groups.my-invitations": "teacher/15-admin-groups.md#my-invitations",
    # 除名
    "groups.remove-btn": "teacher/15-admin-groups.md#remove-btn",
    # 招待コード再発行
    "groups.rotate-btn": "teacher/15-admin-groups.md#rotate-btn",

    # --- header.* — 共通ヘッダー（全タブ共通の画面上部要素） ------------------------------------------
    # 🤖 操作アシスタント
    "header.copilot": "teacher/10-admin-common.md#copilot",
    # 危険確認モーダル
    "header.danger-confirm": "teacher/10-admin-common.md#danger-confirm-modal",
    # 削除確認モーダル
    "header.delete-confirm": "teacher/10-admin-common.md#delete-confirm-modal",
    # ❓ 使い方
    "header.inspect": "teacher/10-admin-common.md#inspect-mode",
    # ログアウト
    "header.logout": "teacher/10-admin-common.md#logout",
    # 📋 次にやること
    "header.next-steps": "teacher/10-admin-common.md#next-steps",
    # 🔔 通知
    "header.notifications": "teacher/10-admin-common.md#notifications",
    # タブナビゲーション（グループ別プルダウン）
    "header.tab-bar": "teacher/10-admin-common.md#tab-groups",

    # --- interest-dashboard.* — 関心集約ダッシュボードタブ -------------------------------------
    # 橋の候補（学習者の重ね合わせ）
    "interest-dashboard.bridge-insights-section": "teacher/21-admin-interest-dashboard.md#bridge-insights-section",
    # コース選択
    "interest-dashboard.course-select": "teacher/21-admin-interest-dashboard.md#course-select",
    # 関心集約ダッシュボード
    "interest-dashboard.dashboard-body": "teacher/21-admin-interest-dashboard.md#dashboard-body",
    # 更新
    "interest-dashboard.refresh-btn": "teacher/21-admin-interest-dashboard.md#refresh-btn",

    # --- knowledge-library.* — 分野別ナレッジライブラリタブ（L層） ---------------------------------
    # 深く検討
    "knowledge-library.detail-deliberate": "teacher/19-admin-knowledge-library.md#detail-deliberate",
    # 凍結（版発行）
    "knowledge-library.detail-freeze": "teacher/19-admin-knowledge-library.md#detail-freeze",
    # 復元
    "knowledge-library.detail-restore": "teacher/19-admin-knowledge-library.md#detail-restore",
    # 廃止
    "knowledge-library.detail-retire": "teacher/19-admin-knowledge-library.md#detail-retire",
    # 保存
    "knowledge-library.detail-save": "teacher/19-admin-knowledge-library.md#detail-save",
    # 分野の一覧
    "knowledge-library.domains-list": "teacher/19-admin-knowledge-library.md#domains-list",
    # エントリ一覧
    "knowledge-library.entries-list": "teacher/19-admin-knowledge-library.md#entries-list",
    # 種別で絞り込む
    "knowledge-library.filter-type": "teacher/19-admin-knowledge-library.md#filter-type",
    # 廃止済みも表示
    "knowledge-library.include-retired": "teacher/19-admin-knowledge-library.md#include-retired",
    # 新規エントリ作成
    "knowledge-library.new-entry-btn": "teacher/19-admin-knowledge-library.md#new-entry-btn",
    # 名称・別名で検索
    "knowledge-library.search": "teacher/19-admin-knowledge-library.md#search",

    # --- lecture-studio.* — 原稿スタジオタブ ----------------------------------------------
    # AI アシスタント
    "lecture-studio.ai-assistant-btn": "teacher/14-admin-lecture-studio.md#ai-assistant-btn",
    # AI アシスタント（書き換え）モーダル
    "lecture-studio.assistant-modal": "teacher/14-admin-lecture-studio.md#assistant-modal",
    # 音声生成
    "lecture-studio.audio-all-btn": "teacher/14-admin-lecture-studio.md#audio-all-btn",
    # 音声を生成します（言語選択モーダル）
    "lecture-studio.audio-lang-modal": "teacher/14-admin-lecture-studio.md#audio-lang-modal",
    # 承認
    "lecture-studio.component-approve": "teacher/14-admin-lecture-studio.md#component-approve",
    # 深く検討
    "lecture-studio.component-deliberate": "teacher/14-admin-lecture-studio.md#component-deliberate",
    # 説明・承認の共有
    "lecture-studio.component-endorse": "teacher/14-admin-lecture-studio.md#component-endorse",
    # 原稿に挿入
    "lecture-studio.component-insert": "teacher/14-admin-lecture-studio.md#component-insert",
    # 開く
    "lecture-studio.component-open": "teacher/14-admin-lecture-studio.md#component-open",
    # 却下
    "lecture-studio.component-reject": "teacher/14-admin-lecture-studio.md#component-reject",
    # コース内容生成
    "lecture-studio.course-content-btn": "teacher/14-admin-lecture-studio.md#course-content-btn",
    # 授業用ドラフト編集欄
    "lecture-studio.course-draft": "teacher/14-admin-lecture-studio.md#course-draft",
    # [選び直す]
    "lecture-studio.course-reset": "teacher/14-admin-lecture-studio.md#course-reset",
    # コース選択
    "lecture-studio.course-select": "teacher/14-admin-lecture-studio.md#course-select",
    # 数式一覧
    "lecture-studio.display-tab-formulas": "teacher/14-admin-lecture-studio.md#display-tabs",
    # プレビュー
    "lecture-studio.display-tab-preview": "teacher/14-admin-lecture-studio.md#display-tabs",
    # 原稿
    "lecture-studio.display-tab-script": "teacher/14-admin-lecture-studio.md#display-tabs",
    # スライド
    "lecture-studio.display-tab-slides": "teacher/14-admin-lecture-studio.md#display-tabs",
    # 抽出
    # 根拠リンクカードのヘッダー（開いて文脈を確認）
    "lecture-studio.evidence-context": "teacher/14-admin-lecture-studio.md#evidence-context",
    # 根拠リンクの構造アウトライン（論理要素グループの折りたたみバー）
    "lecture-studio.evidence-group": "teacher/14-admin-lecture-studio.md#evidence-group",
    "lecture-studio.evidence-tab-extract": "teacher/14-admin-lecture-studio.md#evidence-tabs",
    # PDF
    "lecture-studio.evidence-tab-pdf": "teacher/14-admin-lecture-studio.md#evidence-tabs",
    # 主張を抽出
    "lecture-studio.extract-claims-btn": "teacher/14-admin-lecture-studio.md#extract-claims-btn",
    # 論理要素候補を抽出
    "lecture-studio.extract-theory-btn": "teacher/14-admin-lecture-studio.md#extract-theory-btn",
    # 採用して挿入（教材図スタジオ）
    "lecture-studio.figure-studio-adopt": "teacher/14-admin-lecture-studio.md#figure-studio",
    # キャプションを直す（既存の図から選ぶタブ / このコースで作った図）
    "lecture-studio.figure-studio-caption": "teacher/14-admin-lecture-studio.md#figure-studio-caption",
    # 教材に挿入 / 採用して挿入（既存の図から選ぶタブ）
    "lecture-studio.figure-studio-insert": "teacher/14-admin-lecture-studio.md#figure-studio-insert",
    # 教材図スタジオ（モーダル本体 / 既存の図から選ぶタブ）
    "lecture-studio.figure-studio-modal": "teacher/14-admin-lecture-studio.md#figure-studio",
    # 採用に戻す（回収済みの図）
    "lecture-studio.figure-studio-restore": "teacher/14-admin-lecture-studio.md#figure-studio-restore",
    # 回収する（採用済みの図）
    "lecture-studio.figure-studio-retire": "teacher/14-admin-lecture-studio.md#figure-studio-retire",
    # 送信（教材図スタジオの AI との相談）
    "lecture-studio.figure-studio-send": "teacher/14-admin-lecture-studio.md#figure-studio",
    # 図の提案ペイン
    "lecture-studio.figure-suggestions": "teacher/14-admin-lecture-studio.md#figure-suggestions",
    # 提案を生成
    "lecture-studio.figure-suggestions-generate": "teacher/14-admin-lecture-studio.md#figure-suggestions",
    # 🖼 図を挿入
    "lecture-studio.insert-figure": "teacher/14-admin-lecture-studio.md#insert-figure",
    # + スライド区切りを挿入
    "lecture-studio.insert-slide-marker-btn": "teacher/14-admin-lecture-studio.md#insert-slide-marker-btn",
    # コース設定 ▼
    "lecture-studio.more-menu": "teacher/14-admin-lecture-studio.md#more-menu",
    # コンポーネント（左ペインタブ）
    "lecture-studio.nav-components": "teacher/14-admin-lecture-studio.md#nav-tabs",
    # コース（左ペインタブ）
    "lecture-studio.nav-course": "teacher/14-admin-lecture-studio.md#nav-tabs",
    # 文書構造（左ペインタブ）
    "lecture-studio.nav-document": "teacher/14-admin-lecture-studio.md#nav-tabs",
    # 再構成の確認
    "lecture-studio.recon-review-btn": "teacher/14-admin-lecture-studio.md#recon-review-btn",
    # 再構成の確認：並び順（負荷の高い順）トグル
    "lecture-studio.recon-review-sort": "teacher/14-admin-lecture-studio.md#recon-review-sort",
    # グラフを更新
    "lecture-studio.refresh-graph-btn": "teacher/14-admin-lecture-studio.md#refresh-graph-btn",
    # 右ペインを隠す
    "lecture-studio.right-pane-toggle": "teacher/14-admin-lecture-studio.md#right-pane-toggle",
    # 原稿を保存
    "lecture-studio.save-btn": "teacher/14-admin-lecture-studio.md#save-btn",
    # 設定
    "lecture-studio.settings-btn": "teacher/14-admin-lecture-studio.md#settings-btn",
    # スライドの負荷ラベル（WMレンズ）
    "lecture-studio.slide-wm-label": "teacher/14-admin-lecture-studio.md#slide-wm-label",
    # 根拠リンク
    "lecture-studio.stumble-tab-evidence": "teacher/14-admin-lecture-studio.md#stumble-tabs",
    # 図の提案（右ペイン第3トグル）
    "lecture-studio.stumble-tab-figures": "teacher/14-admin-lecture-studio.md#stumble-tabs",
    # つまづき
    "lecture-studio.stumble-tab-stumble": "teacher/14-admin-lecture-studio.md#stumble-tabs",
    # 主張
    "lecture-studio.theory-tab-claims": "teacher/14-admin-lecture-studio.md#theory-graph-tabs",
    # グラフ
    "lecture-studio.theory-tab-graph": "teacher/14-admin-lecture-studio.md#theory-graph-tabs",
    # 論理要素
    "lecture-studio.theory-tab-theory": "teacher/14-admin-lecture-studio.md#theory-graph-tabs",
    # 原稿（編集ツールバー）
    "lecture-studio.work-tab-edit": "teacher/14-admin-lecture-studio.md#work-tabs",
    # 理論グラフ（編集ツールバー）
    "lecture-studio.work-tab-graph": "teacher/14-admin-lecture-studio.md#work-tabs",
    # 構造（編集ツールバー）
    "lecture-studio.work-tab-structure": "teacher/14-admin-lecture-studio.md#work-tabs",

    # --- llm-models.* — AIモデルタブ（SYSTEM_ADMIN 限定、場面別LLMモデル選択 M層） -------------------
    # ステージ別の既定を追加
    "llm-models.add-stage-override": "system_admin/16-admin-llm-models.md#add-stage-override",
    # 変更（場面ごとのモデル変更）
    "llm-models.change-scene-model": "system_admin/16-admin-llm-models.md#change-scene-model",
    # 既定に戻す
    "llm-models.reset-scene-model": "system_admin/16-admin-llm-models.md#reset-scene-model",
    # ステージ別の指定（N件）
    "llm-models.stage-overrides": "system_admin/16-admin-llm-models.md#stage-overrides",
    # URL取得の許可ドメイン: 追加（migration 070 / url_material_upload_design.md）
    "llm-models.url-fetch-domain-add": "system_admin/17-admin-url-fetch-domains.md#url-fetch-domain-add",
    # URL取得の許可ドメイン: 行の削除
    "llm-models.url-fetch-domain-remove": "system_admin/17-admin-url-fetch-domains.md#url-fetch-domain-remove",
    # URL取得の許可ドメイン: 区画本体（一覧）
    "llm-models.url-fetch-domains": "system_admin/17-admin-url-fetch-domains.md#url-fetch-domains",
    # 「LLM使用量」タブへのリンク
    "llm-models.usage-link": "system_admin/16-admin-llm-models.md#usage-link",

    # --- llm-usage.* — LLM使用量タブ（SYSTEM_ADMIN 限定） ----------------------------------
    # 集計軸（feature×model/feature/model/provider/operation/day）
    "llm-usage.groupby": "system_admin/13-admin-llm-usage.md#groupby",
    # 更新
    "llm-usage.refresh": "system_admin/13-admin-llm-usage.md#refresh",

    # --- manual-editor.* — マニュアル編集タブ（SYSTEM_ADMIN 限定、help_kb Phase 3 DB draft/freeze） -
    # 凍結して配信
    "manual-editor.freeze": "system_admin/14-admin-manual-editor.md#freeze",
    # 更新
    "manual-editor.refresh": "system_admin/14-admin-manual-editor.md#refresh",
    # 再読込
    "manual-editor.reload": "system_admin/14-admin-manual-editor.md#reload",
    # 保存
    "manual-editor.save": "system_admin/14-admin-manual-editor.md#save",
    # ファイルから取込
    "manual-editor.seed": "system_admin/14-admin-manual-editor.md#seed",
    # ファイルを選ぶ（draft一覧の各ボタン）
    "manual-editor.select-file": "system_admin/14-admin-manual-editor.md#select-file",
    # DB配信に切替
    "manual-editor.switch-to-db": "system_admin/14-admin-manual-editor.md#switch-to-db",
    # ファイル配信に戻す
    "manual-editor.switch-to-files": "system_admin/14-admin-manual-editor.md#switch-to-files",

    # --- materials.* — 教材管理タブ -----------------------------------------------------
    # 図面・画像を解析する
    "materials.analyze-images": "teacher/11-admin-materials.md#analyze-images",
    # arXivから探す（論文ディスカバリー, paper_discovery_design.md）— アップロードゾーン内のリンク
    "materials.arxiv-discovery": "teacher/11-admin-materials.md#arxiv-discovery",
    # 選択した論文を取り込む（許可ドメイン未設定・未選択のときは無効）
    "materials.arxiv-discovery-ingest": "teacher/11-admin-materials.md#arxiv-discovery-ingest",
    # arXivから探すモーダル（検索・購読パネル / 候補一覧 / 取り込みの3区画）
    "materials.arxiv-discovery-modal": "teacher/11-admin-materials.md#arxiv-discovery-modal",
    # この条件で検索（arXiv のメタデータのみ・LLM 不使用）
    "materials.arxiv-discovery-search": "teacher/11-admin-materials.md#arxiv-discovery-search",
    # この条件を保存（分野単位の購読条件。外したキーフレーズも保持される）
    "materials.arxiv-discovery-subscribe": "teacher/11-admin-materials.md#arxiv-discovery-subscribe",
    # AI利用枠の見通し（コスト見通しの一行。アップロードゾーン + 再解析モーダル）
    "materials.cost-forecast-note": "teacher/11-admin-materials.md#cost-forecast-note",
    # 外部レビュー用に書き出しモーダル
    "materials.export-modal": "teacher/11-admin-materials.md#export-modal",
    # 深く検討（図）
    "materials.figure-deliberate": "teacher/11-admin-materials.md#figure-deliberate",
    # 図で確認
    "materials.figure-overlay": "teacher/11-admin-materials.md#figure-overlay",
    # ライブラリへ昇格（図の装置候補から）
    "materials.figure-promote": "teacher/11-admin-materials.md#figure-promote",
    # 図・画像モーダル
    "materials.figures-modal": "teacher/11-admin-materials.md#figures-modal",
    # 位置づけ（分野マップ）モーダル
    "materials.landscape-modal": "teacher/11-admin-materials.md#landscape-modal",
    # AIで再提案（配置候補の再生成）
    "materials.landscape-propose": "teacher/11-admin-materials.md#landscape-propose",
    # 統合先ラジオ（新規作成 / 既存エントリへ統合）+ 例示画像を含めるチェックボックス
    "materials.library-entry-merge-target": "teacher/11-admin-materials.md#library-entry-merge-target",
    # ライブラリへ昇格モーダル
    "materials.library-entry-modal": "teacher/11-admin-materials.md#library-entry-modal",
    # 解析モデル 変更
    "materials.llm-model-change": "teacher/11-admin-materials.md#llm-model-change",
    # 解析を再開（オプション）モーダル
    "materials.reanalyze-modal": "teacher/11-admin-materials.md#reanalyze-modal",
    # 更新（教材一覧の再取得）
    "materials.refresh": "teacher/11-admin-materials.md#refresh-materials",
    # 監査＋候補生成 / 採用 / 却下 / 再修正
    "materials.revision-decision": "teacher/11-admin-materials.md#revision-decision",
    # 反復改善モーダル
    "materials.revision-modal": "teacher/11-admin-materials.md#revision-modal",
    # 改善処理を開始
    "materials.revision-start": "teacher/11-admin-materials.md#revision-start",
    # 削除
    "materials.row-delete": "teacher/11-admin-materials.md#row-delete",
    # 解析コスト見積り
    "materials.row-estimate": "teacher/11-admin-materials.md#row-estimate",
    # 図・画像
    "materials.row-figures": "teacher/11-admin-materials.md#row-figures",
    # 検出要素
    "materials.row-inventory": "teacher/11-admin-materials.md#row-inventory",
    # 位置づけ（分野マップ）…
    "materials.row-landscape": "teacher/11-admin-materials.md#landscape-open",
    # 操作メニュー（⋯）— 行操作の2層化（admin_ux_issues_2026-08-01.md §2.3）
    "materials.row-more-menu": "teacher/11-admin-materials.md#row-more-menu",
    # PDF再登録
    "materials.row-pdf-reupload": "teacher/11-admin-materials.md#pdf-reupload",
    # パイプラインを実行 ▼
    "materials.row-pipeline-run": "teacher/11-admin-materials.md#pipeline-run",
    # 解析再開
    "materials.row-resume-analysis": "teacher/11-admin-materials.md#resume-analysis",
    # ステージ再実行（縮退時のみ表示されるリンク）
    "materials.row-retry-stage": "teacher/11-admin-materials.md#row-retry-stage",
    # ゼミ前ブリーフ…（seminar_brief_mirroring_design.md §1: 輪講前の read-only 合成ビュー）
    "materials.row-seminar-brief": "teacher/11-admin-materials.md#seminar-brief-open",
    # 共有設定
    "materials.row-share": "teacher/11-admin-materials.md#row-share",
    # 版の管理
    "materials.row-version": "teacher/11-admin-materials.md#row-version",
    # ゼミ前ブリーフモーダル（4区画: 脆い前提 / 一点吊りの支持線 / 晴れ間 / 学習者からの問い）
    "materials.seminar-brief-modal": "teacher/11-admin-materials.md#seminar-brief-modal",
    # アップロードゾーン（ドラッグ&ドロップ / ファイルを選択）
    "materials.upload-zone": "teacher/11-admin-materials.md#upload-zone",
    # URLから取得（arXiv など）— アップロードゾーン内のリンク（migration 070）
    "materials.url-upload": "teacher/11-admin-materials.md#url-upload",
    # URLから教材を取得モーダル
    "materials.url-upload-modal": "teacher/11-admin-materials.md#url-upload-modal",
    # モーダルの「アップロード」（許可リストが空のときは無効）
    "materials.url-upload-submit": "teacher/11-admin-materials.md#url-upload-submit",

    # --- release-review.* — リリース前の確認ウィザード（release_review_flow_design.md。コース管理/コースビルダーから開く横断UI） -
    # ウィザード本体（3ステップ: 学習マップ → 論文の位置づけ → 公開）
    "release-review.modal": "teacher/13-admin-course-management.md#release-review-modal",
    # 各ステップの主ボタン（＝確認したものとして記録する）
    "release-review.next": "teacher/13-admin-course-management.md#release-review-next",
    # 公開する
    "release-review.publish": "teacher/13-admin-course-management.md#release-review-publish",

    # --- schema-proposals.* — スキーマ提案タブ --------------------------------------------
    # カナリアリリース（特定コースのみ）
    "schema-proposals.approve-canary-btn": "teacher/22-admin-schema-proposals.md#approve-canary-btn",
    # システム全体に適用
    "schema-proposals.approve-full-btn": "teacher/22-admin-schema-proposals.md#approve-full-btn",
    # 選択したコースに適用
    "schema-proposals.canary-confirm-btn": "teacher/22-admin-schema-proposals.md#canary-confirm-btn",
    # コース選択（複数選択）
    "schema-proposals.canary-course-select": "teacher/22-admin-schema-proposals.md#canary-confirm-btn",
    # 提案一覧
    "schema-proposals.proposals-list": "teacher/22-admin-schema-proposals.md#proposals-list",
    # シミュレーションを実行
    "schema-proposals.simulate-btn": "teacher/22-admin-schema-proposals.md#simulate-btn",
    # シミュレーション結果
    "schema-proposals.simulation-result": "teacher/22-admin-schema-proposals.md#simulation-result",

    # --- schema.* — スキーマ進化タブ ------------------------------------------------------
    # AIメタ分析を実行
    "schema.analyze-btn": "teacher/23-admin-schema.md#analyze-btn",
    # 承認して適用
    "schema.approve-btn": "teacher/23-admin-schema.md#approve-btn",
    # 再抽出ジョブ
    "schema.jobs-list": "teacher/23-admin-schema.md#jobs-list",
    # 関係性タイプ（CorePredicate）
    "schema.preds-list": "teacher/23-admin-schema.md#preds-list",
    # 提案一覧
    "schema.proposals-list": "teacher/23-admin-schema.md#proposals-list",
    # 提案を更新
    "schema.refresh-btn": "teacher/23-admin-schema.md#refresh-btn",
    # 却下
    "schema.reject-btn": "teacher/23-admin-schema.md#reject-btn",
    # 概念カテゴリ（OntologyType）
    "schema.types-list": "teacher/23-admin-schema.md#types-list",

    # --- students.* — 学生管理タブ ------------------------------------------------------
    # 学生を作成
    "students.student-form": "teacher/16-admin-students.md#student-form",
    # 学生アカウント一覧
    "students.user-list": "teacher/16-admin-students.md#user-list",
    # 停止… / 再開（教員以上）
    "students.user-suspend": "teacher/16-admin-students.md#user-suspend",
    # 以下3件は SYSTEM_ADMIN 限定のボタン（AL7）。TEACHER には
    # resolve_admin_ui_anchors の fail-closed により配信されない値（system_admin/）を指す。
    # パスワード再設定…（学生）
    "students.user-reset": "system_admin/10-admin-teachers.md#student-user-reset",
    # 利用状況…（学生）
    "students.user-activity": "system_admin/10-admin-teachers.md#student-user-activity",
    # 削除予約… / 削除予約を取消（学生）
    "students.user-delete": "system_admin/10-admin-teachers.md#student-user-delete",

    # --- stumbles.* — つまづきデータタブ ---------------------------------------------------
    # コース選択
    "stumbles.course-select": "teacher/20-admin-stumbles.md#course-select",
    # 更新
    "stumbles.refresh-btn": "teacher/20-admin-stumbles.md#refresh-btn",
    # つまづきデータ一覧
    "stumbles.table": "teacher/20-admin-stumbles.md#table",

    # --- system-stats.* — システム統計タブ（SYSTEM_ADMIN 限定） -------------------------------
    # 音声生成 / 音声再実行
    "system-stats.generate-audio": "system_admin/11-admin-system-stats.md#generate-audio",
    # 原稿生成 / 原稿再実行
    "system-stats.generate-script": "system_admin/11-admin-system-stats.md#generate-script",
    # 更新
    "system-stats.refresh": "system_admin/11-admin-system-stats.md#refresh",
    # 解析を実行・再実行する（DSL/Claim/Component/Graph/全解析）
    "system-stats.run-analysis": "system_admin/11-admin-system-stats.md#run-analysis",
    # 列見出しでの並べ替え
    "system-stats.sort-columns": "system_admin/11-admin-system-stats.md#sort-columns",
    # 教員で絞り込む
    "system-stats.teacher-filter": "system_admin/11-admin-system-stats.md#teacher-filter",

    # --- teachers.* — 教員管理タブ（SYSTEM_ADMIN 限定） -------------------------------------
    # 教員を作成
    "teachers.teacher-form": "system_admin/10-admin-teachers.md#teacher-form",
    # 教員アカウント一覧
    "teachers.user-list": "system_admin/10-admin-teachers.md#user-list",
    # 停止… / 再開
    "teachers.user-suspend": "system_admin/10-admin-teachers.md#user-suspend",
    # パスワード再設定…
    "teachers.user-reset": "system_admin/10-admin-teachers.md#user-reset",
    # 利用状況…
    "teachers.user-activity": "system_admin/10-admin-teachers.md#user-activity",
    # 削除予約… / 削除予約を取消
    "teachers.user-delete": "system_admin/10-admin-teachers.md#user-delete",
    # 移管…
    "teachers.user-transfer": "system_admin/10-admin-teachers.md#user-transfer",

    # --- versioning.* — 共有版管理（V層。コース管理/教材管理の「版の管理」ボタン、通知ベルから開く横断UI） ---------------
    # 削除予約を取り消す
    "versioning.cancel-deletion": "teacher/25-admin-versioning.md#cancel-deletion",
    # 通知（🔔）
    "versioning.inbox": "teacher/25-admin-versioning.md#inbox",
    # 更新を取り込む・更新を確認する
    "versioning.inbox-adopt": "teacher/25-admin-versioning.md#inbox-adopt",
    # 通知を既読にする・すべて既読にする
    "versioning.inbox-read": "teacher/25-admin-versioning.md#inbox-read",
    # 共有版として発行
    "versioning.publish": "teacher/25-admin-versioning.md#publish",
    # 削除を予約
    "versioning.schedule-deletion": "teacher/25-admin-versioning.md#schedule-deletion",
    # 版の履歴
    "versioning.version-history": "teacher/25-admin-versioning.md#version-history",
}


def _split_ref(ref: str) -> tuple[str, str, str]:
    """``"teacher/<file>.md#<anchor>"`` を ``(audience, file, anchor)`` に分解する。"""
    audience, _, rest = (ref or "").partition("/")
    file, _, anchor = rest.partition("#")
    return audience, file, anchor


def split_manual_ref(ref: str) -> tuple[str, str]:
    """``"teacher/<file>.md#<anchor>"`` から audience を除いた ``(file, anchor)`` を返す。

    ``routes/admin_assistant.py`` の usage_help ハンドラが、直接解決した UI アンカーの
    内容から citation（file/anchor/title）を組み立てるために使う。
    """
    _audience, file, anchor = _split_ref(ref)
    return file, anchor


# ロールごとに閲覧できる audience（上位継承。student は admin 側に一切含めない）。
_ROLE_AUDIENCES: dict[str, tuple[str, ...]] = {
    "TEACHER": ("teacher",),
    "SYSTEM_ADMIN": ("teacher", "system_admin"),
}

_AUDIENCE_INDEX_BUILDERS = {
    "teacher": _manual._teacher_index,
    "system_admin": _manual._system_admin_index,
}


def resolve_admin_ui_anchors(role: str) -> dict[str, dict]:
    """``ADMIN_UI_ANCHORS`` の各アンカーを ``role`` から見える索引で解決する。

    TEACHER は teacher/ のみ、SYSTEM_ADMIN は teacher/ + system_admin/ を解決する
    （fail-closed。SYSTEM_ADMIN 限定タブの節は TEACHER には返らない）。未知のロールは
    空 dict（student 等、admin 側に存在しないロールを渡しても安全側に倒れる）。

    解決できない（ファイル/アンカーが実在しない、または teacher/・system_admin/ 以外を
    指す不正なマッピング、もしくは role から見えない audience）ものは結果に含めない
    （fail-open。起動時 validator の ``check_admin_ui_anchor_mappings`` が別途違反として
    検出する）。

    Returns:
        ``{anchor_id: {"title": .., "body": .., "manual_anchor": ..}}``。
    """
    allowed_audiences = _ROLE_AUDIENCES.get(role)
    if not allowed_audiences:
        return {}

    idx_by_audience: dict[str, dict] = {}
    for audience in allowed_audiences:
        idx, _excluded = _AUDIENCE_INDEX_BUILDERS[audience]()
        idx_by_audience[audience] = idx

    out: dict[str, dict] = {}
    for anchor_id, ref in ADMIN_UI_ANCHORS.items():
        audience, file, anchor = _split_ref(ref)
        if audience not in idx_by_audience:
            continue
        sec = idx_by_audience[audience].get(f"{file}#{anchor}")
        if sec is None:
            continue
        out[anchor_id] = {
            "title": sec.get("title", ""),
            "body": sec.get("body", ""),
            "manual_anchor": ref,
        }
    return out


def resolve_admin_ui_anchor(anchor_id: str, role: str) -> Optional[dict]:
    """単一の UI アンカーを解決する（usage_help ハンドラの ui_anchor 優先解決用）。

    ``ADMIN_UI_ANCHORS`` に無い、または ``role`` から解決できないアンカーIDは
    ``None``（呼び出し側は従来の非LLM guidance 検索へフォールバックする）。
    """
    if anchor_id not in ADMIN_UI_ANCHORS:
        return None
    return resolve_admin_ui_anchors(role).get(anchor_id)
