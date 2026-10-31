---
id: IK-0428
title: "確認問題の要件に、問いと無関係な「数式 eq_blk_003_0055 の意味または役割に触れる」が毎回足され（内部 ID が学習者向けの要件に出る）、テンプレートの問いの模範解答に英文のトピック要約が入る"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、トピックの確認問題に要件と模範解答を補う
  layers: [course_builder]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: _fill_check_question_detail は問いの文を見ずに、トピックの先頭 3 式すべてについて
    「数式 {label or equation_id} の意味または役割に触れる」を要件へ足していた。label が無い式では
    equation_id（eq_blk_003_0055 等）がそのまま入る（logic）。模範解答が空のときはトピックの要約を
    そのまま入れ、要約は解析由来の英文のことがある。接続軸: 足した要件は「その問いがその式を扱う」
    という対応を主張するが、対応は取れていない（meaning）。第 9 周の req 00154 / 00158 / 00160 / 00162 の
    下書きで、全問に内部 ID の要件が付き、00160 のテンプレートの問いの模範解答が
    「Corrects population inference for …」だった。構造は none（medium: 要件の出所を表現で分けていない
    とも読める）。統制は none。
generalization:
  level: general
  general_form: 対応を確かめずに既定の要素を足し、足した要素が「対応あり」と読まれる
pattern: fallback-fabricates-missing-link
discovery:
  perspective: [data_inspection]
  note: 第 9 周のコース内容の生成プロンプトの「現在の下書き」の確認問題を読んだ。
resolution:
  perspective: [fail_closed, guardrail_fix]
  note: >-
    数式の要件は、問いの文がその式を参照しているとき（印字番号「式 (12)」・式の本体・ID のいずれかが
    問いにある）だけ足し、式は印字番号で呼ぶ（番号が読めなければ「問いにある数式の意味または役割に
    触れる」）。旧形式の自動要件と内部 ID（eq_* / comp_* / claim_* / synth_claim_* / ev_* / UUID）を含む
    要件は外し、模範解答からは埋め込み記法と内部 ID を取り除く。模範解答が空のときに要約を使うのは
    日本語を含む要約だけにし、無ければ学習目標、それも無ければ固定文にした。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0427_0429_course_draft_hygiene.py
    - backend/tests/test_course_content_builder.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成（第 9 周のコースで、確認問題の要件から内部 ID が消えるか）
      - 問いが式を言葉だけで指す場合（「損失関数の式」）は参照と判定しないので要件が付かない
related: [IK-0409]
view_of: []
history: []
---

## 課題

確認問題の要件に、問いと無関係な内部 ID 入りの数式要件が毎回足されていた。

## 発見の観点

生成モデルが受け取った下書きの確認問題を読んだ。

## 解決の観点

問いが式を参照しているときだけ、印字番号で要件を足す。確かめられない対応は足さない。

## 一般化

既定値で補った要素は、補ったことが後段に伝わらないと「対応あり」と読まれる。
