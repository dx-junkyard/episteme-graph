---
id: IK-0715
title: "A層 agent の LLM 呼び出しが打ち切りで決定論フォールバックに落ちても run は completed のままで、教材が「解析済み」に見え、フォールバックで出来た部品・グラフであることが画面に出ない"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-22
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
  perspective: [carry_through, canonical_source]
  note: >-
    運転側は周回用 overlay で打ち切りを延ばした（uxsim/sandbox/docker-compose.bridge.yml・真因の proxy 滞留は IK-0726）。
    製品側は、agent が縮退時に既に書いている信号（validation_issues の *_failed / *_deterministic_fallback・
    paper_skeleton の review_notes・component_assembly の diagnostics.fallback_reason と maturity_source・
    skipped_reason の llm_call_failed / repair_failed・stage_outputs.llm_failures）を 1 つの読み時投影
    core/analysis_degradation.py::summarize_run_degradation に集め、教材行のチップ（MaterialOut.analysis_degradation）・
    グラフレビューのバナー（ComponentGraphResponse.analysis_degradation）・G層 material.analysis_llm_failed の点灯が
    すべてこの 1 関数を通るようにした。打ち切りは U層に失敗行を残さないため、従来の llm_failures だけでは点かなかった。
    完了時（単一ステージ実行の完了も）に orchestrator が in-memory の artifact からスナップショット stage_outputs.analysis_degradation を書き
    （新しいステージにはしない）、教材一覧はそれを読む。run の completed 判定は変えない。事実文に数字・例外文を書かない。
  landed_in:
    - "uxsim/sandbox/docker-compose.bridge.yml"
    - "backend/core/analysis_degradation.py"
    - "backend/core/document_pipeline/orchestrator.py"
    - "backend/api/routes/admin.py"
    - "backend/api/routes/theory_components.py"
    - "backend/api/schemas.py"
    - "backend/core/admin_assistant/next_steps.py"
    - "frontend/public/js/admin.js"
    - "frontend/public/js/admin-graph-review.js"
    - "backend/tests/test_analysis_degradation_guardrails.py"
    - "docs/pipeline/overview.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: >-
        採った部品は判定の 1 関数化と、面（教材行・グラフレビュー・G層・完了時フック）がその関数を呼ぶことを固定する
        ガードレール（縮退の rule_id を面のソースに書かせない）。固有の差分: 上流（agent の打ち切り・フォールバック）は
        意図どおりなので変えず、下流の表示に事実を運ぶだけにした（上流の損失ではなく伝達の欠落）。
  verification:
    methods: [guardrail]
    unverified:
      - "docker の実機で打ち切りを起こした run を完了させ、教材行のチップ・グラフレビューのバナー・次にやることが出ること"
      - "完了時スナップショットを持たない既存 run の教材行（軽い信号だけで判定し、thesis・dsl の縮退は unchecked として出ない）"
      - "admin.js / admin-graph-review.js の構文を node で通していない（静的検査のみ）"
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

運転側は打ち切りを周回用 overlay で延ばして再実行した。延ばしても heavy ステージがフォールバックに落ち続けた真因は周回用 proxy の滞留で、IK-0726 で是正した。
製品側は、縮退の受け皿（fallback_reason・rule_id）が既にあり伝達だけが欠けていたので、表示まで運んだ（carry_through）。判定は 1 つの投影に
置き、教材行・グラフレビュー・G層がそれを通す（canonical_source）。run の completed 判定は変えず、成果の縮退を別の事実として示す。

## 一般化

縮退経路を持つ多段処理では、各段の「成功」と成果の「質」が別物になる。全体の完了を段の成功だけで決めると、縮退した成果が正常品と区別できない。
