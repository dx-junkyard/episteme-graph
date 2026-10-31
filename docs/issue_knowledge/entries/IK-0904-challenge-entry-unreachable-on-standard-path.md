---
id: IK-0904
title: "疑義の入口が前提と主張の台帳にしか無いのに、コースを作らずに解析した教材から組んだコースでは前提の地図の点が式と部品だけ・前提候補は監査とマイニングの後もゼロで、主張の台帳を開く道も D層の画面に無いため、疑義 → 検証提案の流れに標準の経路から一度も入れず、入口が無い理由も画面に出ない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/architecture/persona_enactment_testing_design.md §22.8"
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/stakes_ledger_design.md"
feature_context:
  realizing: "教員が前提の地図・ノードの台帳から疑義を残し、検証の提案へ進める"
  layers: [doubt_d, stakes_ledger_sl, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
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
    接続軸 target: 疑義の受け皿は routes/doubt.py の _CHALLENGE_TARGET_TYPES = (assumption, claim) と doubt-atlas.js 362 行の分岐に限られる。標準の経路で前提の地図に載る対象は ledger_builder が作る component（グラフのノード）と equation
    で、前提（assumption_nodes）は監査・マイニングが候補を作らない限り現れない。主張の台帳は地図の点にも合意リストにも出ず、原稿スタジオのノード詳細も部品の台帳を開く。受け皿の対象の種類と、経路が届ける対象の種類が交わらない。処理軸 none。構造軸 none（疑義を前提・主張に限ること自体は D3-1 の設計）。確信度 medium
    は、式やノードにも疑義を許すかが設計判断を含むため。
generalization:
  level: general
  general_form: "操作の入口を一部の対象の種類にだけ置いたのに、利用者が標準の経路で辿り着く対象はそれ以外の種類だけで、入口が無い理由も示さないため、機能は存在するが一度も使えない"
pattern: last-mile-missing
discovery:
  perspective: [reproduction, boundary_walk]
  note: >-
    te-04 と te-01 が前提の地図・合意リスト・ノード詳細のどこから開いても『疑義を残す』が無く（ui_guard）、前提候補はコーパス監査・前提マイニング・再読込の後もゼロで、両者とも検証提案の場面を gave_up した。run:
    uxsim/runs/c-r25-doubt-r2/20261022T215750Z-73306（seq24・27・28・29）・uxsim/runs/c-r25-doubt-r2/20261022T220507Z-77990（seq20・23・24・25）。
resolution:
  perspective: [carry_through]
  note: >-
    第 2 波で是正（2026-10-23）。式・部品の台帳 GET が challenge_allowed: false + challenge_note（CHALLENGE_TARGETS_NOTE / CHALLENGE_TARGETS_NONE_NOTE）+ supporting_claims（ノードの主張参照のうち live の主張・最大 6・ledger_targets.supporting_claims_for_target）を返し、画面はそこから主張の台帳を開ける（疑義を残せる）。前提の一覧 GET は mining_note（範囲にマイニング由来の候補があるか）を毎回返す。
  landed_in:
    - "backend/core/doubt/ledger_targets.py"
    - "backend/core/doubt/scope.py"
    - "backend/api/routes/doubt.py"
    - "frontend/public/js/doubt-atlas.js"
    - "backend/tests/test_doubt_ledger_document_key_round25.py"
  verification:
    methods: [guardrail]
    unverified:
      - "前提の地図に主張の点を出す層の選択は入れていない"
      - "mining_note の画面描画（前提の一覧）は入れていない"
      - "標準の経路で疑義から検証提案まで歩くことは第 3 周（te-05）で確かめる"
related: [IK-0852, IK-0853, IK-0899]
view_of: []
history: []
---

## 課題

症状: 前提の地図の点は式と部品だけで、台帳詳細には「疑義はありません。」だけが出て［疑義を残す］ボタンが無い。
前提候補はコーパス監査（「新しい前提の候補はありませんでした」）・前提マイニング（受け付けただけで結果の事実文は無い）の
後もゼロ。主張の台帳を開く道が D層の画面に無いので、疑義を一つも残せず、検証提案にも進めない。なぜこの対象には疑義の
入口が無いのか、どの対象なら残せるのかも出ない。

原因: 疑義の受け皿（前提・主張）と、標準の経路が地図に載せる対象（式・部品）が交わらない。

## 解決の観点

第 2 波で是正（2026-10-23。frontmatter の resolution）。以下は起票時の是正案。是正案（どれを採るかは設計書 doubt_layer_issues の D3-1 の範囲で担当が決められる）:

- 式・部品の台帳詳細の疑義の区画に、事実文「疑義は前提と主張に残せます。この対象を支える主張は次のとおりです」と、
  対象のノードの `linked_claim_ids`（live の主張・本文の抜粋）を並べ、それぞれから主張の台帳を開けるようにする。
- 前提の地図に主張の点を出す（台帳の claim 行は既にある。表示する層の選択を地図に足す）。
- 前提マイニングの非同期の結果を、次の再読込で「前回のマイニングで候補はありませんでした」と事実文で出す
  （受付だけで終わらせない。IK-0853 の残り）。
- 式・ノードへの疑義を許すかは別の設計判断（許すなら `_CHALLENGE_TARGET_TYPES` と UI を同時に広げる）。

## 発見の観点

2 人の教員に地図・合意リスト・ノード詳細のすべての入口から疑義を残させ、どこにもボタンが無いことを確かめた（境界の歩き）。

## 一般化

入口を置いた対象の種類と、利用者が辿り着く対象の種類を突き合わせないと、機能は存在するのに届かない。確かめ方は、
標準の経路だけで入口まで歩き、少なくとも 1 件の操作が成立することを見ること。

第 3 周の再演: 残存（2026-10-23）。te-05 が標準の経路（前提の地図 → 散布図の点の台帳・原稿スタジオのノード台帳）から疑義に一度も届かず、検証提案・取り下げも
不達（blocked）。理由は 3 つ重なる。①支える主張の一覧が常に空（グラフの主張参照が agent ID で、supporting_claims_for_target が UUID だけを採る — IK-0930）
②台帳行の無いノードは GET 404 で、疑義の誘導そのものが出ない（IK-0931）③前提の地図の点が claim チェーンの導出ステップで埋まり、前提・主張の点が無い
（IK-0933。是正案の「地図に主張の点」は入っていない）。mining_note は出ている。
run: uxsim/runs/c-r25-doubt-r3/20261023T032742Z-99529（seq3・5・8・9・10）。

第 3 波で是正（2026-10-23）: 3 つの根をそれぞれ直した。①支える主張は局所 ID も UUID へ解決して並ぶ（IK-0930）②台帳行の無いノードも 200（exists:false）で疑義の誘導と支える主張を返し、画面が同じ規則で描く（IK-0931）③前提の地図・合意リスト・ブリーフの母集合から claim チェーンの導出ステップを外した（IK-0933）。地図に主張の点を足す案は入れていない。標準の経路を歩く確認は第 4 周（te-03 × t-db-challenge-from-node）。

第 4 周の再演（2026-10-25）: GONE（取り下げを除く）。te-03 が原稿スタジオのノード台帳 → 支える主張（UUID で並ぶ — IK-0930 GONE）→ 主張の台帳で疑義を 2 件残し、
1 件を検証提案 → 着手まで進めた（4 周目で初めて成立）。取り下げは runner の番号の取り違えで送られず、製品では未確認（harness_fixes_doubt.md H28）。提案を後から読めない件は IK-0968。
run: uxsim/runs/c-r25-doubt-r4/20261024T233017Z-7034（seq5〜11）。
