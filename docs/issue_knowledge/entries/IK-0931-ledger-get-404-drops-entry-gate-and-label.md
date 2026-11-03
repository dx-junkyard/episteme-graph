---
id: IK-0931
title: "台帳行のまだ無い対象（主グラフのノード等）では台帳 GET が 404 になり、画面は空の台帳を描くが、第 2 波で応答に載せた疑義の入口（challenge_note・支える主張）・記帳できるか（can_edit / can_edit_note）・対象の名前がすべて欠けるため、行が無い対象に限って記帳ボタンが権限と無関係に押せる形で出て、疑義への誘導も出ない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/architecture/persona_enactment_testing_design.md §22.9"
  - "docs/features/doubt_layer_issues.md"
feature_context:
  realizing: "教員が原稿スタジオのノードを選んで台帳を開き、記帳できるか・疑義をどこに残せるかを 1 画面で読む"
  layers: [doubt_d, frontend_admin_ui, lecture_studio]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: routes/doubt.py の get_ledger_entry は _fetch_ledger_row が None のとき 404 を返し、IK-0900 の can_edit / can_edit_note・IK-0904 の
    _challenge_entry・IK-0901 の target_label は行がある 200 応答にしか載らない。doubt-atlas.js は 404 を entry=null として描き（178〜183 行・renderLedgerInto の
    !entry 分岐）、disableLedgerWrites（442 行）も challenge_note の描画（415 行）も entry が前提。閲覧ゲート（_ledger_target_access）は行の有無と無関係に解決済みなので、
    情報はサーバにありながら 404 で落ちる。構造軸 responsibility: 「台帳の詳細」の応答を行の存在に従属させ、対象についての事実（権限・入口・名前）を行の属性として
    置いている。処理軸 none。統制軸 none。書き込みそのものはサーバの _require_editable_ledger_target が閉じる（IK-0900 のサーバ側は効く）。
generalization:
  level: repo_pattern
  general_form: "対象についての事実（権限・入口・名前）を、その対象の記録行の属性として返すため、記録がまだ無い対象では事実ごと欠け、画面が既定の（許可側の）形で描かれる"
pattern: permission-and-affordance-asymmetric
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    te-05 と te-02 が Cepheus の主グラフ先頭ノードを開くと GET が 404 で、台帳は「検証スコープの記帳なし」と記帳ボタンだけを出し、疑義の誘導（IK-0904）が無かった。
    te-02 がスコープを記帳して行ができた後の GET 200 には challenge_note・can_edit が載った。PINN の同じノードも 404 で同じ形。
    run: uxsim/runs/c-r25-doubt-r3/20261023T032742Z-99529（seq5）/ uxsim/runs/c-r25-doubt-r3/20261023T033238Z-24957（seq5・8・10）。
resolution:
  perspective: [responsibility_move]
  note: >-
    台帳 GET（api/routes/doubt.py get_ledger_entry）は行が無いとき 404 にせず、閲覧ゲート（_ledger_target_access）と対象の実在の確認（論文の中でしか一意でない対象は _scoped_target_in_document、全体で一意の対象は閲覧ゲートが対象の行から論文を引けたこと）を通れば 200 の exists:false（not_recorded_note・target_label・document_title・疑義の入口・can_edit / can_edit_note・空の配列）を返す。404 は実在しない・閲覧できないときだけ。doubt-atlas.js は exists:false を「まだ記帳されていません」と描き、疑義の入口（前提・主張は疑義を残す・式・部品は支える主張へ）と記帳ボタンの無効化を行のある台帳と同じ規則で出す。
  landed_in:
    - "backend/api/routes/doubt.py"
    - "frontend/public/js/doubt-atlas.js"
    - "frontend/public/admin.html"
    - "backend/tests/test_doubt_round25_wave3.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面で閲覧だけの教員が行の無いノードを開き、記帳ボタンが無効で理由の事実文が出ること"
related: [IK-0900, IK-0904, IK-0901, IK-0899]
view_of: []
history: []
---

## 課題

症状: 主グラフのノード（「Theory basis」）の台帳を開くと、行が無いときだけ、対象の名前・疑義の誘導・読み取り専用の事実文が出ず、
記帳ボタンは権限と無関係に押せる形で並ぶ。一度スコープを記帳して行ができると、同じ画面に疑義の誘導と can_edit が現れる。

原因: 台帳 GET が行の不在を 404 で返し、第 2 波で足した対象についての事実（権限・入口・名前）が行のある応答にしか載らない。

## 発見の観点

教員 2 人に同じノードの台帳を開かせ（再現）、404 と 200 の応答のキーの差を描画の分岐まで辿った（経路の追跡）。

## 解決の観点

対象についての事実（名前・権限・疑義の入口）を記録行の属性から対象の解決へ移し（responsibility_move）、行の有無と切り離した。（第 25 周 第 3 波・2026-10-23）

## 一般化

「記録の詳細」API に、記録の無い対象についての判断（権限・入口）を相乗りさせると、記録が無いときに判断ごと消えて、画面は許可側の
既定で描かれる。確かめ方は、記録ゼロの対象で詳細を開き、権限の無い利用者と有る利用者で画面が分かれることを見ること。
