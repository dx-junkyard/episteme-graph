---
id: IK-0784
title: "claim_qualification のプロンプトが atomic claim の先頭に置いてはいけない接続語を手書きの一部（and / but / therefore …）だけで示し、validator は because / since も弾くため、「Because …」で始まる応答が修復要求に回り続ける"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "主張を 1 命題ずつの自立した文に atomic 化し、出力契約を validator で検査して違反を修復させる"
  layers: [pipeline_a]
classification:
  axes:
    processing: [wording]
    structure: [aggregation]
    connection: [contract]
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
    仮説（コードの読み合わせで確度は高い）: 禁止する先頭接続語の集合は schema.py の ATOMIC_CLAIM_LEADING_CONNECTORS（because / since を
    含む 20 語）が validator 側の正本で、prompt.py は「(and / but / therefore / then / thus / hence / moreover ...)」と手書きの一部だけを
    示す。LLM は because / since が禁止だと知らされずに書き、validator が atomic_claim_starts_with_connector で弾いて修復に回る。
    構造軸 aggregation = 同じ語彙の表がプロンプトと validator の 2 箇所に分かれている。接続軸 contract = 出力契約の片側（指示）が
    検査側と一致していない。処理軸 wording = 指示文の列挙の欠け。統制軸 none。確定には、prompt を正本の集合から組み立てた後に
    「Because 始まり」の修復が消えることを周回で見る。
generalization:
  level: repo_pattern
  general_form: "出力の禁止語彙を検査側の正本とは別に指示文へ手書きで抜き出し、指示に無い語を検査が弾いて修復の往復が生じる"
pattern: duplicate-canonical-sources
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 S0 が 2609.35757（TeX 経路）の claim_qualification を担当し、修復要求が 3 回連続（seq 2757・2760・2763）していずれも「Because 始まり」の接続語違反であること、プロンプトの接続語の例に because / since が無いことに気付いた。出所: uxsim/runs/radar_loop/BRAIN_NOTES_S0.md。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "禁止する先頭接続語の正本を schema の ATOMIC_CLAIM_LEADING_CONNECTOR_WORDS と leading_connector_list_text() に置き、プロンプトの手書き列挙を撤去して正本から組み立て、validator の違反メッセージにも全語の列挙と言い直しの指針 CAUSAL_REWRITE_GUIDANCE を載せた。「Because X, Y」は「Y because X」に言い直し、無理なら non_atomic / review_required で保持する。X を qualification_reason に移す案は本文の情報を落とす（P4）ため採らなかった。"
  landed_in:
    - "src/episteme_graph/agents/claim_qualification/schema.py"
    - "src/episteme_graph/agents/claim_qualification/prompt.py"
    - "src/episteme_graph/agents/claim_qualification/validator.py"
    - "src/tests/agents/claim_qualification/test_connector_vocab.py"
    - "docs/pipeline/agents.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "当たる型の列には無い duplicate-canonical-sources だが、部品①（語彙の正本 1 つ）と④（プロンプト・validator を正本への委譲だけにする）を採った。全経路の横断検査は置かず、正本とプロンプト・validator の一致を単体テストで固定した。設計前に原理は読んでいない。"
  verification:
    methods: [guardrail]
    unverified:
      - "実 LLM で「Because 始まり」の修復の往復が収まること（周回で実測）"
related: [IK-0754, IK-0711]
view_of: []
history: []
---

## 課題

症状: claim_qualification の修復要求が 3 回連続し、どれも atomic claim が「Because …」で始まる接続語違反だった。修復のたびに LLM コールを使う。

原因（仮説）: 禁止接続語の正本（schema）とプロンプトの例示が別々で、プロンプトに because / since が無い。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_S0.md（seq 2757・2760・2763）。

## 発見の観点

修復要求の理由を連続して読み、指示文と検査の語彙を突き合わせた（data_inspection）。

## 解決の観点

指示の列挙を検査側の正本から生成し（手書きの部分集合をやめる）、検査の違反メッセージにも同じ正本と言い直しの指針を載せた（canonical_source）。一致は単体テストで固定した（guardrail_fix）。理由を qualification_reason へ移す案は本文の情報を落とすため採らなかった。

## 一般化

検査側の語彙を指示に手書きで写すと、写し漏れがそのまま修復の往復になる。確かめ方は、正本に語を 1 つ足したときプロンプトにも現れることと、
「Because 始まり」の修復要求が周回で出なくなることを見る。辞書の型 duplicate-canonical-sources（IK-0754・IK-0711 と同じ「指示と検査の語彙の分裂」）。
