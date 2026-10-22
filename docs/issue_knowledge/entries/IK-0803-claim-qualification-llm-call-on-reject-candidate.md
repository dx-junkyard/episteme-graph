---
id: IK-0803
title: "claim_qualification が役割判定で is_reject_candidate=True になった span（書式文字列「APS/123-QED」等）にも LLM を 1 回使い、決定論で落とせる span で呼び出しを消費する"
status: resolved
recorded_at: 2026-10-17
resolved_at: 2026-10-17
sources:
  - "docs/pipeline/agents.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "役割判定の結果を受けて、主張候補の span ごとに採否・区分を LLM で判定する"
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [ordering, budget]
  axis_confidence:
    processing: medium
    structure: medium
    connection: low
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認済み（2026-10-17）: input_builder._should_include が is_reject_candidate の span を既定（include_reject_candidates=True）で
    LLM の母集合に入れていた。あわせて rhetorical_role は prior_work の span に reject 印を付ける経路があった（seq 3028 の
    ["prior_work", "citation_context"]）。以下は起票時の仮説: claim_qualification の母集合が役割判定の is_reject_candidate を見ずに全 span を LLM に回しており、前付けの書式文字列のように
    決定論で reject と決まる span も 1 コールずつ消費する。統制軸 ordering = 決定論の絞り込みが LLM 呼び出しの後ろ（または不在）にある。
    統制軸 budget = 上限なしの既定（E-2）と組み合わさって呼び出し回数が母集合に比例して増える。接続軸 information（low）= 前段の
    reject 候補の印が後段の選別に使われていない。処理軸 none / 構造軸 none。確定には claim_qualification の input_builder が span を
    選ぶ条件と、is_reject_candidate の span を LLM に渡すかを読む。prior_work の span を reject 候補として扱うかどうか（先行研究の
    主張は採らない規律）も同じ印に依存するので、印の意味（確定 reject か候補か）と合わせて確かめる。
generalization:
  level: general
  general_form: "前段が決定論で付けた除外の印を後段の選別に使わないため、結果が決まっている項目にも外部呼び出しを使う"
pattern: gate-position-wrong
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 U1 が claim_qualification（2610.08751・seq 3115）の材料を読み、is_reject_candidate=True の書式文字列「APS/123-QED」1 個に LLM 1 コールが使われていることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_U1.md）。"
resolution:
  perspective: [order_and_budget, guardrail_fix]
  note: "reject 候補かつ役割が主張になり得ない語彙（section_meta / meta_discourse / citation_context = schema.NON_CLAIMABLE_ROLE_LABELS）だけの span は LLM の母集合と上限配分の前で外し、status=rejected / qualification_reason=reject_candidate_from_rhetorical_role / confidence は役割判定の値で決定論記録する。外した span は summary_stats.skipped_reject_candidates と coverage details.skipped_reject_candidates に理由付きで残す（P4）。prior_work は reject 候補にしない: rhetorical_role の span 解析が prior_work の reject 印を外して reason に注記し、claim_qualification は prior_work の span を採否判定の母集合に残す（旧 artifact の印が残っていても決定論 reject の対象にしない）。figure / table narration・background_general は主張を含み得るので LLM に回す。"
  landed_in:
    - "src/episteme_graph/agents/claim_qualification/agent.py"
    - "src/episteme_graph/agents/claim_qualification/input_builder.py"
    - "src/episteme_graph/agents/claim_qualification/schema.py"
    - "src/episteme_graph/agents/rhetorical_role/schema.py"
    - "src/episteme_graph/agents/rhetorical_role/repair.py"
    - "src/tests/agents/claim_qualification/test_deterministic_reject.py"
    - "src/tests/agents/claim_qualification/test_reject_flag_flow_x1.py"
    - "docs/pipeline/agents.md"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "実機での再現手順（seq 3115 の論文の再解析）を再実行して LLM 呼び出しが消えたことの確認"
      - "prior_work の印を外したことで採否判定の母集合に入る span が増え、LLM 呼び出しがかえって増える論文の実測"
related: [IK-0696, IK-0699, IK-0790]
view_of: []
history:
  - date: '2026-10-17'
    field: resolution.landed_in
    from: test_deterministic_reject.py まで
    to: test_reject_flag_flow_x1.py を追加
    reason: "X1 seq 3316 → 3409 で reject 印が後段で消えたように見えた件を確認。再発ではない: 消えたのは role_labels に prior_work を含む span_003 だけで、IK-0803 の規律（prior_work は reject 候補にしない）どおり。run 40ecf8df の artifact でも span_001 / 002 は true・span_003 は false + 注記。背景（background_general）を含む reject 候補は主張を含み得るので LLM に回るのも設計どおり。印が repair → artifact → input_builder を保たれて通ることを回帰テストで固定"
---

## 課題

症状: tex_b1 の「APS/123-QED」（is_reject_candidate=True の書式文字列）にも claim_qualification が LLM を 1 回使い、rejected meta を返した。
前段で reject と分かっている span で呼び出しを消費する。
原因（仮説）: 主張採否の母集合が前段の除外の印を見ていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_U1.md（seq 3115）。

追加の出所（2610.08751・頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3199 — reject 候補の図説明 1 文にも LLM 1 コール）。

追加の出所（頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3146 — 内容のある一般背景、seq 3215 — 結論冒頭の研究課題文が is_reject_candidate / section_meta になっている。前段の reject 候補の判定が厳しすぎる側の症状で、印を LLM 前の決定論の絞り込みに使うなら判定の精度も確かめる必要がある。seq 3164 — 引用指示文も reject 候補で LLM 1 コール）。

追加の出所（頭脳 X1・全是正入りの再解析 run 40ecf8df（seq 3313 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3316 → 3409 — 役割判定で is_reject_candidate=true を返した tex_b25 の span が、claim_qualification の入力では is_claim_candidate=False / is_reject_candidate=False で届き、reject の印が後段で落ちる。reject 候補でも claim_qualification に回る。詳細は fixRejectFlow の起票）。

追加の出所（頭脳 X0・全是正入りの再解析 run 40ecf8df（seq 3315 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3315 — 要旨の Previous Context が「APS/123-QED」で、REVTeX のプリアンブル残骸が文脈として渡る）。

追加の出所（頭脳 X1・全是正入りの再解析 run 40ecf8df）: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3316 → 3409 — 役割判定で reject=true だった span_003 が採否判定の入力で false。prior_work による規律どおりの解除で、経路の欠落ではないことを確認）。

追加の出所（頭脳 X2・是正前の run（seq 3290〜3314））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3314 — REVTeX の「APS/123-QED」が本文ブロック tex_b1 になり、reject の判定に LLM 1 回）。

## 発見の観点

claim_qualification の入力の span と、役割判定が付けた is_reject_candidate を突き合わせた（data_inspection）。

## 解決の観点

決定論の絞り込みを LLM 呼び出しの前（母集合・上限配分の前）に置いた（order_and_budget）。prior_work は reject 候補にしない規律を役割判定側の印の正規化と採否判定側の母集合の両方で固定し、回帰テストを置いた（guardrail_fix）。

解消の確認（reproduction_rerun・頭脳 Y0・再解析 run 40ecf8df）: uxsim/runs/radar_loop/BRAIN_NOTES_Y0.md（seq 3420・3468 — section_meta の tex_b34 は claim_qualification に来ておらず、決定論 reject が効いている。figure_narration・background_general の reject 候補（seq 3408・3429・3468、uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md seq 3488）は設計どおり LLM へ回る）。観察（uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md seq 3479）: 中心命題の最も明快な言い直し「Mpc で十分」が節の繋ぎ文に入り、役割判定で meta_discourse の reject 候補になっていた。決定論 reject の語彙に meta_discourse がある以上、こうした再掲の主張が LLM を通らず落ちる危険がある。

## 一般化

前段の除外の印を後段の選別に使わないと、結果の決まった呼び出しが予算を食う。辞書の型 gate-position-wrong（コール節約の族、IK-0696・IK-0790 と同族）。
