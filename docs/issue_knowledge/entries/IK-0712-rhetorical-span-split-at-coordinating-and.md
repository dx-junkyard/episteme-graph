---
id: IK-0712
title: "役割判定の span が文中の等位 'and' で割れ、前の span が未完で終わり次の span が ' and demonstrates …' で始まって、主張採否に半文が渡る"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "段落を論理役割ごとの span に分け、span 単位で主張の採否と atomic 化を判定する"
  layers: [pipeline_a]
classification:
  axes:
    processing: [unknown]
    structure: [decomposition]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: low
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: rhetorical_role の span 分割（LLM が返す span 境界と、それをサーバ側で本文に合わせる resolve_span_offsets）が、
    1 文の中の等位接続 'and' を節の境界として span を切っている。構造軸 decomposition = 分割の単位（文か節か）が定義されて
    おらず、述語を共有する等位節が別 span になる。処理軸 unknown = 境界を LLM が節で返しているのか、オフセット解決が
    文の途中に寄せているのかを確かめていない。確定には、当該ブロックの rhetorical_role の LLM 生出力の span 境界と、
    resolve_span_offsets 後の境界を比べる。接続軸 none = 後段は受け取った span をそのまま使っており、受け渡しで落ちる値は無い。
    統制軸 none。
generalization:
  level: repo_pattern
  general_form: "分割の単位が文か節かを定義しないまま境界を決め、1 つの命題を成す文が接続詞の位置で別単位に割れて後段に半文が渡る"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回の頭脳メモ uxsim/runs/radar_loop/BRAIN_NOTES_I1.md（seq 1693・1714）が、claim_qualification の入力で Current Span が '… at linear order' で終わり Next Span が 'and the stochastic fields …' で始まる例、Previous Span が '… of Sec. 2.4' で Current Span が ' and demonstrates …'（先頭空白+接続詞）で始まる例を見た。"
resolution:
  perspective: [pending]
  note: "未解決。案は、span 境界を文境界に限る規約を rhetorical_role の出力契約に足す（節での分割は atomic rewrite の責務 = ClaimQualificationAgent に残す）か、先頭が接続詞・空白で始まる span を直前の span へ決定論で併合する。どちらも LLM 呼び出しの回数は変えない。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0713, IK-0714]
view_of: []
history: []
---

## 課題

症状: claim_qualification の入力の span が文の途中で切れている（'Here $u_z…$ at linear order' | 'and the stochastic fields are uncorrelated …'、
'This appendix derives the trispectrum covariance model of Sec. 2.4' | ' and demonstrates that the terms omitted … are small.'）。
後ろ半分は主語を持たず、前半は述語が途中で終わる。観察の出所は uxsim/runs/radar_loop/BRAIN_NOTES_I1.md と uxsim/runs/mailbox/req-01693.json・req-01714.json。

原因（仮説）: 役割判定の span 分割が等位の 'and' を境界にしている。LLM の境界かオフセット解決かは未確認。

## 発見の観点

主張採否の入力を実データで読んだ（data_inspection）。

## 解決の観点

未解決。span の単位を「文」と定義して節の分割を atomic rewrite に寄せるか、接続詞始まりの span を直前に併合するかを、
LLM 生出力とオフセット解決後の境界を比べてから選ぶ。

## 一般化

区切りの単位を定義しないと、命題の途中で単位が割れる。確かめ方は、等位節を含む文を通し、span が文境界でだけ切れることを見る。辞書の型 unit-of-work-undefined。
