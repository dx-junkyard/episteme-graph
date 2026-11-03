---
id: IK-0886
title: "関係（辺）の候補を次版の下書きへ反映しても、候補のカードは「採用」のまま下書きに入ったことを示さず、下書きのプレビューにも関係が出ないので、何を反映したかを 1 操作で確かめられない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_relation_edges_design.md"
feature_context:
  realizing: "教員が採用した関係を次版の下書きに反映し、反映した結果を確かめてから公開する"
  layers: [relation_edges_re, field_atlas_s, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [version]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: low
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 version: 候補の一覧は凍結版の骨格から導出する（routes/atlas_edges.py list_atlas_edge_candidates が
    derive_edge_candidates(skeleton=frozen)）。既存辺の除外も凍結版の edges だけを見るので、下書きに入った辺は
    凍結まで候補として残り続ける。admin.js の反映後の注記「反映済みの候補は次の導出で消える」は、この導出と食い違う。
    構造軸 representation: 判断の状態は accepted / dismissed（+ 凍結後の applied_version）だけで、「下書きに入った」
    状態を表す値が無い（mark-incorporated は監査だけで列を持たない — 設計上の選択）。カードにも下書きのプレビュー
    （領域 → 概念の列挙）にも関係の反映が描かれない。採用時・反映の確認にも「依存」の向き（どちらがどちらに）は出ない。
    統制軸 none（反映・凍結の手続そのものは成立している。low: 二重の反映を 409 で弾く弁は draft 側にある）。
    確認: run 20261022T220051Z-74929 seq 16〜17（反映）→ seq 20（1 番は[採用]のまま・プレビューは領域と概念のみ）。
generalization:
  level: repo_pattern
  general_form: "作業コピーへの反映を確定版の側から導出した一覧で見せるため、反映済みの項目が未反映と同じ顔で残り、作業コピーの状態を画面で確かめる場所が無い"
pattern: stale-derivative-served
discovery:
  perspective: [reproduction, doc_code_diff]
  note: "教員ペルソナが反映後に「1 番は[採用]のまま。下書きプレビューにも関係が出ない」と書いた。admin.js の注記と route の導出元（凍結版）を突き合わせた。 run: uxsim/runs/c-astro-r25-atlas-r2/20261022T220051Z-74929。"
resolution:
  perspective: [first_class_state]
  note: >-
    下書きに入った辺を候補の状態として返す。edge-candidates は下書きの edges を重ね、同じ無向ペアがあれば in_draft: true と in_draft_note（atlas_review_display.EDGE_IN_DRAFT）を付けて［次版の下書きへ反映…］を押せなくする（候補は消さない）。採用済みで種類があれば direction_line（「A が B に依存する関係として記載されます」— 依存の向きは記載順）を一覧と incorporate-preview に載せる。下書きの地図プレビューの下に「関係（辺）」の節（現在の公開版に無い関係に「次版で加わる関係」）を足した。
  landed_in:
    - "backend/api/routes/atlas_edges.py"
    - "backend/core/atlas_review_display.py"
    - "frontend/public/js/admin.js"
    - "docs/manual/teacher/17-admin-atlas.md"
    - "backend/tests/test_atlas_review_round25_r2.py"
  verification:
    methods: [guardrail]
    unverified:
      - "依存の向きを教員が選ぶ操作は未提供（向きは id の記載順のまま — 向きを文で言うところまで）"
      - "ブラウザ未確認"
related: [IK-0885, IK-0848]
view_of: []
history: []
---

## 課題

症状: 採用した関係を[次版の下書きへ反映…]で下書きに追加すると「次版を保存しました / 次版の下書きに追加しました」と出るが、
カードは[採用]のままで、反映済みかどうかの区別が無い。下書きのプレビューは領域と概念の列挙だけで関係が出ない。どの関係を
反映したかを一覧で確かめられない。「依存」の向きも示されない。

原因: 候補は凍結版から導出され、下書きに入った辺を候補の側で表す状態が無い。

## 発見の観点

再演（reproduction）の所見と、admin.js の注記・route の導出元の食い違い（doc_code_diff）。

## 解決の観点

第 2 波（2026-10-23）で是正。下書きの辺を候補の導出に重ね、「下書きに入っている」を状態として返す。プレビューに関係を描く。

## 一般化

確定版から候補を導出し、作業コピーに反映する 2 段の運用（カテゴリギャップの取り込み・別名・対応表）で同じ形が出うる。
確かめ方: 反映した直後に一覧・プレビューの両方で反映済みと読めること、もう一度反映できないことを見る。
