---
id: IK-0816
title: "doubt:scope_candidates が脚注の親主張（「（脚注: …）」の括弧書きのまま）と、その atomic 子主張の両方を別々に対象にし、同じ内容を 2 回 LLM に出す。子は論文が採らなかった手で、検証スコープの対象にならない"
status: open
recorded_at: 2026-10-17
resolved_at: null
sources:
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "D層の台帳の主張ごとに、検証された条件（スコープ）の候補を LLM で抽出する"
  layers: [doubt_d, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [decomposition]
    connection: [target]
    governance: [budget]
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
    仮説: Phase 1 で claim object の全件（親 claim_object / atomic 子 / 吸収されない span）が theory_claims の行になり、台帳の
    バックフィルがその全行に付くため、scope_candidates の対象選別は親と子を区別せず両方を回す。脚注も主張と同じ母集合にいる
    （IK-0713）。構造軸 decomposition = 検証スコープの対象の単位（親か子か）が定義されていない。接続軸 target = 注記や「本来こうすべき」
    という論文が採らなかった手まで対象に入る。統制軸 budget = 同じ内容への重複呼び出しが日次上限を消費する。処理軸 none。IK-0790
    （合成主張）・IK-0731（背景文）と同じ対象選別の族だが、親子の重複と注記という別の原因として分けた。確定には worker の対象 SQL が
    parent_claim_id・origin を見るかを読む。
generalization:
  level: general
  general_form: "親と分割後の子を同じ母集合に置いたまま 1 件ずつ処理するため、同じ内容に外部呼び出しが重複し、処理の対象にならない子も回る"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 X1 が doubt:scope_candidates（2610.08751・是正前の run・seq 3304・3310）を読み、seq 3304 の対象が脚注の文全体（括弧書きのまま）で label が 100 字で「to properl」と途中で切れ、seq 3310 がその atomic 子（「正しい式は IR 再和スペクトル」= 論文が採らなかった手）であることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md）。"
resolution:
  perspective: [pending]
  note: "未解決。対象を親か子のどちらかに揃え（atomic 子があれば子、親は外す）、脚注・注記と「採らなかった手」の主張は対象から外して coverage に理由付きで残す案。"
  landed_in:
    - "docs/features/doubt_layer_issues.md"
related: [IK-0790, IK-0731, IK-0756, IK-0713]
view_of: []
history: []
---

## 課題

症状: scope_candidates が脚注の親主張（「（脚注: …）」の括弧書きのまま）と、その atomic 子主張（「正しい式は IR 再和スペクトル」）を別々に対象にし、同じ内容を 2 回 LLM に出す。
親は注記で、子は論文が採らなかった手なので、どちらも検証スコープの対象として不適。親の label は 100 字で「to properl」と途中で切れる。
原因（仮説）: 対象選別が親子と主張の種類（注記・採らなかった手）を区別していない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3304・3310。是正前の run）。

追加の出所（頭脳 X0・是正前の run（seq 3288〜3312））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3309・3312 — 定義型の主張（「one-loop 項は NLO の寄与」）も対象に入る。定義は検証される命題ではなく、対象選別が主張の種類を見ていない同じ症状）。

追加の出所（頭脳 X2・是正前の run（seq 3290〜3314））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3308 — 「〜を含む」型の定義・列挙の主張が対象に入る。seq 3308・3311 — 同じ内容の主張が span 原文版と atomic 版で 2 件別々に台帳対象。seq 3311 — label が「…including the linear bias $b_1」と数式の開き $ の内側で切れる）。

追加の出所（第 2 波 deploy 後の run・頭脳 Z0/Z1）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3678 — 名詞句の断片の atomic 子が対象になる）、uxsim/runs/radar_loop/BRAIN_NOTES_Z1.md（seq 3679 — label が「$P_{\rm sn} \equiv \bar{n}」のように inline 数式の途中で切れ $ が閉じない）。

## 発見の観点

scope_candidates の対象の主張を、親子の関係と主張の種類に突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

親と子を同じ母集合に置くと、同じ内容に呼び出しが重複する。辞書の型 unit-of-work-undefined。
