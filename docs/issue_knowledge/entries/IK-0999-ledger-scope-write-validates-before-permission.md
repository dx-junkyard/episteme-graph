---
id: IK-0999
title: "台帳のスコープ記帳 API（POST …/ledger/{type}/{id}/scopes）は、入力の検査（軸・根拠・理由の 422）を対象の教材の編集権限の検査（_require_editable_ledger_target）より先に走らせるため、閲覧だけの教員の不完全な要求には「根拠を入力してください」が返り、権限が無いから書けないのか、根拠を足せば書けるのかが応答から分からない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/features/stakes_ledger_design.md §16.8"
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
feature_context:
  realizing: "閲覧だけの教員が他の教員の論文の台帳に書き込めないことを、サーバの応答で確かめる"
  layers: [doubt_d, auth_visibility]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [ordering]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    統制軸 ordering: add_ledger_scope は ①4 軸のいずれか ②evidence_ids か evidence_quote ③reason を順に 422 で検査し、その後で _require_editable_ledger_target（権限・不在は同一 404）を呼ぶ。権限の弁そのものは健在で迂回ではない（根拠つきの要求は 404 で止まる — コード確認）。422 の本文は対象の存在に依らない定型文なので存在の漏洩は無い。課題は検査の順序で、権限の無い利用者に「直せば通る」と読める入力エラーを先に見せる。確信度 medium は、同じ順序が他の台帳書き込み API にもあるかを未点検のため。
generalization:
  level: repo_pattern
  general_form: "書き込みの要求で入力の妥当性を権限より先に検査するため、権限の無い利用者に『入力を直せば通る』と読める拒否を返す"
pattern: gate-position-wrong
discovery:
  perspective: [boundary_walk, reproduction]
  note: >-
    te-02（PINN を閲覧だけ）が画面を経ない記帳の 1 手を根拠なしで送り、HTTP 422「根拠（原文の引用など）を入力してください。」が返った。PINN の台帳のスコープは増えていない（受け付けられていない）。run: uxsim/runs/c-r25-doubt-r5/20261027T070623Z-23504（seq8）。
resolution:
  perspective: [order_and_budget, guardrail_fix]
  note: >-
    台帳の書き込みルート（スコープ・検証状態・覆る条件・根拠の線・疑義・検証提案の作成と状態遷移・前提の手動登録と確定）で、対象の解決と権限の検査を本文の 422 より前へ移した（404 / 422 の意味は不変・パスの target_type の 422 だけは先に残す）。権限の検査を持っていなかった PATCH /proposals/{id} に疑義の対象の閲覧ゲート（_require_viewable_proposal_target）を足した。AST のガードレールが「権限の呼び出しが本文の 422 より前」を固定する。
  landed_in:
    - "backend/api/routes/doubt.py"
    - "backend/tests/test_doubt_round25_wave5.py"
    - "backend/tests/test_stakes_ledger_api.py"
    - "backend/tests/test_doubt_ledger_document_key_round25.py"
  verification:
    methods: [guardrail]
    unverified:
      - "権限の無い教員から入力の欠けた要求を実機（砂場）で送り 404 になることは run で確かめていない（第 6 周の H34）"
related: [IK-0900, IK-0931]
view_of: []
history: []
---

## 課題

症状: 閲覧だけの教員が記帳を送ると、権限の拒否ではなく入力の不備が返る。教員は「根拠を書けば記帳できる」と読める。

原因: 入力検査が権限検査より先に並んでいる。

## 発見の観点

境界の歩き（権限の無い利用者が画面を経ずに送る）と再現。

## 解決の観点

第 25 周 面 B 第 5 波（2026-10-27・docs/features/stakes_ledger_design.md §16.8）で解決。台帳の書き込みルート（スコープ・検証状態・覆る条件・根拠の線・疑義・検証提案の作成と状態遷移・前提の手動登録と確定）で、対象の解決と権限の検査を本文の 422 より前へ移した（404 / 422 の意味は不変・パスの target_type の 422 だけは先に残す）。権限の検査を持っていなかった PATCH /proposals/{id} に疑義の対象の閲覧ゲート（_require_viewable_proposal_target）を足した。AST のガードレールが「権限の呼び出しが本文の 422 より前」を固定する。

起票時の是正案: 是正案: 台帳の書き込み API 群で、対象の解決と編集権限の検査（_require_editable_ledger_target）を入力検査より前に置く（不在と権限なしは従来どおり同一 404 で存在を漏らさない）。他の台帳書き込み経路（検証状態・覆る条件・根拠の線・前提の確定）も同じ順序かを一覧にし、ガードレールで「権限の呼び出しが 422 より前」を固定する。

## 一般化

拒否の理由は最初に失敗した検査で決まる。確かめ方は、権限の無い利用者から入力の欠けた要求と完全な要求の両方を送り、どちらも権限の拒否で止まることを見ること。

第 6 周の再演: GONE（2026-10-27・有効な body）。te-05（PINN を閲覧だけ）が軸・根拠・理由・document_id の揃った body で画面を経ずにスコープを記帳すると 404 で拒否され、台帳は増えていない。不完全な body での順序（権限 → 入力）は今周の run では再び当てていない（コードで確認: routes/doubt.py add_ledger_scope は _require_editable_ledger_target を入力検査より先に呼ぶ）。拒否の文が英語の「Ledger target not found」で画面の理由文と食い違う点は IK-1027。run: uxsim/runs/c-r25-doubt-r6/20261027T122225Z-3508（seq8）。

第 7 周: 未再訪（2026-10-28）。te-01 の画面を経ない記帳は有効な body（runner が根拠・理由を埋める）で 403 だったので、不完全な body での権限 → 入力の順は run で当たっていない（頭脳ノートの「不完全な body で権限が先」は偽所見 — harness H43）。コード上の順序は第 5 波のまま。run: uxsim/runs/c-r25-doubt-r7/20261027T171356Z-51086（seq8）。

第 8 周の再演: GONE（2026-10-28・8 周目で初当て）。te-01（PINN TeX の閲覧のみ）が根拠を空にした不完全な body を画面を経ずに送ると 403 + 画面と同文の理由が返り、422（根拠を入力してください）より先（route も対象の解決と編集権限 → 入力検査の順）。有効な body も同じ 403。run: uxsim/runs/c-r25-doubt-r8/20261027T223243Z-92707（seq8・9）。
