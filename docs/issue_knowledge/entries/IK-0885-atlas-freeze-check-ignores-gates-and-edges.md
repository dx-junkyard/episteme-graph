---
id: IK-0885
title: "分野の地図の公開前チェックが、凍結を止める条件（採用済み未反映の候補・辺）も関係（辺）の変更も見ずに「現在の公開版と同じです」と言い、押した後に初めて止まる"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_binding_lifecycle_design.md §4.4"
  - "docs/features/atlas_relation_edges_design.md"
feature_context:
  realizing: "教員が次版の下書きを公開する前に、何が変わり、公開できるかを 1 画面で確かめる"
  layers: [field_atlas_s, relation_edges_re, category_gaps, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [information]
    governance: [ordering]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 aggregation: 「公開できるか」の判定が 2 箇所にある。GET .../atlas/freeze-impact（atlas_lifecycle.
    compute_freeze_impact + 対応表）は draft と凍結版の node 差分と影響コースだけを計算し、POST .../freeze が
    凍結時に _pending_gap_candidates / _pending_edge_candidates を初めて評価する（routes/atlas.py）。
    接続軸 information: freeze-impact は edges を材料に持たない — FACT_SAME_NODE_SET「下書きの領域・概念は、現在の
    公開版と同じです。」は node 集合の一致だけで出るので、関係（辺）を 1 本足した下書きでも「同じ」と言い、足した
    関係が変更として列挙されない。統制軸 ordering: 止める条件の評価が確認の後（押した後）に来る。
    処理軸 none。確認: run 20261022T220051Z-74929 seq 17（辺を下書きに追加）→ seq 18（チェックはゼロ・「同じです」）→
    seq 19（409）。
generalization:
  level: repo_pattern
  general_form: "確定の前に見せる確認画面が、確定の処理が実際に使う判定材料の一部しか計算せず、確認で通った操作が確定の時点で別の理由で止まる・変わったものを『変わっていない』と言う"
pattern: duplicate-canonical-sources
discovery:
  perspective: [reproduction, trace_walk]
  note: "教員ペルソナが関係を 1 本追加した下書きの公開前チェックを読み「足した関係のことはどこにも書いていない」と書いた直後、公開で止められた。freeze-impact と freeze の判定材料を突き合わせた。 run: uxsim/runs/c-astro-r25-atlas-r2/20261022T220051Z-74929。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    凍結の可否を 1 つにした。routes/atlas.py _freeze_readiness（採用済み未反映の候補・関係の照会 = fail-open）を freeze と freeze-impact の両方が呼び、可否と事実文は atlas_lifecycle.freeze_readiness が決める。freeze-impact は readiness{ready, pending_labels, pending_edges, facts} と、下書きと現行版の関係の増減 added_edges / removed_edges（「A — B（依存）」のラベル列挙）を返し、FACT_SAME_NODE_SET は領域・概念・関係がすべて同じときだけ出す（関係だけ変わるときは FACT_SAME_NODES_EDGES_CHANGED）。admin.js は ready:false なら確認の段階で［公開できません］（押せない）と［止めている候補を見る］を出し、押してから 409 で止めない。
  landed_in:
    - "backend/core/atlas_lifecycle.py"
    - "backend/api/routes/atlas.py"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_atlas_review_round25_r2.py"
    - "backend/tests/test_atlas_gaps_guardrails.py"
    - "backend/tests/test_atlas_edges_api.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "確認画面と確定処理の 2 面が同じ判定関数を通る。"
  verification:
    methods: [guardrail]
    unverified:
      - "ブラウザでのモーダルの見え方は未確認"
related: [IK-0884, IK-0851, IK-0886]
view_of: []
history: []
---

## 課題

症状: 公開前チェックは「検証エラー 0・警告 0・未確認の修正報告 0・削除される概念・外れるコースはありません・下書きの
領域・概念は、現在の公開版と同じです」と出る。下書きには関係を 1 本足してあるが、どこにも書かれない。[公開する]を押すと
採用済み未反映の候補で止まる。

原因: 公開前チェックの材料（node 差分・影響コース）と凍結の判定材料（採用済み未反映の候補・辺）が別の関数にあり、
チェックは辺も pending も見ない。

## 発見の観点

再演（reproduction）でチェックと凍結の結果が食い違い、2 つのエンドポイントの材料を突き合わせた（trace_walk）。

## 解決の観点

第 2 波（2026-10-23）で是正。可否の判定を 1 つにし、確認画面と確定処理が同じものを読む。変更の列挙に関係を含める。

## 一般化

「確認 → 確定」の 2 段で、確認だけが軽い計算を持つ形は、凍結・公開・取り込みの確定で再発する。確かめ方: 確定を止める
条件をそれぞれ作り、確認画面がその条件を先に言うこと、変更の種類（領域・概念・関係）を全部列挙することを見る。
