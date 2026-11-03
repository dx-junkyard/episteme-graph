---
id: IK-0795
title: "論文レーダー自律周回の driver が周番号を記録済みの周の件数で決めるため、再開した run が既存の周番号を再利用し、残っていた古い判定ファイルを即採用して同じ論文を二重に取り込んだ"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "検証用の自律周回で、周ごとに浅い段・深い段の判定ファイルを書き、判定に従って候補を取り込み次の周へ進む"
  layers: [cycle_verification]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [version]
    governance: [resume]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（uxsim/sandbox/radar_loop.py の run）: 周番号を ledger の iterations の件数で決めており、attach で深い段だけ記録した周や
    kill された周は iterations に入らない。構造軸 representation = 周の識別子が周の実体（ledger の tried / chain、scan-* / deep-* の
    ファイル）ではなく記録件数から導かれ、同じ番号が別の周に付く。接続軸 version = 前の周の判定ファイル（scan-07.decision.json）が
    今の周のものとして読まれ、書いた時刻との前後を見ていなかった。統制軸 resume = 停止・再開のあとの冪等性が無く、再開が前の判定を
    再実行した。処理軸 none。
generalization:
  level: general
  general_form: "識別子を記録済みの件数から導くため、記録から漏れた実行があると再開時に同じ識別子が別の実行に付き、その識別子に紐づく古い成果物が今の実行のものとして使われる"
pattern: id-not-stable-across-versions
discovery:
  perspective: [symptom_report]
  note: "2026-10-16 23:02、再開した run が「iteration 7」で始まり、残っていた scan-07.decision.json（ingest=2609.35757）を即採用して同じ論文を新しい document として取り込んだ（arXiv 呼び出し 1 回と解析の LLM コールが無駄になった。重複した document は削除済み）。製品側に取り込み済み ID の弁が無いこと（同じ arXiv 論文を別 document として受理する）は別課題 IK-0796（fixDupIngest が起票）。"
resolution:
  perspective: [representation_change, state_transition]
  note: "_next_iteration_number() が ledger の iterations / tried / chain と scan-* / deep-* のファイル名の最大値から次の番号を決める。明示した --seed / --distance が ledger の current_* に勝つ。wait_decision(newer_than=) で、scan / deep を書いた時刻より古い判定ファイルは使わない。"
  landed_in:
    - uxsim/sandbox/radar_loop.py
  verification:
    methods: [reproduction_rerun]
    unverified:
      - "driver の単体テスト（driver にテストは無い）"
      - "attach と kill が重なった再開の組み合わせ"
related: [IK-0796]
view_of: []
history: []
---

## 課題

症状: 再開した自律周回の run が既に使った「iteration 7」で始まり、残っていた scan-07.decision.json（ingest=2609.35757）を
即採用して同じ論文を新しい document に二重に取り込んだ。arXiv 呼び出しと解析の LLM コールが無駄になった（重複 document は削除済み）。
原因: 周番号を ledger の iterations の件数で決めており、attach で深い段だけ記録した周や kill された周が数に入らない。判定ファイルの
読み込みも、書いた時刻より古いものを除いていなかった。明示の --seed / --distance より ledger の current_* が優先されていた。

製品側の弁（同じ arXiv ID の論文を別 document として受理しない）が無いことは別課題 IK-0796 で、本課題はハーネス側の再開の不良。

## 発見の観点

再開直後の取り込みを loop.log と ledger・判定ファイルの番号に突き合わせた（symptom_report）。

## 解決の観点

周番号を周の実体（ledger の全区画とファイル名）の最大値から導き（representation_change）、判定ファイルは今の周で書いた時刻より
新しいものだけを採る（state_transition）。実機で iteration 8 が正しい番号・起点で始まったことを loop.log で確かめた。

## 一般化

件数から識別子を作ると、記録から漏れた実行のぶん番号が巻き戻り、古い成果物が今のものとして使われる。辞書の型
id-not-stable-across-versions。
