---
id: IK-0711
title: "equation_semantics のプロンプトが reconstruction.status の許容値から経路を問わず 'none' を除外し、同じプロンプトの TeX 方針（'none' を使え）と矛盾していた"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "TeX 原文から取り出した式を復元せずにそのまま採用し、意味役割だけを LLM に推定させる"
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [contract]
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
    確認（prompt.py / schema.py / validator.py / repair.py の現物と、論文レーダー自律周回の req-01711 のプロンプト）:
    語彙の正本 schema.RECONSTRUCTION_STATUSES は 'none' を含み、validator も repair も 'none' を受ける。プロンプトだけが
    許容値の行に「(exclude 'none')」を経路非依存で付け、出力スキーマの例も 'none' を欠いていた（PDF 経路の復元必須の規則を
    全経路の語彙として書き写していた）。構造軸 aggregation = 語彙をプロンプト側で別の形に再記述し、正本と分裂した。
    接続軸 contract = プロンプトが LLM に課す出力契約と、同じプロンプトの TeX 方針・後段 validator の受理範囲が両立しない。
    処理軸 none = 後段は TeX 経路で LLM 出力を使わず make_none() にするので、実害は LLM への矛盾した指示に留まる。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "経路ごとに選ぶ値が違うだけの語彙を、指示文で一方の経路の制約ごと書き写し、他方の経路の方針と矛盾する"
pattern: duplicate-canonical-sources
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回の頭脳メモ uxsim/runs/radar_loop/BRAIN_NOTES_I1.md（seq 1711）が、実際に LLM へ渡った equation_semantics のプロンプト（uxsim/runs/mailbox/req-01711.json）の許容値の行と TeX 方針の文言が食い違うことを指摘した。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "'none' を正式な値として語彙の正本どおりに提示し、経路で変わるのは選ぶ値だけにした（TeX は 'none'・PDF は 'none' を選ばない）。許容値の行は source_is_trusted で分岐する 1 関数から作り、出力スキーマの例にも 'none' を足した。LLM 呼び出しの回数・validator・repair は変えていない。"
  landed_in:
    - "src/episteme_graph/agents/equation_semantics/prompt.py"
    - "src/tests/agents/equation_semantics/test_reconstruction_status_vocab.py"
    - "docs/pipeline/agents.md"
  verification:
    methods: [guardrail]
    unverified:
      - "実際の LLM が TeX 経路で 'none' を返すようになったか（自律周回での再観察）"
related: []
view_of: []
history: []
---

## 課題

症状: TeX 由来の式の equation_semantics プロンプトで、許容値の行は「reconstruction statuses: none, … (exclude 'none')」、出力スキーマの例は
'none' を含まない 3 値、一方で TeX 方針は「Use reconstruction.status='none'」、制約欄も「reconstruction.status=none を優先」と言う。
観察の出所は uxsim/runs/radar_loop/BRAIN_NOTES_I1.md（seq 1711）と uxsim/runs/mailbox/req-01711.json。

原因: 語彙の正本（schema.RECONSTRUCTION_STATUSES）は 'none' を含み validator も受けるのに、プロンプトが PDF 経路の「復元必須」を
語彙そのものの除外として全経路に書いていた。

## 発見の観点

実際に LLM に渡ったプロンプトを読み、指示どうしの整合を見た（data_inspection）。

## 解決の観点

正本の語彙をそのまま示し、経路の違いは「どの値を選ぶか」の一文に移した（canonical_source）。プロンプトの文言をテストで固定した（guardrail_fix）。

## 一般化

経路ごとに使い分ける語彙を、指示文が一方の経路の制約ごと再記述すると、他方の経路の方針と食い違う。確かめ方は、各経路の
プロンプトを実際に組み立て、許容値の行・出力スキーマの例・方針文が同じ値を指すことを見る。辞書の型 duplicate-canonical-sources。
