---
id: IK-1113
title: "「わたしの地図」で見ている場所（範囲ビューの理論の土台・検証の条件）について尋ねても、学習チャットの画面文脈には「わたしの地図を開いた」の真偽値しか届かず、どのノードを見ているかが AI に渡らない"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/backend/rag-chat.md"
  - "docs/features/assistant_screen_adapter_design.md"
feature_context:
  realizing: "学習者が「わたしの地図」で見ている場所について、AI がその場所の事実で答える"
  layers: [personal_network, screen_adapter_sa, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    app.js getScreenContext は personal-map-home の状態を
    view.personal_map_seen（真偽値）としてしか載せない。範囲ビュー（mode=range）には中心（center）が無く、ノードは主グラフの集約ノード（component_id = theory_op_* /
    stage）で、SA層の element 解決（LEARNING_ELEMENT_TYPES = component / claim / equation / figure・部品文脈の解決）では引けない。接続軸 information。
generalization:
  level: repo_pattern
  general_form: "画面の別パネルで選んでいる対象が、対話の画面文脈の参照語彙に無い型で、真偽値に縮めて渡される"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    製品頭脳 B 4291: st-05（uxsim/runs/c-r25-doubt-r9 seq
    16→17）がわたしの地図の範囲ビュー（theory_op_0001「理論の土台」・記録された条件）を見て尋ねたが、画面文脈は「通常のチャット画面」と分野の地図の場所だけだった。回答自体は出典で正しく答えた。
resolution:
  perspective: [carry_through, canonical_source]
  note: >-
    第 25 周 第 10 波（2026-10-31）: personal-map-home.js が「いまここの周り」を開いている間だけ getScreenContext() で参照
    （personal_map_node_id = 本人の痕跡ノード・personal_map_center_component_id = 点ビューの中心）を返し、app.js が selection に合流。
    route の _screen_personal_map_sources が画面と同じ近傍関係ビューの正本 nearby_for_person_node（本人ゲート・閲覧可否ゲート込み）で
    引き、解決器 _personal_map_node_facts が範囲ビュー（範囲の論文と理論構成・触れた部分・検証の札と記録された条件）か点ビュー（中心と
    前後 1 階層）の事実文にする。kind は登録しない（view の解決器と casual の選択の末尾に足す）。主グラフの集約ノードは DTO のラベルで
    名指すので部品の文脈の解決点は通らない。設計書 SA層 §11.22。
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "画面と AI が同じ近傍関係ビューの正本を読む（解決器で語の表・射影を持たない）。"
  landed_in:
    - "frontend/public/js/personal-map-home.js"
    - "frontend/public/js/app.js"
    - "backend/api/routes/learning.py"
    - "backend/core/assistant_context/resolvers/learning.py"
    - "backend/core/assistant_context/schema.py"
    - "backend/tests/test_round25_wave10_chat.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面でわたしの地図を開いたまま尋ねる往復（第 11 周の再演）"
      - "わたしの地図のパネルと学習チャットを同時に操作できる配置か（パネルがモーダルなら閉じた後の往復では参照が落ちる）"
related: [IK-0907]
view_of: []
history: []
---

## 課題

わたしの地図で見ている場所が AI に渡らない。

## 発見の観点

製品頭脳 B 4291: st-05（uxsim/runs/c-r25-doubt-r9 seq 16→17）がわたしの地図の範囲ビュー（theory_op_0001「理論の土台」・記録された条件）を見て尋ねたが、画面文脈は「通常のチャット画面」と分野の地図の場所だけだった。回答自体は出典で正しく答えた。

## 解決の観点

未着手（第 9 波は見送り）。見立て: personal-map-home が「見ている場所」を公開し（範囲ビューは選択の概念が無いので、ノードを押したときだけ）、selection に main ノードの参照（document_id + 主グラフのノード id）を載せ、SA層に main ノードを解決する種別を足す。element 解決器は面 B の担当で、4-c（topic / visible）は作らない規律があるため、種別の追加は SA層設計書 §11 に追補してから。

## 一般化

画面の別パネルで選んでいる対象が、対話の画面文脈の参照語彙に無い型で、真偽値に縮めて渡される。型 available-but-unwired。

第 10 周（2026-10-31・judge_doubt_r10）: STILL。st-05 がわたしの地図の理論の土台の条件を尋ねると、AI は「その記録の中身はいま手元の画面文脈には届いていない」と答えた（製品頭脳 A 4305）。run: uxsim/runs/c-r25-doubt-r10/20261031T053655Z-69809（seq16・17）。

第 10 波の境界（製品頭脳 B 4314）: テキスト選択だけで「この部品」と問うと部品の画面文脈は届かない。選択テキストは描画後の DOM の文字で
埋め込みの参照（`![[component:…]]`）を含まないため、選択から部品を解決する経路は作っていない（⚓ を開いた往復は第 5 波の IK-0986 で届く）。

