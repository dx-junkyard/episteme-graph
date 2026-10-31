---
id: IK-0379
title: "コース内容の生成が組む文脈に、端点名が空の依存関係行（-  ->   (Type: derives)）や JSON の途中で切れたトピック草案プロンプト（available_references が読めない）・空の参照一覧が混ざり、生成モデルが根拠を付けられない"
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が LLM へ渡す文脈（依存関係・参照一覧）を組む
  layers: [course_builder]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [contract]
    governance: [budget]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認済み（起票時の仮説 3 つのうち 2 つは成立、1 つは見立て違い）。統制軸（成立）: トピック草案プロンプトは
    `json.dumps(...)[:8000]` の文字列スライスで JSON の途中を切っていた（budget）。しかも `available_references` は
    長い `content_blocks` の後ろに置かれていたため、予算を超えると真っ先に切られて読めなくなった。処理軸（成立）:
    空の参照一覧（`"available_references": []`）や空の欄、端点が空の依存行をそのまま渡していた（input_handling）。
    接続軸（見立て違い → contract）: 端点が空だった原因は「部品 ID を名前へ解決していない」ことではなく、
    コースビルダーの文脈組み立て（`routes/admin.py::_build_material_context`）が辺の端点を `source` / `from` /
    `target` / `to` で読み、保存形（`persistence.persist_component_graph` の `source_component_id` /
    `target_component_id`）を読んでいなかったこと。種別だけは `relation` で読めたので `-  ->   (Type: derives)` に
    なった。既存テストの fixture が旧形 `source` / `target` だったため green のまま気付かれなかった。名前解決も
    していなかった（ID しか書けない）ので、あわせて直した。
generalization:
  level: repo_pattern
  general_form: 生成モデルへ渡す文脈の切り詰めと未解決の参照を、渡す前に検査しない
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [data_inspection]
  note: 肩代わり頭脳がプロンプトの実物を読んだ。
resolution:
  perspective: [order_and_budget, guardrail_fix]
  note: >-
    文字数予算を要素単位の間引きに置き換えた（`course_content_builder._prompt_json`: 空の値を落とす → 長文の末尾を
    「…」で切る → 要素数の多いリストの末尾要素を落とす → 残りの文字列を切る → 最後に `available_references` の
    末尾要素。`latex` / `id` などは途中で切らず要素ごと落とす。間引いたら `_omitted` に事実文を1つ足す。JSON は
    常に読める）。`available_references` を根拠候補の先頭に移した。依存行は `_component_dependency_lines` が保存形の
    端点キーを先に読み、グラフのノード名 → theory_components の名前で「名前 (ID)」にし、端点が空のままの辺は
    行にしない（全部落ちたら見出しも出さない）。
  landed_in:
    - backend/api/routes/admin.py
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0379_course_context_prompt.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのペルソナ通し受講の再実行（生成時のプロンプトを実物で読み直すこと）
      - 実 LLM が間引かれた根拠候補で根拠を付けられるようになったか（生成品質）
      - 参照一覧が空だったトピックが上流（evidence_links の組み立て）でも空なのか
      - 依存行の上限は辺の先頭から数えるため、式の詳細層の辺が主グラフの辺を押し出しうること
      - 保存されたグラフの辺が graph_layer を持たないこと（層で選べない）
related: [IK-0377]
view_of: []
history:
  - date: '2026-09-28'
    field: classification
    from: connection=[information] cause_status=hypothesis confidence=medium
    to: connection=[contract] cause_status=confirmed confidence=high
    reason: 端点が空だったのは名前未解決（information）ではなく、読み手のキー名が保存形の辺と食い違っていた（contract）。コードと保存形を突き合わせて確認
---

## 課題

生成文脈の切り詰めと未解決参照。

## 発見の観点

製品のプロンプトの実物を読む。

## 解決の観点

予算は文字列ではなく要素の単位で統制する（切っても JSON は閉じる・閉世界の一覧は最後まで残す）。依存行は保存形の
キーで読み、名前へ解決し、端点が空の行は渡さない。fixture を保存形に揃えたテストで固定した。

## 一般化

境界での検査不在。
