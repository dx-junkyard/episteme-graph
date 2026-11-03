---
id: IK-0974
title: "教員の教材一覧（/api/admin/materials）に、自分の教材か・編集の共有か・閲覧だけかの区別が無く、閲覧だけの論文は台帳を開いて「編集権限が無いため記帳できません」と言われるまで分からない（コース一覧には role_label がある）"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
feature_context:
  realizing: "教員が教材一覧から、自分が記帳・編集できる論文を選んで台帳やグラフを開く"
  layers: [auth_visibility, frontend_admin_ui, doubt_d]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [assignment]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: /api/admin/materials の各行は visibility・group_id・reference_health 等を返すが、閲覧者にとっての権限（owner / editor / viewer）を返さない。
    services.resolve_document_access は view / edit / owner を解決できるのに、一覧へ運ばれていない。/api/admin/courses は role / role_label を返し、コース一覧は
    〔所有〕〔共有（編集可…）〕を出している（面ごとに非対称）。統制軸 assignment: 誰が記帳・編集の責任を持てるかが、操作の前に見えない。
    構造軸 none（列を足すだけ）。処理軸 none。
generalization:
  level: repo_pattern
  general_form: "一覧が各行の権限を返さないため、利用者は対象を開いて拒否されるまで、自分にできる操作の範囲を知らない"
pattern: permission-and-affordance-asymmetric
discovery:
  perspective: [boundary_walk, symptom_report]
  note: >-
    閲覧だけの PINN を持つ te-01 と editor の Cepheus を持つ te-03 が教材一覧を開き、どちらも「どれが編集できてどれが閲覧だけか」が一覧に無いと記した（全行 public の表示のみ）。
    te-01 は台帳を開いて初めて権限の無さを知った。run: uxsim/runs/c-r25-doubt-r4/20261024T232812Z-98847（seq4・5）/ uxsim/runs/c-r25-doubt-r4/20261024T233017Z-7034（seq4）。
resolution:
  perspective: [carry_through]
  note: >-
    /api/admin/materials の各行に role（owner / editor / viewer）・role_label（label_vocab.MATERIAL_ROLE_LABELS: 所有 / 共有（編集可） / 閲覧のみ）を足し、viewer の行には編集の共有を頼む先（所有者の表示名を含む edit_request_note・教員向け一覧のみ）を添えた。判定は user_can_edit_document と同じ条件（所有者・document の editor 共有・SYSTEM_ADMIN）を一覧の SQL で一括に読む（N+1 なし）。admin.js はタイトル下の開示範囲バッジの横に出す。
  landed_in:
    - "backend/api/routes/admin.py"
    - "backend/api/schemas.py"
    - "backend/core/label_vocab.py"
    - "frontend/public/js/admin.js"
    - "docs/manual/teacher/11-admin-materials.md"
    - "backend/tests/test_label_vocab_guardrails.py"
    - "backend/tests/test_doubt_round25_wave4.py"
  verification:
    methods: [guardrail]
    unverified:
      - "一覧の SQL（is_editor の EXISTS・所有者の LEFT JOIN）は実 DB で未実行"
      - "コース経由（object_group_permissions object_type='course'）でしか見えない教材は viewer になる（台帳の記帳ゲートと同じ）— 画面で未確認"
related: [IK-0900, IK-0407]
view_of: []
history: []
---

## 課題

症状: 教材一覧で、自分が編集できる論文と閲覧だけの論文を見分けられない。閲覧だけと分かるのは台帳を開いて拒否の文を読んだとき。

原因: 一覧の応答が行ごとの権限を返さない（コース一覧は返している）。

## 発見の観点

権限の違う 2 人の教員で同じ一覧を開いた（境界の歩行）。

## 解決の観点

第 4 波で是正（2026-10-25）。一覧に権限の役割を運ぶ。

## 一般化

一覧に権限が無いと、利用者は試行と拒否でしか自分の範囲を知れない。確かめ方は、所有・編集共有・閲覧のみの 3 人で同じ一覧を開き、行ごとの役割が操作の前に読めることを見ること。

第 5 周の再演: GONE（2026-10-27）。教材一覧の各行に「共有（編集可）/ 閲覧のみ」、閲覧のみの行に edit_request_note「…所有者の uxsim-bootstrap-teacher さんに頼めます」が出た。残り（軽）: 頼む先の名前がログイン名風（H21 と同じ表示名の問題）・同じ文が所有者ごとに畳まれず行ごとに反復・台帳の画面（閲覧だけの理由の文）には頼む先が出ない（教材一覧を経ずに台帳へ来た教員には届かない）。run: uxsim/runs/c-r25-doubt-r5/20261027T070623Z-23504（seq4・6）。
