---
id: IK-0755
title: "ComponentRefiner の子部品の local_text が「… within … (#324)」「covers 5 equation step(s)」のような内部の課題番号・操作名・件数の機械文で、依存先の表示名にも混ざり、学習者・教員向け表示に出るおそれ"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/features/graph_paper_layer_design.md"
  - "docs/features/display_projection_design.md"
feature_context:
  realizing: "refiner が分割した子部品に、説明と表示に使う局所テキスト（local_text）を付ける"
  layers: [pipeline_a, hierarchical_explanation, graph_paper_layer]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [none]
    governance: [none]
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
    仮説: refiner は子部品の local_text を決定論の定型文（『… isolated as a reusable equation_system component within …
    (#324)』『Reusable theory unit (derive_consequence) within …; covers 5 equation step(s).』）で埋めており、コード上の
    課題番号・内部の操作名・件数がそのまま表示用の欄に入る。構造軸 representation = 子部品の由来（分割規則・成員数）を
    記録する欄と、人に見せる説明文の欄が分かれていない。処理軸 wording = 表示文として不適切な定型文。表示投影
    （DP）・PL7 の遮断語彙は内部 ID の正規表現と数値キーを対象にし、『(#324)』『covers N equation step(s)』のような
    文中の番号・件数を拾わない可能性がある（未確認）。確定には refiner の local_text の組み立てと、学習者・教員の射影が
    この文を通すかを読む。
generalization:
  level: repo_pattern
  general_form: "決定論で作った由来の記録を表示用の文の欄に入れるため、内部番号・件数の機械文が人向けの表示に出る"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 M1 が contextual_explanation の材料（seq 2116・2119・2122）を読み、分割後の子部品（comp_002__r1 / __r2 ほか）の local_text と依存先の表示名に『(#324)』『covers 5 / 7 equation step(s)』『derive_consequence』の機械文が入っていることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_M1.md）。"
resolution:
  perspective: [pending]
  note: "未解決。分割の由来（規則・成員数）は local_text ではなく別の欄（agent_payload 等）に記録し、local_text は空か人向けの文にする。表示投影の遮断語彙に文中の課題番号・件数の機械文を足すかは、上流で入れない是正を先にしてから判断する（上流の損失を下流で読み替えない）。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0728, IK-0571]
view_of: []
history:
  - date: '2026-10-17'
    field: resolution
    from: refiner 側の是正なし
    to: refiner 側の是正 1 件（子の summary / teaching_takeaway に機械文を入れない）
    reason: "頭脳 W1（seq 3289）で LLM が日本語で書いた summary が「… isolated as a reusable … (#324)」に置き換わっていた。分割の子（__opN / __rN）は親の summary / teaching_takeaway をそのまま保ち、分割の由来の機械文は reason にだけ残す（_child_summary・component_refiner.py）。label の「Verb: 対象」形・依存先の表示名・既存 artifact に残る機械文、表示投影で reason が漏れないかの確認は残るため open のまま"
---

## 課題

症状: refiner が分割した子部品の local_text が『… isolated as a reusable equation_system component within … (#324)』
『Reusable theory unit (derive_consequence) within …; covers 5 equation step(s).』の機械文で、内部の課題番号・操作名・件数が
入る。依存先の表示名にも『(#324)』『covers 7 equation step(s)』が混ざる。二層説明の材料として渡り、学習者・教員向け表示
（PL7 / 表示投影）に出るおそれがある。
原因（仮説）: 分割の由来を記録する欄と人向けの説明文の欄が分かれていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_M1.md（seq 2116・2119・2122）。

追加の出所（2609.35757・TeX 経路・頭脳 T1）: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2956 — 分割部品の local_text に「… isolated as a reusable equation_system component within … (#324)」「Reusable theory unit (derive_consequence) … covers 6 equation step(s)」）。

追加の出所（頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2955 — local_text が「Derive: D/D\tau, M, N: Reusable theory unit (derive_consequence) within … (#324)」で、op 名・課題番号・別節の記号列が説明材料に入る）。

追加の出所（頭脳 T2）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 2954 — op 分割部品の local_text が「Reusable theory unit (evaluate_condition) within …; covers N equation step(s)」と「Unknown specific operation」。seq 2957 — comp_013 の lower に「… within … (#324)」）。

追加の出所（2610.08751・頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3289 — 分割子の summary が「— definition isolated as a reusable definition component within … (#324)」の英語の機械文で、頭脳が日本語で書いた summary を置き換える（出力言語の指定が後処理で失われる）。ラベル「Unknown specific operation: …」「Transform representation: …」が name にそのまま出る）。

追加の出所（頭脳 X1・是正前の run（seq 3289〜3310・2026-10-17 03:00〜03:11））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3295 — 子部品 comp_002__r1/r2/r3・comp_003__op1/op2 の local_text が「… within … (#324)」「covers 4 equation step(s)」。seq 3298 — 子部品名「Evaluate condition: A_{\rm AP}, P_{\rm gg}(k,\mu), m_p」の記号列が生 TeX のまま comp_007/010/011 の 3 部品で同名）。

追加の出所（頭脳 X0・是正前の run（seq 3288〜3312））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3297 — local_text が「X — application: X — application isolated as a reusable application component within X (#324).」と定型文の自己反復で、「covers 10 equation step(s)」も入る。comp_007__op2 の label が記号の羅列。role_in_thesis が英語の定型文）。

追加の出所（頭脳 X2・是正前の run（seq 3290〜3314））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3296 — local_text が「Define: …: Reusable theory unit (define_relation) within …; covers 1 equation step(s).」の機械文、「Unknown specific operation: …」が表示ラベルに出る。seq 3299 — role_in_thesis が「States a result the thesis asserts.」の定型文）。

## 発見の観点

子部品の local_text と依存先の表示名を、表示投影の遮断規則（PL7 / DP）と突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

由来の記録を表示用の欄に入れると、内部番号・件数の機械文が人向けに出る。辞書の型
information-dropped-as-unrepresentable の近傍（表示の欄に由来しか表せない）。PL7 系の遮断が文中の番号を拾わないなら
guardrail-does-not-cover-new-path も疑う。
