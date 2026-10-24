---
id: IK-0916
title: "深く検討の同一性リンクの行が、結んだ相手の部品を内部の要素型と論文の UUID（「インスタンス: theory_component / ce667c63-…」）で示し、教員が手で作って確定したリンクにも確信度の段階ラベル「暫定」を付け続けるため、何と何を結んだか・確定したのかが読めない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が論文の部品を共通部品に結びつけ、結んだ対を確かめて確定する"
  layers: [deliberation_w, concept_registry, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: frontend/public/js/deliberation.js:1976-1978 の _identityLinkRowHtml は instance_element_type と
    instance_document_id をそのまま「インスタンス: …」に描き、部品の名前・論文の題名を描かない（応答にも同梱が無い — routes/deliberation.py:184-205 の
    _identity_link_response は共通部品側の name・summary だけを足す）。確信度は
    identity_links.confidence_label（core/deliberation/identity_links.py:47-52）が生値 None
    を最も慎重な「暫定」に倒し、手動作成（mapping_justification=manual_curation・confidence なし）でも確定後でも同じラベルを描く。構造軸 representation: 「AI
    の確信の段階」と「人の判断の状態」を同じ行の同じ位置の語で表す。処理・統制は none（判定と確定の手続は正しく動く）。
generalization:
  level: repo_pattern
  general_form: "対応づけの行が、片側を表示名で、もう片側を内部の型と識別子で示し、人が作った行にも機械の確信度の段階を付けるため、何と何をどの根拠で結んだのかが読めない"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-02 が TeX 版の「TOV network map」を共通部品に結んで確定した直後、行に英数字の列と「暫定」が残ると記した。応答と描画関数を突き合わせた。run:
    uxsim/runs/c-astro-r25-share-r3/20261023T032742Z-99528 seq6-7・seq10。面の審判 B（内部 ID と英語の要素型）が同じ所で鳴った。
resolution:
  perspective: [representation_change]
  note: >-
    core/deliberation/identity_links.py に describe_instance（部品・主張・式・図・記号・理論モジュールの名前・要約・入出力・論文の題名。live ビューのみ・fail-soft）と出所の語（provenance_label: 教員が作成 / 教員が確定 / 教員が却下 / 照合による候補）・shows_confidence（AI・照合由来の未確定の行だけ確信度の段階）を置き、routes/deliberation.py の _identity_link_response が instance_type_label / instance_label / instance_summary / instance_inputs / instance_outputs / instance_document_title / provenance_label を同梱する。手動・確定済みの行は confidence_label を None にする。deliberation.js の _identityLinkRowHtml は「部品: 名前｜要約（論文『題名』）」と入出力を描き、内部の型名・UUID は title 属性にだけ残す。
  landed_in:
    - "backend/core/deliberation/identity_links.py"
    - "backend/api/routes/deliberation.py"
    - "frontend/public/js/deliberation.js"
    - "backend/tests/test_share_round25_identity_links.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "表示語の解決を行の応答を組む 1 箇所（_identity_link_response → describe_instance）に置き、作成・確定・一覧・共通部品側の一覧の全経路が同じ語を返すようにした。固有の差分: 確信度の段階と人の判断の状態を別のキーに分けた。"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面での行の見え方（手動作成・AI 候補・確定済みの 3 種）"
      - "実 DB での describe_instance の SQL（theory_components_live / document_figures / knowledge_theory_modules_live）"
related: [IK-0866, IK-0917]
view_of: []
history: []
---

## 課題

症状: 同一性リンクの行に「インスタンス: theory_component / ce667c63-…」と出て、どの論文のどの部品かが読めない。手で作り確定した行にも「暫定」が付き、確定と暫定が並んで矛盾して読める。原因: 応答が部品側の表示名を持たず、画面が内部の値を描く。確信度の段階を出所に関係なく付ける。

## 発見の観点

教員ペルソナの通し受講（reproduction）で見え、応答から描画関数まで辿った（trace_walk）。

## 解決の観点

是正済み（第 3 波）。行の応答が部品側の名前・要約・入出力・論文の題名と出所の語を同梱し、画面は内部の型名・UUID を本文に描かない。確信度の段階は AI・照合由来の未確定の行だけに付く。

## 一般化

表示語の写像を面ごとに直す型（rule-enforced-per-surface）の、対応づけの行での一形。確かめ方は、手動作成・AI 候補・確定済みの 3 種のリンクを 1 本ずつ作り、どの行にも内部の型名・UUID が出ず、確信度の段階が AI 由来の行だけに付くことを画面で見ること。
