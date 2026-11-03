---
id: IK-0810
title: "component_assembly の子振り分けに渡る atomic 主張の本文が claim_qualification の normalized_text（短縮・略語「AP」）で、論文の原文ではない。親の要約は日本語・子の名前と主張は英語で混在する"
status: open
recorded_at: 2026-10-17
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/features/display_projection_design.md"
feature_context:
  realizing: "部品に結びついた主張を子部品へ振り分け、その主張本文を部品の説明・表示の材料にする"
  layers: [pipeline_a, theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
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
    仮説: claim object（または振り分けの材料）の text 欄に atomic claim の normalized_text（照合用の短縮文）が入り、本文の text と
    区別されないまま後段で主張本文として使われる。構造軸 representation = 照合用の正規化文と表示・説明用の本文の欄が分かれていない
    （または取り違えている）。接続軸 meaning = 正規化文が後段で原文として読まれる。処理軸 none / 統制軸 none。確定には
    ClaimObjectBuilder が atomic claim の text / normalized_text を claim object のどの欄に写すかと、振り分けの input_builder を読む。
generalization:
  level: general
  general_form: "照合用に正規化した文と表示用の本文を同じ欄で運ぶため、正規化文（短縮・略語）が後段で本文として扱われる"
pattern: projection-mistaken-for-source
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 W0 が component_assembly の子振り分け（seq 3285・comp_006 の 13 件）の材料を読み、atomic 主張の本文が seq 3162 の claim_qualification の normalized_text と一致することに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md）。同じ材料で inline 式候補 eq_eqcand_inline_tex_b73 の artifact に summary も latex も無いこと（頭脳 T2 も uxsim/runs/radar_loop/BRAIN_NOTES_T2.md seq 2936 で同じ症状）、親の要約は日本語・子の名前は英語の混在も記されている。"
resolution:
  perspective: [pending]
  note: "未解決。claim object に原文の text と照合用の normalized_text を別欄で持たせ、下流の本文は原文を使う案。中身の無い式 artifact は振り分けの対象から外して事実文で残す。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0789, IK-0571]
view_of: []
history: []
---

## 課題

症状: 子振り分けの材料で、atomic 主張の本文が「The power spectrum's (k, mu) dependence is conventionally compressed into Legendre multipoles.」のような
claim_qualification の normalized_text（短縮・略語）になっている。部品の説明・表示の材料が原文でなくなる。親の要約は日本語・子の名前と主張は英語で混在する。
原因（仮説）: 正規化文と本文の欄が分かれていないか、取り違えている。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md（seq 3285・3162）。

追加の出所（2610.08751・頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3283 — available_claims / accepted_claims の text が claim_qualification の normalized_text で、ヒントだけ返した span は原文のまま残り、本文と言い換えが混在する）。

追加の出所（頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3284 — atomic 主張の本文が seq 3140 で頭脳が書いた normalized_text「The nuisance prior contribution can be absorbed into the matrix F.」になっている）。

追加の出所（頭脳 Z2）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3620 — atomic 子の text が LaTeX を剥いだ英語平文（「Theta denotes…」）で、親 span の TeX 表記と揺れる。再判定理由が全件「confidence below 0.8; teacher_review_required」で一律）。

## 発見の観点

振り分けの主張本文を claim_qualification の text / normalized_text と突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

照合用の正規化文を本文の欄で運ぶと、後段が正規化文を原文として扱う。辞書の型 projection-mistaken-for-source。
