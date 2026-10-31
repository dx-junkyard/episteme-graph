---
id: IK-1017
title: "学習者の地図の L2 の代表選出が、コースの論文の配置を手がかりに使わず、トピックに地図の対応が無いコースでは骨格の並び順の先頭 20 件に戻る — 2026.3 で確定した関係「惑星形成 — 星形成」は描かれず名前でも言われないまま、事実文は「いま学んでいる場所と、そのつながりを優先しています」と言う"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/field_atlas_overlay_spec.md"
  - "docs/features/knowledge_landscape_design.md"
feature_context:
  realizing: "受講したばかりの学生が、教員の凍結で加わった関係と自分のコースの論文がある場所を、分野の地図の L2（コースの概念マップ）で読む"
  layers: [field_atlas_s, knowledge_landscape, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information, condition]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: routes/atlas_view.py select_l2_concept_ids の priority は焦点・いまここ・course_topics の atlas_node_id・足跡だけで、
    同じ route が学習者向けに返しているコースの論文の配置（landscape_placements・コース sources の live 配置）を渡していない。受講直後で足跡が無く、
    トピックに atlas_node_id の無いコース（1bad7d9f）では priority が空になり、選出は骨格の並び順の先頭 20 件（宇宙論 6・銀河 5・星間物質 5・
    高エネルギーの先頭 4）に戻る — 第 5 波以前と同じ集合。星形成にはコースの論文（Cep B）が配置されているのに 1 hop の惑星形成が選ばれず、確定した
    関係「惑星形成 — 星形成」は実線にならない。接続軸 condition: ATLAS_L2_OMITTED_NOTE は priority の有無で文を変えず「いま学んでいる場所と、
    そのつながりを優先しています」と言う（優先が働かなかった状態でも同じ文）。描かなかった関係を名前で挙げる面も無い。処理軸 none（関数は
    宣言どおりに動く）、構造軸 none（代表選出をサーバ 1 箇所に寄せた構造は正しい）、統制軸 none。
generalization:
  level: repo_pattern
  general_form: "表示の優先順位を決める関数が、同じ応答の中で既に手元にある最も具体的な手がかりを入力に取らず、手がかりが空のときの既定順に黙って戻りながら、優先が働いたかのような説明文を出す"
pattern: available-but-unwired
discovery:
  perspective: [trace_walk, reproduction]
  note: "凍結 2026.3 の後に学生 st-03 が L2 を開き、実線は『ブラックホール — 超大質量ブラックホール』の 1 本だけだった。levels['2'] の描く概念（digest と観察の『描かれている』列）が骨格の先頭 20 件と完全に一致することと、select_l2_concept_ids の priority の組み立て（atlas_node_id と足跡だけ）を突き合わせ、確定した 2 本目の関係が惑星形成の未選出で落ちたことを見た。run: uxsim/runs/c-astro-r25-atlas-r6/20261027T123201Z-9456 seq 6。"
resolution:
  perspective: [carry_through, vocabulary_table]
  note: >-
    同じ route が学習者に返しているコースの論文の配置（コース sources の landscape_placements・学習者に見せてよい状態・confirmed 優先・旧版 node_id は
    NodeResolver で読み替えるだけ）と、凍結で加わった関係（load_frozen_history の末尾 2 版の無向差）の両端を、select_l2_concept_ids の course_hints
    として「焦点・いまここ・トピックの対応・足跡」の後・1 hop の前に渡した（routes/atlas_view.py _l2_course_hints / course_placement_concept_ids /
    added_relation_pairs）。事実文は label_vocab.atlas_l2_omitted_note(hints) が描いた概念に実際に効いた手がかりだけを並べ、手がかりが無いときは
    「地図の並び順で先頭の項目を描いています」と言う（数値なし）。描かなかった確定済みの関係を名前で挙げる行は足していない（関係の両端を優先するため
    上限内では描かれる）。
  landed_in:
    - "backend/api/routes/atlas_view.py"
    - "backend/core/label_vocab.py"
    - "backend/tests/test_atlas_review_round25_r6.py"
    - "docs/features/field_atlas_overlay_spec.md"
    - "docs/manual/student/02-student.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "L2 の選出は第 5 波でサーバの 1 関数に寄せてあったので、入力を足すだけで済んだ。固有の差分: 配置の母集合は学習者の『論文の位置』レイヤーと同じ store 関数・同じ状態集合で読み、L2 専用の判定を書かなかった。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の固定コース（足跡なし・atlas_node_id なし）で L2 を開き直して、星形成と惑星形成が描かれ実線が 2 本になることは確かめていない（次周の再演）"
      - "コースの論文の配置と凍結で加わった関係の端点の合計が上限を超える分野での並び（上限内では confirmed の配置が先）"
      - "ブラウザでの描画（circle と実線の数）"
related: [IK-0989, IK-0940, IK-0887]
view_of: []
history: []
---

## 課題

症状: 第 6 周で凍結 2026.3 が成立し、関係「ブラックホール — 超大質量ブラックホール」「惑星形成 — 星形成」が地図の関係になった。受講直後の学生の
L2 には前者だけが実線で出て、後者は線にも名前にも出ない。L2 の描く 20 概念は骨格の並び順の先頭 20 件と同じで、前者が描かれたのは両端が偶然
その範囲に入っていたからだった。事実文は「いま学んでいる場所と、そのつながりを優先しています」と言う。

原因: 第 5 波（IK-0989）で代表選出をサーバに寄せたが、priority の材料は焦点・いまここ・トピックの atlas_node_id・足跡だけで、コースの論文の配置を
使っていない。足跡の無い学生 × 地図の対応を持たないトピックでは priority が空になり、既定の骨格順に戻る。事実文は優先が働いたかを見ずに同じ文を出す。

## 発見の観点

通し受講（reproduction）で学生が実線 1 本を読み、描く概念の列と骨格の並び順、選出関数の入力を突き合わせた（trace_walk）。IK-0989 の
verification.unverified が予告していた範囲（「コースのトピックが骨格概念に対応していないコースでは…先頭になる」）が実際の固定コースで起きた。

## 解決の観点

第 6 波（2026-10-27）で解決。選出の入力にコースの論文の配置と凍結で加わった関係を足し、事実文は使った手がかりで言い分ける。

## 一般化

型 available-but-unwired: 手がかりは同じ応答に揃っているのに、優先順位の関数へ渡されていない。確かめ方: 足跡の無い学生 × atlas_node_id の無い
コースで L2 を開き、描く概念の列が骨格順の先頭と一致しないこと、コースの論文が置かれた概念とその 1 hop が含まれることを見る。

## 第 7 周の再演（第 25 周・面 C 第 7 周、2026-10-28・campaign c-astro-r25-atlas-r7）

GONE（主部）。足跡のある学生 st-03（いまここ = 星間磁場）の L2 は、骨格順の先頭の「初期宇宙・インフレーション」が外れ、いまここ・コースの論文の配置（分子雲・ダスト・星形成・フィードバック・中性子星・統計推論 ほか）・2026.4 で加わった関係の両端（暗黒物質・暗黒成分の物理）で 20 概念が埋まった。描いた概念は出典タブの 4 論文の配置先 + いまここ + 加わった関係の両端と一致し、omitted_note の括弧「（いま学んでいる場所と、このコースの論文が置かれている場所と、地図の新しい版で加わった関係と、そのつながりを優先しています）」は選出の内容と合う。加わった関係「暗黒物質 — 暗黒成分の物理」は実線。注意: 学生は同題名のコースから 6be229e1 を選び（campaign の固定 1bad7d9f ではない — IK-0407・審判 A）、この是正の確かめ方の条件（足跡なし × 地図の対応なし）は前提状態が変わって観察していない。残り: 前の版で確定した関係 2 本は線にも名前にも出ない → NEW IK-1045。run: uxsim/runs/c-astro-r25-atlas-r7/20261027T172454Z-57525 seq 5 / 6 / 7。

## 第 8 周の再演（2026-10-28・judge_atlas_r8）

**GONE（確かめ方の条件で）**。足跡なし × 固定コース（st-01・run uxsim/runs/c-astro-r25-atlas-r8/20261027T224409Z-99225 seq 6）で、描かれた 20 概念は st-03（足跡あり）と同じ組で、骨格順の先頭（初期宇宙・インフレーション…）には戻らない。残り: 事実文の括弧が足跡なしでも「いま学んでいる場所と…」から始まる（NEW IK-1055）、改訂で加わった関係が上限の外に出る（IK-1045 STILL）。
