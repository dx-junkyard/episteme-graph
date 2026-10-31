---
id: IK-1037
title: "学習チャットの往復ごとに出典番号が変わる（前の回答の [出典5] が今回 [出典12]）— 同じ論文の別の版の同じ箇所を往復ごとに選び直し、別のチャンクとして新しい番号が振られる"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22"
  - "docs/features/focus_document_design.md"
feature_context:
  realizing: "学生が前の回答の出典番号を手がかりに続けて尋ね、同じ番号で同じ箇所を指してもらう"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 接続軸 version — 出典番号は chunk_id 単位で会話の中に固定する（IK-0432）が、第 5 波の版の畳み込み（IK-0996）は
    往復ごとの焦点・式の復元の数・検索順位で残す版を選び直すため、同じ箇所が前の往復と別の版の chunk になり、新しい番号を受ける。
    確定には、前の往復の [出典5] と今回の [出典12] の chunk_id が同じ論文の別の版の同じ箇所であることをログで見ることが要る。
generalization:
  level: repo_pattern
  general_form: "同じ対象の写しのどれを代表に選ぶかを呼び出しごとに選び直し、代表に結び付けた番号が呼び出しをまたいで変わる"
pattern: id-not-stable-across-versions
discovery:
  perspective: [data_inspection]
  note: >-
    製品頭脳 B 4243 で、前の往復の [出典5][出典6] が今回の文脈では別の番号（NICER の学習データは [出典12]）になり、保存済みの回答と食い違った。
resolution:
  perspective: [carry_through]
  note: >-
    版を畳むとき、会話で既に番号を持つチャンク（_SessionCitationNumbers.mapping）を最優先で残す（collapse_paper_versions の
    prefer_chunk_ids・残す版の順位 ⓪）。学習チャットの本体と前提の説明の 2 経路で渡す。番号の採番器は変えていない。
  landed_in:
    - "backend/core/source_versions.py"
    - "backend/api/services.py"
    - "backend/api/routes/learning.py"
    - "backend/tests/test_round25_wave6_chat_rag.py"
    - "docs/backend/rag-chat.md"
  principles:
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "派生形のうち「前回の同一性に合わせて突き合わせる」だけを採った（前回の番号を持つ chunk を代表に残す）。行の同期・人の確定の保護は当たらない。"
  verification:
    methods: [guardrail]
    unverified:
      - "別の箇所（版の畳み込みに当たらない別のチャンク）が選ばれて番号が変わる場合（版とは別の根）"
      - "砂場の再演で同じ問いを続けたとき番号が揃うこと"
related: [IK-0432, IK-0996, IK-0444]
view_of: []
history: []
---

## 課題

症状: 前の回答が [出典5] と書いた箇所が、次の往復の文脈では [出典12] になり、学生も AI も番号で前の箇所を辿れない。原因（仮説）: 版の代表を往復ごとに選び直していた。

## 発見の観点

製品頭脳に渡ったプロンプトと保存済みの回答の出典番号の突き合わせ。

## 解決の観点

番号を既に持つ chunk を代表に残す（carry_through）。採番器を版単位にする案は、番号の単位（chunk）を変えるので採らなかった。

## 一般化

代表の選び直しで番号が変わる型。確かめ方: 前の往復で PDF 版に番号を振り、今回 TeX 版が上位でも PDF 版が残って同じ番号になることを見る。
