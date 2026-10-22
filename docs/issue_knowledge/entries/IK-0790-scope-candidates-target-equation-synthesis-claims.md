---
id: IK-0790
title: "doubt:scope_candidates の対象に式由来の定型主張（「An equation of this paper defines X.」）が選ばれ、出典に原文が無いため候補が常に空で LLM コールだけを消費する"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "D層の台帳の主張ごとに、検証された条件（スコープ）の候補を LLM で抽出する"
  layers: [doubt_d, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
    governance: [budget]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認済み（worker._claim_pending_targets を読んだ）: scope_candidates worker の対象選別が主張の origin（span / claim_object / atomic_rewrite / equation_synthesis）を見ておらず、
    式から決定論で合成した定型主張（equation_synthesis）も台帳の対象として LLM に渡す。合成主張は論文の文を持たず、出典テキストが
    合成文 1 文だけなので、適用条件を抽出する材料が原理的に無い。接続軸 target = 抽出の対象として成立しない主張が対象に入る。
    統制軸 budget = 空振りが確定している呼び出しが日次上限を消費する。構造軸 none（low: 主張の出所を表す origin 列は既にある）。
    処理軸 none。対象 SQL に origin の条件が無いことを読んで確認した。
generalization:
  level: general
  general_form: "抽出の材料を持ちえない種類の項目を対象選別で除かないため、結果が空と決まっている外部呼び出しが予算を消費する"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 T1 が doubt:scope_candidates（seq 2965・2968・2971）の材料を読み、3 件とも対象が式由来の合成主張で出典テキストが合成文 1 文だけ、候補が空になることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md）。"
resolution:
  perspective: [order_and_budget, guardrail_fix]
  note: "原因を確認: worker の対象選択（_claim_pending_targets）が台帳行の target を origin で見ていなかった。対象 SQL に theory_claims_live の origin='equation_synthesis' を外す NOT EXISTS 述語を足し（LIMIT の前に除くので空振りが日次上限を消費しない）、外した事実は件数を出さず run の戻り値 skipped_reasons と info ログに理由 skipped_synthesized_claims として残す。台帳行は触らない（analyzed_at も scope_candidates も空のまま = P4）。input_builder の claim / equation 文脈も合成主張を材料にしない（式に触れる主張が合成文だけなら出典なしで LLM を呼ばない）。is_atomic=False 系は theory_claims に列として永続化されていないため対象選択では判定できず、未対応として残す。"
  landed_in:
    - backend/core/doubt/scope_candidates/worker.py
    - backend/core/doubt/scope_candidates/input_builder.py
    - backend/tests/test_doubt_scope_candidates_targets.py
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB での対象 SQL の実行（scratch DB で NOT EXISTS 述語を流していない）"
      - "実機での日次上限消費の解消（seq 2965・2968・2971 の再現手順の再実行）"
      - "is_atomic=False / split_pending の主張の除外（DB に atomicity が無く判定できない）"
related: [IK-0731, IK-0756]
view_of: []
history: []
---

## 課題

症状: scope_candidates の対象が「An equation of this paper defines $\bar{W}(\lambda)$.」「defines log W_2…」「defines u^FoG」の
式由来の定型主張で、出典テキストが合成文 1 文だけのため 3 件とも候補が空。呼び出しは日次上限を消費する。
原因: 対象選別が主張の origin を見ず、本文を持たない合成主張を対象にしている。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2965・2968・2971）。

追加の出所（頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2961・2964・2967・2970 — 対象がすべて式由来の合成主張で出典は本文 1 行のみ、候補なし）。

追加の出所（頭脳 T2）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 2963・2966・2969 — 合成主張の対象で候補なし。2963 では合成主張「δ^dress depends on P_s, n̄, …」の依存の向きが式の定義と逆、2969 では合成文の意味も怪しく label が 120 字で TeX の途中で切れる）。

隣接の観察（頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3287 — 合成主張 synth_claim_0017「An equation of this paper defines $\tilde p$.」は内容が無く、学習者に出ると情報ゼロの文になる。スコープ候補の対象から外すのと同じく、表示の対象としても扱いを決める必要がある）。

隣接の観察（頭脳 X0・是正前の run（seq 3288〜3312））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3297 — 部品の upper_context に合成主張「An equation of this paper defines $A_AP$.」が並ぶ）。

## 発見の観点

scope_candidates の入力の主張と出典テキストを、主張の出所（式からの合成か本文か）と突き合わせた（data_inspection）。

## 解決の観点

対象選択の SQL で origin='equation_synthesis' を外し（予算を消費する前に除く）、外した事実は理由語だけで残す（order_and_budget）。
回帰テストで SQL の述語・理由語・LLM 未呼び出しを固定（guardrail_fix）。

## 一般化

材料を持ちえない項目を対象から除かないと、空と決まった呼び出しが予算を食う。IK-0731（背景文が対象になる）と同じ対象選別の型
extent-decided-by-proxy-signal の別の現れ。
