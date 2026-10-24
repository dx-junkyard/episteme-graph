---
id: IK-0933
title: "前提の地図の点・未検証合意リスト（教員・学生）・ゼミ前ブリーフの〔脆い前提〕が、前提ではなく式の詳細層の導出ステップ（主張を出現順に並べた claim チェーンのノード「中間主張を導出: …」）で埋まり、同じ文面の重複・文の切れ端・文字化けまで「前提」として並ぶ一方、疑義を残せる前提・主張の点は 1 つも出ない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/theory_module_layer_design.md §7"
  - "docs/features/stakes_ledger_design.md"
feature_context:
  realizing: "教員と学生が、依存の広い・検証の薄い前提を地図と一覧から選び、疑義やゼミの順番の材料にする"
  layers: [doubt_d, seminar_brief, theory_artifacts, frontend_admin_ui, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [decomposition]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 meaning: core/doubt/ledger_builder.py は debug 層以外のグラフのノードをすべて component の台帳対象にし（168〜183 行）、式の詳細層の claim チェーン由来の
    ノード（eq_op_05xx「中間主張を導出: The filament is thermally supercritical.」）も含む。負荷度は下流到達で決まるので、直列に並んだ claim チェーンは最高位・高に
    集まり、assumption-atlas・open-assumptions・seminar_brief の fragile_assumptions の母集合を占める。理論モジュール層の O-2（docs/features/theory_module_layer_design.md
    §7）は claim チェーンを構造の成員にしないと決めたが、D層の母集合はこの判断を受けていない。構造軸 decomposition: 台帳の対象の粒度（集約ノード・式の手順・主張の
    並び）を区別せずに 1 つの集合へ投影している。学習者の見出しは「検証の記録がない前提」で、中身の種別と食い違う。処理軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "別の判断で「構造ではない」と決めた生成物を、別の層が同じ集合の要素として拾い続けるため、利用者向けの一覧が見出しの種別と違うもので埋まる"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction, invariant_audit]
  note: >-
    te-05 と te-02 の前提の地図、st-01 と st-04 の「検証の記録がない前提」、te-05 のブリーフの〔脆い前提〕がいずれも「中間主張を導出: …」の英文で、同文面の重複・
    「Deharveng et al. 2005).」・「�� ≈ 0.75」が並んだ。run: uxsim/runs/c-r25-doubt-r3/20261023T032742Z-99529（seq3・18）/
    uxsim/runs/c-r25-doubt-r3/20261023T033238Z-24957（seq3）/ uxsim/runs/c-r25-doubt-r3/20261023T033451Z-40732（seq10）/
    uxsim/runs/c-r25-doubt-r3/20261023T070217Z-13811（seq10）。
resolution:
  perspective: [canonical_source]
  note: >-
    claim チェーン由来の詳細ノードの判定を theory_modules/builder.py の公開関数 claim_sequence_node_ids に出し（グラフレビューのキャンバスから外す判定と同じ正本）、core/doubt/population.py の ClaimChainExclusion が前提の地図（assumption-atlas）・未検証合意リスト（教員・学習者）・ゼミ前ブリーフ（脆い前提・一点吊り・晴れ間）・discuss 開幕の前提（compile_open_assumptions の既定）の母集合から外す。外したときは population_note（「論文の主張を出現順にたどる導出の手順は、前提ではないためこの一覧に含めていません。」）を一覧に添える（台帳の行は残る — 情報を落とさない）。依存の伝播（反実仮想・負荷・支持線）は従来どおり全ノードで計算する。
  landed_in:
    - "backend/core/doubt/population.py"
    - "backend/core/theory_modules/builder.py"
    - "backend/core/doubt/open_assumptions.py"
    - "backend/core/doubt/seminar_brief.py"
    - "backend/api/routes/doubt.py"
    - "frontend/public/js/doubt-atlas.js"
    - "frontend/public/js/app.js"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_doubt_round25_wave3.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実データ（Cepheus B）で地図・合意リスト・ブリーフの先頭に前提・主張・主グラフのノードが並ぶこと"
      - "導出の解析結果（derivation_chain）が無い run では外さない（従来どおり）こと"
related: [IK-0904, IK-0593, IK-0752]
view_of: []
history: []
---

## 課題

症状: 教員の前提の地図・未検証合意リスト・ゼミ前ブリーフ、学生の「このコースの教材で、検証の記録がない前提」が、論文の導出の
並び（主張を出現順に並べた鎖のステップ）で埋まる。前提に見えるものは無く、同じ文の重複・文の切れ端・文字化けが並び、疑義を残せる
前提・主張の点が地図に無い。学生は「どれが大事な前提か分からない」と読み切れない。

原因: D層の台帳対象の母集合が、グラフのノードの粒度を区別せずに作られている。claim チェーンは理論モジュール層で構造の成員から
外す判断があるが、D層はその判断を受けていない。

## 発見の観点

教員 2 人・学生 2 人に地図・一覧・ブリーフを開かせ（再現）、見出しの種別（前提）と中身の種別（導出のステップ）を突き合わせた（不変条項の監査）。

## 解決の観点

claim チェーンを構造から外す判断の正本を理論モジュール層の 1 つにし、D層の母集合もそれを通した（canonical_source）。（第 25 周 第 3 波・2026-10-23）

## 一般化

ある生成物を「構造ではない」と一つの層で決めても、他の層の集合に流れ続けると、利用者向けの一覧は見出しと違うもので埋まる。
確かめ方は、一覧の各要素の種別を数えずに読み、見出しの種別に当たるものが先頭に並ぶことを見ること。
