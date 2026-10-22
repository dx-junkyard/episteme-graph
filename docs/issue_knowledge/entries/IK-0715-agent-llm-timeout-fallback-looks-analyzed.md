---
id: IK-0715
title: "A層 agent の LLM 呼び出しが打ち切りで決定論フォールバックに落ちても run は completed のままで、教材が「解析済み」に見え、フォールバックで出来た部品・グラフであることが画面に出ない"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/features/paper_radar_autonomous_loop_design.md"
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "取り込んだ論文の解析パイプラインを最後まで通し、出来た部品・グラフを教員がレビューできる状態にする"
  layers: [pipeline_a, cycle_verification]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [completion]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（docker logs の "Agent LLM call exceeded 330.0s" と stage_outputs の fallback_reason・fallback_components と
    n_fallback_* の CORRELATES 鎖・空の thesis_nodes）: agent の LLM 呼び出しは打ち切り（既定 300 秒 / 壁時計 330 秒）を超えると
    決定論フォールバックに落ち、ステージは成功として終わる。run 全体の status はステージの縮退を数えずに completed になる。
    統制軸 completion = 「解析済み」の定義が run の status だけで、成果の縮退を含まない。接続軸 information = 縮退の事実は
    stage_outputs に残るが、教材行・グラフレビューの表示まで運ばれない。処理軸 none = 打ち切りとフォールバック自体は意図どおり動く。
    構造軸 none = 縮退を表す受け皿（fallback_reason）は既にある。
generalization:
  level: general
  general_form: "下位の処理が縮退した結果で成功し、全体の完了状態は縮退を数えないため、利用者には正常な成果と区別できない"
pattern: failure-reported-as-success
discovery:
  perspective: [data_inspection, symptom_report]
  note: "論文レーダーの自律周回で LLM を頭脳（Opus）が肩代わりし 1 応答に数分かかった結果、多数の呼び出しが打ち切られた。頭脳の所見と docker logs・stage_outputs を読み、フォールバック部品が並ぶ教材が解析済みとして見えることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_L2.md）。"
resolution:
  perspective: [pending, order_and_budget]
  note: "部分解決。運転側は周回用 overlay uxsim/sandbox/docker-compose.bridge.yml に RADAR_LOOP_AGENT_LLM_TIMEOUT_SECONDS / RADAR_LOOP_AGENT_LLM_WALL_TIMEOUT_SECONDS を足し、1500 / 1800 秒で再実行した（本番の既定は不変）。ただし打ち切りの延長だけでは直らず、延ばしてもフォールバックに落ち続けた真因は周回用 proxy 側の滞留（去った呼び出し元の待機がスレッドプールを埋めて新しい要求が proxy の中で待たされた — IK-0726 で是正）だった。製品側の「フォールバックで出来た部品・グラフを教材行とグラフレビューに事実として示す（run の完了とは別に縮退を表示する）」は依然 open（未着手）。"
  landed_in:
    - "uxsim/sandbox/docker-compose.bridge.yml"
related: [IK-0699, IK-0710, IK-0726]
view_of: []
history: []
---

## 課題

症状: 自律周回で取り込んだ教材が教材行では「解析済み」に見えるが、中身は thesis_reconstruction / dsl_linking /
component_assembly の決定論フォールバック（フォールバック部品・CORRELATES の鎖・空の中心命題）で、グラフレビューにも
その事実が出ない。
原因: `src/episteme_graph/agents/llm_json_client.py` の打ち切り（`AGENT_LLM_TIMEOUT_SECONDS` 300 / `AGENT_LLM_WALL_TIMEOUT_SECONDS` 330）を
超えた呼び出しはフォールバックに落ち、ステージは成功として終わる。run の status は縮退を数えずに completed になり、
`stage_outputs` の `fallback_reason` は画面まで運ばれない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_L2.md（頭脳 L2 の所見）・docker logs の打ち切りの記録。

追加の出所（2610.08751・頭脳 Z0/Z1・第 2 波 deploy 後の run）: uxsim/runs/radar_loop/BRAIN_NOTES_Z1.md（seq 3616 — eq_tex_b96・eq_tex_b102（中心式の TCM 再パラメータ化）・eq_tex_b78 が「Equation semantics could not be inferred / can_support_claim=false」のまま component_assembly の材料に入る）、uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3612・3627・3636 — dsl_linking・振り分けで同じ式が中身ゼロのまま unassigned になり、中心の式が部品に入らない。seq 3651 — 二層説明の local_text の頭と derived_from の要素名に失敗の文言「Equation semantics could not be inferred」そのものが流れる）。打ち切り後の縮退結果に印が無く、後段が通常の材料として使う。原因は頭脳のレーンの空白で応答が打ち切りを越えたこと（運転側の記録は IK-0825）。

## 発見の観点

頭脳の所見から遡って実ログと stage_outputs を読み（data_inspection）、「解析済みに見えるのに中身が空」という症状（symptom_report）と突き合わせた。

## 解決の観点

運転側は打ち切りを周回用 overlay で延ばして再実行した（order_and_budget）。ただし延長だけでは直らなかった。延ばしても heavy ステージがフォールバックに落ち続けた真因は、周回用 proxy が去った呼び出し元の待機でスレッドプールを塞ぎ、新しい要求が proxy の中で十数分待たされていたことで、IK-0726 で proxy 側を是正した。製品側は未解決で、run の完了状態とは別に
「フォールバックで出来た成果」を教材行・グラフレビューの事実文として出す必要がある（既にある受け皿 fallback_reason を表示まで運ぶ）。

## 一般化

縮退経路を持つ多段処理では、各段の「成功」と成果の「質」が別物になる。全体の完了を段の成功だけで決めると、縮退した成果が正常品と区別できない。
