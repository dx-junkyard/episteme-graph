---
id: IK-0716
title: "取り込んでから確かめる 1 段の検証周回は 1 本 10 時間を超えて UX の反復が回らず、審判を先に挟む浅い段 → 深い段の 2 段構成で取り込みを 1 本に絞れた（原理候補の記録）"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/features/paper_radar_autonomous_loop_design.md"
  - "docs/architecture/improvement_cycle.md"
feature_context:
  realizing: "検証の周回で、候補を取り込んで解析し、その結果から画面の欠けを見つけて是正の波を回す"
  layers: [cycle_design, cycle_verification]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [ordering, budget]
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
    確認（設計書 §3・§5 の実測: 1 周目の「近い」候補は起点の主題と無関係で、取り込んだ論文 1 本の解析に 10 時間を超えた）:
    v1 の周回は高コストの段（取り込み・解析）を先に走らせ、低コストの判定（要旨と比較を読んだ審判の関心）を後に置いた。
    「関係が無い」という知見は低コストの段で数分で得られるのに、高コストの段を通した後でしか得られなかった。統制軸 ordering =
    判定と高コストの処理の順序。統制軸 budget = 1 本あたりの解析時間が周回の予算を超えた。処理・構造・接続の軸 none = 各段の処理と
    受け渡しは正しく、問題は段の並べ方だけ。
generalization:
  level: general
  general_form: "高コストの処理を走らせてからでないと低コストで得られる判定が出ない順序になっていて、捨てる候補にも高コストが払われる"
pattern: gate-position-wrong
discovery:
  perspective: [data_inspection, external_constraint]
  note: "1 周目の実測（解析 1 本 10 時間超・候補が主題と無関係）と、頭脳が LLM を肩代わりする運転の速度制約から逆算した（出所: uxsim/runs/radar_loop/ の頭脳所見と周回ログ）。"
resolution:
  perspective: [pending, order_and_budget]
  note: >-
    構成は v2 で変更済み（浅い段 = 検索・比較で審判の判定を先に取り、関心を持てる候補だけ深い段 = 取り込み・解析へ送る。設計書 §2〜§3）。
    原理候補「高コストの確定の前に低コストの判定を挟む（persona-gated two-tier）」は principles.md へは未昇格。成立条件
    （consulted の事例が別々の型から 2 件以上・適用後に同型の見逃しなし）は索引が導く。手法の記録として open に置く。
  landed_in:
    - "docs/features/paper_radar_autonomous_loop_design.md"
related: [IK-0699, IK-0715]
view_of: []
history: []
---

## 課題

症状: 依頼の素朴な形（近い候補を順に取り込み、パイプラインを通してから類似性を確かめる）で周回したところ、1 本の解析に
10 時間を超え、しかも 1 周目の候補は起点の主題と無関係だった。UX の反復（画面の欠けを見つけて是正する波）が回らなかった。
原因: 低コストで得られる判定（要旨と比較を読んだ審判の関心）を、高コストの段（取り込み・解析）の後に置いた順序。

出所: uxsim/runs/radar_loop/ の頭脳所見と周回ログ（設計書 §5 の実測の記録）。

## 発見の観点

1 周目の実測（data_inspection）と、頭脳が製品 LLM を 1 応答で肩代わりする運転の速度制約（external_constraint）から逆算した。

## 解決の観点

審判（分野ペルソナ）の判定を浅い段に移し、関心を持てる候補だけを深い段へ送る（order_and_budget）。取り込みは 1 本に絞れ、
浅い段で関心が持てない理由がそのまま「画面に欠けている要素」の仮説になって、解析を待たずに是正の波を回せた。

原理候補: **高コストの確定の前に低コストの判定を挟む（persona-gated two-tier）**。守る条件は取り込みの弁を明示判断に置く
（PD1）のと同じ形。非適用の候補: 低コストの判定が高コストの結果を代替できない（解析してみないと分からない欠陥を探す）場合。
principles.md への昇格は未了で、成立条件は索引が導く。

## 一般化

検証・選別のパイプラインでは、捨てる候補が多いほど「安い判定を先に」の順序が効く。判定を後に置くと、捨てる候補にも高コストが払われる。
