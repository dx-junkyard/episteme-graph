---
id: IK-0989
title: "学習者の分野の地図の L2（コースレベル）は骨格の概念を先頭 20 件しか描かず、凍結で加わった関係（ブラックホール — 重力波）と推定の糸の大半が画面に出ない — 上限超えは console.warn だけで、学習者にも教員にも言われない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_relation_edges_design.md"
  - "docs/features/field_atlas_overlay_spec.md"
feature_context:
  realizing: "教員が凍結で地図に加えた関係と、AI 推定の糸を、学習者が分野の地図の L2 で読む"
  layers: [field_atlas_s, relation_edges_re, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 representation: routes/atlas_view.py は L2 の nodes に骨格の全概念（宇宙物理は 50）を返すが、atlas-overlay.js の LIMITS.l2Nodes = 20 と _MAX_L2_NODES = 20 は「コースの概念マップ ≤ 20」を前提に描画を先頭 20 件で切る（領域の並び順で宇宙論・銀河・星間物質・高エネルギーの一部まで）。L2 の実線の辺（骨格の関係）と atlas-threads-layer.js の糸は描かれた circle の座標を読むので、端点が 21 件目以降の関係・糸は描かれない。接続軸 information: 切ったことは console.warn だけで、学習者向けの事実文も糸の事実行も切り捨てを言わない（糸の事実行は 30 本ぶんの根拠を列挙する）。処理軸 none（切り捨ては仕様の上限どおりに動く）、統制軸 none。
generalization:
  level: repo_pattern
  general_form: "表示面の描画上限が、データ側の母集合の成長（全概念を返す API）と食い違い、上限を超えた要素とそれにつながる関係が画面の外で黙って消える"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [trace_walk, reproduction]
  note: "凍結 2026.2 の後に学生が L2 を開き、頭脳は『凍結で加わった関係がどこにも関係として出ない』と記した。投影（API の全量を列挙）では糸が 30 本並ぶため、atlas_view.py の L2 nodes と atlas-overlay.js の LIMITS・描画ループ、atlas-threads-layer.js の座標読みを突き合わせて、製品は先頭 20 概念しか描かないことを見た（ブラウザでの描画は未確認）。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T071720Z-78305 seq 5 / 6。"
resolution:
  perspective: [representation_change, responsibility_move]
  note: >-
    L2 の代表選出をサーバの 1 箇所（routes/atlas_view.py select_l2_concept_ids）へ移した。焦点・いまここ・コースのトピックに対応する概念・足跡 → その 1 hop → 骨格の並び順で §13 の上限まで選び、描かなかった概念があれば levels['2'].omitted_note（label_vocab.ATLAS_L2_OMITTED_NOTE・件数なし）を返し、実線は描いた概念どうしの間だけ送る。糸の端点が描かれていなければ threads.undrawn_note。フロント（atlas-overlay.js #atlas-level-note / atlas-threads-layer.js .threads-undrawn）は素通し。地形と関係の描き方（field_map_display_principles）は変えない。
  landed_in:
    - "backend/api/routes/atlas_view.py"
    - "backend/core/label_vocab.py"
    - "frontend/public/js/atlas-overlay.js"
    - "frontend/public/js/atlas-threads-layer.js"
    - "frontend/public/css/atlas.css"
    - "docs/manual/student/02-student.md"
    - "backend/tests/test_atlas_review_round25_r5.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "描く範囲の判断を描画側の黙った切り捨てから、データを返すサーバの 1 関数に寄せ、描かなかった事実をそこから事実文で返す形を採った。固有の差分: 上限そのもの（§13）は残し、母集合をコースの手がかりで並べ替えるだけにした。"
  verification:
    methods: [guardrail]
    unverified:
      - "ブラウザでの L2 の描画（選ばれた 20 概念と凍結で加わった関係の実線）は確かめていない（第 6 周の browser runner）"
      - "コースのトピックが骨格概念に対応していないコースでは、いまここ・足跡が無ければ従来どおり骨格の並び順の先頭になる"
related: [IK-0887, IK-0886, IK-0942]
view_of: []
history: []
---

## 課題

症状: 第 5 周で凍結 2026.2 が成立し、関係「ブラックホール — 重力波」が地図の関係になった。学生の L2 では糸からこの組が消える
（確定した関係は糸にしない = RE の設計どおり）が、実線の関係としても読めない。製品の L2 は骨格 50 概念のうち領域順の先頭 20 件
（宇宙論 6・銀河 5・星間物質 5・高エネルギーの先頭 4）しか描かず、重力波は 21 件目以降なので線が引けない。糸も両端が先頭 20 件に
入る組（ブラックホール ⋯ 超大質量ブラックホール など数本）しか描けず、事実行と根拠の折りたたみが言う 30 本のほとんどが画面に無い。

原因: L2 は「コースの概念マップ（≤ 20）」として設計されたが、サーバは骨格の全概念を返し、描画側だけが上限で切る。切り捨ては
console.warn だけで、画面の言葉にならない。

## 発見の観点

通し受講（reproduction）で学生が「関係が消えた」と記し、API の L2 と描画コード（trace_walk）を突き合わせた。投影は API の全量を
並べるため、この切り捨てはハーネスの観察には現れない（harness_fixes_atlas.md H32）。

## 解決の観点

第 5 波（2026-10-27）で解決。L2 の代表選出をサーバへ移し、描かなかったことを事実文で言う。

## 一般化

描画上限を持つ画面は、母集合を返す側と上限の前提を共有し、上限を超えたことを画面の言葉で言う。確かめ方: 骨格の全概念を
返す版で L2 を開き、描かれた circle の数と API の nodes の数、糸の事実行の本数と描かれた線の数を突き合わせる。



## 第 6 周の再演（第 25 周・面 C 第 6 周、2026-10-27・campaign c-astro-r25-atlas-r6）

一部 GONE・根は残る。描かなかったことは言われるようになった（levels['2'].omitted_note・threads.undrawn_note が観察に出た）。凍結 2026.3 で確定した「ブラックホール — 超大質量ブラックホール」は実線で読めた。ただし描いた 20 概念は骨格の並び順の先頭 20 件と完全に一致し（足跡の無い学生 × トピックに atlas_node_id の無い固定コースで priority が空）、もう 1 本の確定した関係「惑星形成 — 星形成」は惑星形成が選ばれず線にも名前にも出ない。実線 1 本は偶然両端が先頭 20 件に入っていたため。verification.unverified の 2 行目が予告した範囲が実際に起きた — 新規 IK-1017 に起票（代表選出がコースの論文の配置を使わない・事実文が優先の有無で分かれない）。ブラウザ描画は今回も未確認（browser runner がハーネスに無い）。run: uxsim/runs/c-astro-r25-atlas-r6/20261027T123201Z-9456 seq 6。
