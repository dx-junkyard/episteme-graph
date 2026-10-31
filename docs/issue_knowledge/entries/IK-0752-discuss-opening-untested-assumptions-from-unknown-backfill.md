---
id: IK-0752
title: "discuss_opening の untested_assumptions が、台帳の unknown バックフィルを一律に『未検証の前提』として渡し、要旨の目的・結果・背景の文が開幕素材に流れる"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/features/discuss_opening_authoring_design.md"
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "D層の未検証前提を材料に、discuss 開幕の「議論のきっかけ」を論文ごとに生成する"
  layers: [discuss, doubt_d]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [completion]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: discuss_opening の入力 builder が「前提であること」を台帳の verification_status=unknown で代理判定している。
    ledger_builder は主張に unknown を非LLM でバックフィルするため、要旨の目的・設定・結果・序論の背景の文まで
    「未検証の前提」として渡る（8 件中、前提らしいのは次元の限定の 1 件）。統制軸 completion = 「未検証の前提」という
    状態を、検証状態が未記帳であることで代理判定している。構造軸 representation = 主張と前提（assumption_nodes /
    人が確定した前提）の区別が入力の表現に乗っていない。接続軸 meaning = unknown（記帳なし）が後段で「未検証」と
    読み替えられる。処理軸 none。確定には discuss_opening の input_builder が untested_assumptions を引く SQL・条件と、
    ledger_builder のバックフィル範囲を読む。
generalization:
  level: general
  general_form: "ある種別の項目を、記録が無いことを示す既定値で代理判定して集めるため、既定値で埋められた無関係な項目まで同じ種別として後段に渡る"
pattern: completion-defined-by-proxy
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 M2 が discuss_opening（seq 2129・2PN (A)dS）の材料を読み、untested_assumptions 8 件が要旨の文であることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_M2.md）。同じ頭脳が seq 2132 の scope_candidates で背景文が対象になる同根の症状も記した（IK-0731）。"
resolution:
  perspective: [pending]
  note: "未解決。未検証前提の母集合を、人が確定した assumption / 前提の claim_type に限り、unknown バックフィルの主張は「検証記録なし」として別扱いにする案（コーパスを補う論文のレンズ B と同じ規律）。"
  landed_in:
    - "docs/features/discuss_opening_authoring_design.md"
related: [IK-0731, IK-0665, IK-1013]
view_of: []
history: []
---

## 課題

症状: discuss_opening の untested_assumptions の 8 件が要旨の目的・設定・結果・序論の背景の文で、前提らしいのは「次元の限定」1 件だけ。
開幕の「議論のきっかけ」が前提ではない文を前提として扱う素材から作られる。
原因（仮説）: 台帳の unknown バックフィルを「未検証の前提」の代理指標として使っている。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_M2.md（seq 2129・2132）。

追加の出所（2609.35757・TeX 経路・頭脳 T1）: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2959 — untested_assumptions に定義・記法（u_∥=(n̂·u)n̂、u=v/(aH)、k_{1N} の並進不変性）が「未検証の前提」として並ぶ。同じ材料で速度の分解に operation=solve が付く操作語の誤分類も記されている）。

追加の出所（2610.08751・頭脳 X1・是正前の run（seq 3289〜3310・2026-10-17 03:00〜03:11））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3301 — untested_assumptions 8 件中 4 件が序論の一般論（「BAO から宇宙論パラメータを推定できる」「EFT・VDG は理論枠組み」）で、論文が確かめていない前提ではなく分野の背景。著者の手が式操作だけの件は IK-0817）。

追加の出所（2610.08751・頭脳 Z2）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3671 — untested_assumptions に式由来の合成主張「In an equation of this paper, $\Pi(n)$ depends on …」2 件、先行研究の説明（Jeffreys priors define invariant measures…）、途中で切れた脚注「（脚注: In the correct expression…」が入る）。

追加の出所（第 25 周 第 6 周の前・PINN TeX 再解析 ce667c63・製品頭脳 A）: uxsim/runs/round25/BRAIN_NOTES_product_A_r6pre.md（4236 — untested_assumptions に背景の一般文「The true nature of matter…」と TeX 残骸「M_max &= 2.062\, &&^… alignat」「P()」（\varepsilon が消えた記号欠落 — IK-0665 の取り込み時のマクロ削除と同根の疑い）が入る。central_question は unknown、paper_goal は「本文が入力に無いため未確認」— paper_skeleton が題名だけで走った波及。author_choices の reason に失敗文 — IK-1013）。

## 発見の観点

開幕素材の前提一覧と、その文の論文内の役割を突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

「記録なし」の既定値を種別の判定に使うと、既定値で埋められた全項目が混ざる。辞書の型 completion-defined-by-proxy。
