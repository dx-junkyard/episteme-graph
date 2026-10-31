---
id: IK-0971
title: "学習者向けの台帳の一行（GET /api/learning/courses/{id}/ledger/…）は台帳行の無い対象に 404 を返し、画面は何も描かないため、出典タブでは「まだ記帳されていません」が出る式・主張と無言の式が混ざり、部品の説明ポップアップは常に無言になる — IK-0906 の是正（説明ゼロでも一行を出す）が、部品にはまず行が無いので届かない"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/features/doubt_layer_issues.md"
feature_context:
  realizing: "学生が出典タブ・部品の説明で、その内容がどこまで確かめられているかを一行で読む"
  layers: [doubt_d, learner_experience_b, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
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
    接続軸 information: routes/doubt.py get_learner_ledger_line は learner_ledger_line が None（行なし）のとき 404（docstring「台帳行が無ければ 404（UI は
    セクション自体を出さない fail-closed）」）。app.js fetchLearnerLedgerFactLine は res.ok でないと null を返し、appendLearnerLedgerLine は何も足さない。
    台帳の行は ledger_builder のバックフィルで一部の式・主張にだけあり（unknown で作られる）、部品（理論コンポーネント）には無いので、同じ「記帳が無い」事実が
    行の有無で「まだ記帳されていません」と無言に分かれる。構造軸 representation: 「記帳なし」を行の不在で表しており、学習者向けの表現に居場所が無い
    （教員側は IK-0931 で exists:false に直した）。処理軸 none。統制軸 none（コースの教材ゲートは効いている）。
generalization:
  level: repo_pattern
  general_form: "「記録がまだ無い」という事実を記録行の不在＝見つからない応答で表すため、記録の有無の偶然で同じ事実が文になったり無言になったりする"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    st-07 が出典タブを開くと、式・主張の一行は 200 の対象にだけ出て、同じカードの eq_tovnet ほかは 404 で何も出なかった（同文の一行が反復する一方で抜けもある）。
    部品の説明ポップアップは「承認済みの説明がまだありません」だけで、台帳の一行は 404 で出なかった（出典タブは「未記帳」と言うのに部品は無言）。
    run: uxsim/runs/c-r25-doubt-r4/20261024T233257Z-31520（seq8・10）。
resolution:
  perspective: [representation_change, responsibility_move]
  note: >-
    学習者の台帳 GET（get_learner_ledger_line）は、受講ゲートと対象がコースの教材に実在することを通れば行が無くても 200（exists:false・「この内容の検証スコープはまだ記帳されていません。」・target_label）を返す（_learner_unrecorded_line）。行のある応答には exists:true。404 は対象が無い・教材外のときだけ。出典タブ（annotateOneSourceCardLedger）は同じ事実文を 1 行に畳んで主語を並べ、3 行の上限は畳んだ後で数える。SA層 verification 解決器は従来どおり行なしで何も言わない。
  landed_in:
    - "backend/api/routes/doubt.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_learner_ux_static.py"
    - "backend/tests/test_doubt_round25_wave4.py"
  verification:
    methods: [guardrail]
    unverified:
      - "出典タブと部品の説明ポップアップの実表示はブラウザで未確認"
      - "音声で聞いたときの畳み方の聞き取りやすさは未確認"
related: [IK-0906, IK-0931, IK-0901]
view_of: []
history: []
---

## 課題

症状: 出典タブの台帳の一行が、ある式には「まだ記帳されていません」と出て別の式には何も出ない。部品の説明ポップアップは台帳の一行を出さない。

原因: 学習者の台帳 GET が行の無い対象に 404 を返し、画面は 404 を「出さない」と解釈する。行の有無はバックフィルの偶然で決まる。

## 発見の観点

学生の出典タブと部品の説明を開き（再現）、各一行の要求の応答（200 / 404）を並べた（データの照合）。

## 解決の観点

第 4 波で是正（2026-10-25）。教員側の IK-0931 と同じく、「記帳なし」を事実として 200 で返す。

## 一般化

「無い」を見つからない応答で返すと、画面は「出さない」と「無い」を区別できない。確かめ方は、記録の無い対象・ある対象を同じ面に並べ、両方に一行が出ることを見ること。

第 5 周の再演: 部品と式は GONE（2026-10-27）。st-10 の部品の説明ポップアップに「この内容の検証スコープはまだ記帳されていません。」が出て（exists:false の 200）、出典タブも同じ文が出典ごとに 1 行へ畳まれた。残り: 回答がコースの教材外の資料（PDF 版など）のチャンクを引いた出典カードは、本文が 404（「このコースの教材からは開けません」）で、claim-refs は主張を返すが台帳の一行は 404（教材外）なので、カードに何も添えられない — 「記帳なし」ではなく「教材外の出典」であることがカードで言われない（出典カード 3・4）。教材外の出典を引くこと自体は IK-0987 / IK-0996 系。記帳ありの部品の一行は、周の前の記帳が PDF 版の部品に置かれ学生のコースは TeX 版だったため未確認（harness H37）。run: uxsim/runs/c-r25-doubt-r5/20261027T071839Z-88666（seq8・10）。

第 6 周の再演: 残存（2026-10-27・教材外の出典）。st-05 の出典タブで、本文が 404（コースの教材外）の出典カード 3 枚は主張の台帳の一行も 404 で無言のまま、同じ題名の論文（別版）であることも見分けられない。出典 1 は「一行を引ける式・主張がこのカードに無い」で無言。部品の説明ポップアップの「この内容の検証スコープはまだ記帳されていません。」は第 5 周どおり（ただし開いた部品は IK-1021 で別物）。run: uxsim/runs/c-r25-doubt-r6/20261027T123134Z-9179（seq8・10）。
