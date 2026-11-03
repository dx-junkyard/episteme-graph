---
id: IK-1027
title: "閲覧だけの教員が台帳へ書き込む要求を送ると、サーバは英語の「Ledger target not found」（404）で断る — 同じ対象を画面では閲覧できていて、画面の理由文は「この論文の編集権限が無いため、台帳への記帳はできません」と言うのに、サーバの文は対象が無いと言い、言語も理由も画面と食い違う"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/features/stakes_ledger_design.md"
feature_context:
  realizing: "閲覧だけの教員が台帳への記帳を試みたとき、断られた理由を画面と同じ言葉で受け取る"
  layers: [doubt_d, auth_visibility]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（コードと run）: routes/doubt.py の _require_editable_ledger_target は「対象が解決できない」と「編集できない」を同一の 404・同一の
    detail（_LEDGER_TARGET_NOT_FOUND = "Ledger target not found"）に畳む（存在の有無を漏らさない fail-closed）。ところが台帳 GET
    （_ledger_target_access）は閲覧できる教員に 200 + can_edit=false + 日本語の can_edit_note を返すので、閲覧できる対象については存在は既に
    開示済みで、404 に畳む理由が無い。処理軸 wording = 英語の内部文言がそのまま利用者に届く。接続軸 meaning = 同じ判定（編集権限なし）を
    GET と書き込みで別の意味（「権限が無い」/「見つからない」）として返す。構造軸 none = 判定の場所は 1 箇所で設計どおり。統制軸 none。
generalization:
  level: general
  general_form: "存在を隠すために拒否を「見つからない」に畳む規則を、存在を既に見せている相手にも適用すると、拒否の文が画面の説明と食い違い、利用者は何が起きたかを誤って読む"
pattern: wording-mismatch
discovery:
  perspective: [boundary_walk, reproduction]
  note: "te-05（PINN を閲覧だけ）が台帳 GET で can_edit=false の理由文を読んだ直後に、画面を経ない有効な body のスコープ記帳を 1 回送り（harness H34 の再プローブ）、応答 404 の detail を画面の文と並べた。run: uxsim/runs/c-r25-doubt-r6/20261027T122225Z-3508（seq5〜8）。"
resolution:
  perspective: [canonical_source, single_point_fix]
  note: >-
    _require_editable_ledger_target は、対象の論文を閲覧できるが編集できない相手に 403 + 画面と同じ理由文（label_vocab.LEDGER_READ_ONLY_NOTE — 台帳 GET の can_edit_note と同じオブジェクト。正本を core/doubt/scope.py から label_vocab へ移し scope.py は再エクスポート）、閲覧もできない・対象が解決できないときは日本語の同一の 404（LEDGER_WRITE_TARGET_NOT_FOUND_NOTE「この台帳の対象は見つからないか、記帳できません。」— 存在の有無を言わない）を返す。判定の順序（IK-0999）は不変。
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/core/doubt/scope.py"
    - "backend/api/routes/doubt.py"
    - "backend/tests/test_doubt_round25_wave6.py"
    - "docs/manual/teacher/18-admin-doubt-atlas.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "画面の理由文とサーバの拒否文を label_vocab の 1 定数にし、台帳 GET と書き込みゲートの両方がそこから返す。"
  verification:
    methods: [guardrail]
    unverified:
      - "閲覧だけの教員から画面を経ない記帳を実機（砂場）で送って 403 と文を読むことは run で確かめていない（第 7 周 te-01）"
related: [IK-0900, IK-0999]
view_of: []
history: []
---

## 課題

症状: 閲覧だけの教員 te-05 は PINN の台帳で「この論文の編集権限が無いため、台帳への記帳はできません（閲覧と疑義の記帳はできます）。」と全記帳ボタンの「（押せない）」を読み、
画面から書く道は無かった。画面を経ずに有効な body（軸・根拠・理由・document_id 付き）で記帳を送ると、サーバは HTTP 404「Ledger target not found」を返した。
拒否そのものは効いている（台帳は増えない — IK-0900 / IK-0999 の弁が run で初めて当たった）が、文は英語で、閲覧できている対象を「見つからない」と言う。

原因: 書き込みのゲートは「不在」と「権限なし」を同じ 404・同じ英語 detail に畳む。存在を漏らさないための規則だが、閲覧できる教員には GET が既に存在と権限の事実を見せている。

## 発見の観点

境界の歩き（GET の閲覧ゲートと書き込みのゲートを同じ教員で並べた）と再現（画面を経ない 1 手）。

## 解決の観点

未解決。閲覧できる相手には 403 + 画面と同文、それ以外は日本語の同一 404。Copilot など画面以外の利用者にも同じ文が届く。

## 一般化

「隠すための畳み込み」は相手が既に何を見ているかで変える。型 wording-mismatch（文の出所が 2 つ — 画面の理由文とサーバの detail）。

## 第 6 波の是正（2026-10-27）

閲覧できる相手は 403 + 画面と同文、それ以外は日本語の同一 404。設計書 §16.9。

第 7 周の再演: GONE（403 の側・2026-10-28）。PINN TeX の editor を外した te-01（閲覧のみ）が画面を経ない有効な body でスコープを記帳すると HTTP 403・detail「この論文の編集権限が無いため、台帳への記帳はできません（閲覧と疑義の記帳はできます）。」で、台帳の画面の理由文（同じ run の seq5・6）と逐語一致した。閲覧もできない / 不在の対象の日本語の 404 は run で未観察。頭脳は「condition だけの不完全な body でも権限が先」と書いたが、runner の scope_record_raw は根拠・理由を既定値で埋める（H34）ので body は有効 — 権限 → 入力の順（IK-0999 の残り）は確かめていない（harness H43）。run: uxsim/runs/c-r25-doubt-r7/20261027T171356Z-51086（seq5〜8）。
