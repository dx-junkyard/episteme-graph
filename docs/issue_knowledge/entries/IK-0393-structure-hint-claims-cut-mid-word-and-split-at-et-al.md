---
id: IK-0393
title: "学習チャットの検索由来の構造ブロックで、主張が 120 字の素スライスで語の途中から切れ（…indicating tha）、主張そのものが「et al.」で二つに割れ（The ACF analysis (Houde et al. / 2009), extends…）、「≈9.3」のような数値だけの主張も構造の手がかりとして渡っていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 検索で当たった箇所に結ばれた主張を、AI が構造の手がかりとして読む
  layers: [screen_adapter_sa, pipeline_a, rag_chat]
classification:
  axes:
    processing: [input_handling]
    structure: [aggregation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸で、切り詰めと文分割には正本がある（core.text_excerpt.excerpt・evidence_registry の略語対応の分割）
    のに、画面文脈アダプターの解決器（resolvers/learning.py・graph_review.py の _text）は text[:limit] の素スライス、
    ClaimObjectBuilder の原子性判定と決定論分割は re.split の素朴な正規表現を使っていた（aggregation。medium:
    正本の不使用か、正本が A層・backend に分かれて見つけにくかったかの境界）。処理軸で、数値・記号だけの主張を
    構造の手がかりから外す判定が無かった。確認: 第 8 周の実プロンプト（req-00007 / req-00017 / req-00045）に
    「…aligned field in the t」「The ACF analysis (Houde et al.」「The DCF method (Davis Jr 1951.」。
    ClaimObjectBuilder._analyze_atomicity が Houde の文を「contains multiple sentences」と判定し、
    _split_into_atomic が et al. で割ることを再現した。第 8 周の主張行がこの決定論分割由来か LLM の atomic rewrite
    由来かは DB を見ておらず未確認（どちらでも et al. で割れる経路は塞いだ）。
generalization:
  level: repo_pattern
  general_form: 正本の切り詰め・分割があるのに、下流の箇所が素のスライスや素朴な正規表現で自前に切り、語や略語の途中で割る
pattern: duplicate-canonical-sources
discovery:
  perspective: [data_inspection, reproduction]
  note: 実プロンプトの主張の切れ目を読み、ClaimObjectBuilder で再現した。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    (a) 解決器の _text は limit 超過時に core.text_excerpt.excerpt（文境界 → 語境界 → 文字数・省略記号付き）へ委譲
    （learning / graph_review の両方）。(b) 略語対応の文分割を src/episteme_graph/agents/sentence_split.py に
    正本化し（et al. / Fig. / Figs. / Eq. / Eqs. / Sec. / Ref. / vs. / e.g. / i.e. / cf. / al. / No. / Jr. ほか、
    閉じていない括弧の中の「数字 + 点」も境界にしない）、evidence_registry の split_sentences_with_offsets は委譲、
    ClaimObjectBuilder の 2 箇所の素朴な正規表現を置き換えた。(c) 字母が 2 字未満の主張（≈9.3 / (2) / B = 3）は
    解決器で構造の手がかりにしない（A層の行は消さない）。
  landed_in:
    - backend/core/assistant_context/resolvers/learning.py
    - backend/core/assistant_context/resolvers/graph_review.py
    - src/episteme_graph/agents/sentence_split.py
    - src/episteme_graph/agents/evidence_registry/builder.py
    - src/episteme_graph/agents/claim_object_builder/builder.py
    - backend/tests/test_assistant_context_claim_hygiene.py
    - src/tests/agents/test_sentence_split.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再観測（再解析後の主張行が et al. で割れないこと・構造ブロックが語境界で切れること）
      - 既存の割れた主張行（再解析するまで DB に残る）
      - LLM の atomic rewrite（ClaimQualificationAgent）が略語で割る場合（プロンプト側は未変更）
      - 日本語の「。」の直後に空白が無い文の分割（従来どおり分割しない）
      - _split_into_atomic の接続詞分割が「and」を落として本文を変える件（別件・未修正）
related: [IK-0390]
view_of: []
history: []
---

## 課題

構造の手がかりの主張が語の途中で切れ、et al. で割れ、数値だけの断片も渡っていた。

## 発見の観点

実プロンプトの切れ目を読み、分割器で再現した。

## 解決の観点

切り詰めと文分割を正本に寄せ、数値だけの断片は解決器で外す。

## 一般化

正本の切り詰め・分割を使わず、下流が素のスライス・素朴な正規表現で切る。
