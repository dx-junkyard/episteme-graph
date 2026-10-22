---
id: IK-0726
title: "周回用 LLM 肩代わり proxy が要求ごとに既定スレッドプールの 1 本を応答待ちで占有し、呼び出し元が去った置き土産の待機で塞がるため、新しい要求が proxy の中で長時間待たされて製品側の打ち切りに落ちる"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "論文レーダーの自律周回で、製品の LLM 呼び出しを proxy → mailbox → 頭脳で肩代わりし、解析パイプラインを本物の応答で最後まで通す"
  layers: [cycle_verification]
classification:
  axes:
    processing: [resource]
    structure: [none]
    connection: [condition]
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
    確認（製品側 log の要求送出時刻と mailbox の created_at = proxy が受けた時刻のずれ、proxy の実装の読み）:
    proxy は要求ごとに asyncio.to_thread で mailbox の応答待ち（最長 45 分）を既定 executor のスレッドに載せていた。
    処理軸 resource = 待機を担うスレッドが呼び出し元の切断後も解放されず、api-server を再作成するたびに応答の来ない待機が
    十数本の既定プールを埋める（後始末の不良）。接続軸 condition = 呼び出し元が去ったという条件（切断・再起動）が
    スレッド境界の向こうの待機へ伝わらない。構造軸 none = mailbox 方式と待機の置き場自体は意図どおりで、
    受け皿を足さずに待ち方を変えれば足りた。統制軸 none = 打ち切り値（予算）の設定は誤りではなく、延ばしても
    proxy の中で待たされる時間が消えないことが真因の切り分けになった。
generalization:
  level: general
  general_form: "要求ごとの待機を有限の共有プールの作業者に載せ、呼び出し元が去っても待機を解かないため、置き土産の待機がプールを埋めて新しい要求が入口で待たされ、上流には遅い応答としか見えない"
pattern: context-lost-across-execution-boundary
discovery:
  perspective: [data_inspection, trace_walk]
  note: "打ち切りを延ばしても heavy ステージ（thesis_reconstruction / dsl_linking / component_assembly）がフォールバックに落ち続けたため、製品の log で 00:54 に出した要求が mailbox では 01:11 に現れることを突き合わせ、proxy の中で十数分待たされていることを見つけた。頭脳 M1/M2 の所見（uxsim/runs/radar_loop/BRAIN_NOTES_M1.md / BRAIN_NOTES_M2.md — 頭脳は dsl に応答済みなのに入力 DSL がフォールバックだった）が手掛かり。"
resolution:
  perspective: [carry_through, order_and_budget]
  note: "待機をスレッドに載せず非同期ポーリングに書き換え（共有プールを使わない）、呼び出し元の切断を検知したら res に {\"error\":\"client_disconnected\"} を置いて頭脳側にも去ったことを伝え、proxy の再起動時は未応答の seq に abandoned_at_proxy_restart を置いて置き土産を片付けた。打ち切りの延長（IK-0715 の運転側対処）だけでは直らない根だった。"
  landed_in:
    - "uxsim/sandbox/claude_proxy.py"
  verification:
    methods: [reproduction_rerun]
    unverified:
      - "uxsim の campaign（学生の並列レーン）での再演"
related: [IK-0715]
view_of: []
history: []
---

## 課題

症状: 自律周回で製品側の agent の LLM 打ち切り（IK-0715）を延ばしても、thesis_reconstruction / dsl_linking /
component_assembly が決定論フォールバックに落ち続けた。製品の log で 00:54 に出した要求が、mailbox では 01:11 に現れた
（mailbox の `created_at` は proxy が要求を受けた時刻）。
原因: `uxsim/sandbox/claude_proxy.py` が要求ごとに `asyncio.to_thread(BOX.ask)` で応答待ち（最長 45 分）を既定 executor
（十数本）のスレッドに載せていた。api-server を再作成すると、去った呼び出し元の要求（応答が来ない ask）がスレッドを
抱えたまま残り、プールが埋まって新しい要求は proxy の中で十数分待たされた。呼び出し元の切断・proxy の再起動という
条件は、スレッドの向こうの待機に伝わらなかった。

出所: 頭脳 M1/M2 の所見（`uxsim/runs/radar_loop/BRAIN_NOTES_M1.md` / `BRAIN_NOTES_M2.md`）と
`docs/features/paper_radar_autonomous_loop_design.md`（AL4: LLM は proxy → mailbox → 頭脳で肩代わり）。

## 発見の観点

実ログと mailbox の時刻を突き合わせ（data_inspection）、要求が製品 → proxy → mailbox のどこで時間を失うかを 1 本の
経路として辿った（trace_walk）。打ち切りを延ばしても症状が消えないことが、予算ではなく待機の後始末が根だという切り分けになった。

## 解決の観点

去った呼び出し元の事実を待機まで運ぶ（carry_through: 切断の検知 → `client_disconnected`、再起動 →
`abandoned_at_proxy_restart`）とともに、待機を共有プールから外して非同期ポーリングにした（order_and_budget: 有限の
作業者を応答待ちで占有しない）。プールの本数を増やす案は、置き土産が溜まる速さを遅らせるだけなので採らなかった。
是正後、thesis / dsl / component_assembly が本物の応答で通り、フォールバックは出なかった。学生の並列レーン（campaign）での
再演はまだ。

## 一般化

要求ごとの長い待機を有限の共有プールに載せる中継（proxy・ゲートウェイ・ワーカー）では、呼び出し元の離脱が待機に
伝わらないと、置き土産がプールを埋めて新しい仕事が入口で待たされ、上流には「遅い」「打ち切られた」としか見えない。
辞書の型は `context-lost-across-execution-boundary`（スレッド境界の向こうへ切断という実行コンテキストが伝わらない）に
最も近い。資源の占有そのもの（処理軸 resource）を主に表す型は辞書に無く、同型が重なれば単一処理の族に型を足すかを検討する。
