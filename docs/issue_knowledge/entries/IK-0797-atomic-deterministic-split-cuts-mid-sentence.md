---
id: IK-0797
title: "主張の決定論の分割（split_pending の review suggestion）が文中の「can therefore become」で切れ、「…distortions [ref] can.」と「Become relevant …」の壊れた子主張を作る"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/agents.md"
  - "CLAUDE.md"
feature_context:
  realizing: "LLM の atomic 候補が無い長い主張を、決定論で 1 主張 = 1 命題の候補に分割して review に回す"
  layers: [pipeline_a, theory_artifacts]
classification:
  axes:
    processing: [logic]
    structure: [decomposition]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定（2026-10-16 にコードで確認・旧記述は仮説）: 決定論の分割規則が接続語・副詞（therefore 等）を文境界の手掛かりとして使い、助動詞と動詞の間（can therefore become）でも
    切る。切った先頭を大文字化し、等位の and を落とす。構造軸 decomposition = 分割の単位（節・命題）が文法の境界で定義されていない。
    処理軸 logic = 境界の判定条件の誤り。接続軸 none / 統制軸 none（split_required で review に残るため確定扱いにはなっていない）。
    確定には ClaimObjectBuilder の deterministic split の境界規則を読む。
generalization:
  level: general
  general_form: "文を命題に分ける規則が語の手掛かりだけで境界を決めるため、文法上の切れ目でない位置で切って読めない断片を作る"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 T0 が component_assembly（seq 2934）の available_claims を読み、claim_span_003_sub01 が「…distortions [ref] can.」、sub02 が「Become relevant … density field the long-wavelength …」（and が欠落）になっていることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md）。同じ材料で accepted_claims の block_id が空の行が多いことも記されている。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: "決定論分割の切れ目を ClaimObjectBuilder._connector_parts に一本化し、助動詞の直後・節頭以外の接続副詞・述語の出ていない関係節の内側では切らない。動詞の無い断片は接続語ごと戻して連結し、2 つに割れなければ子主張を作らない。_analyze_atomicity も同じ切れ目を使う（分割と判定の規則を分けない）。"
  landed_in:
    - "src/episteme_graph/agents/claim_object_builder/builder.py"
    - "src/tests/agents/claim_object_builder/test_split_points_and_system_atomicity.py"
    - "docs/pipeline/agents.md"
  verification:
    methods: [guardrail]
    unverified:
      - "再解析での実 run の再演（次の周で確認）"
      - "英語以外の主張文・名詞句の等位を関係節の外で持つ文（例: A and B are …）の判定"
related: [IK-0712, IK-0798]
view_of: []
history:
  - date: '2026-10-16'
    field: status
    from: 'open'
    to: 'resolved'
    reason: '原因確定: _CLAUSE_CONNECTORS.split が therefore / and を位置を問わず切り、動詞の無い断片の連結で接続語を落としていた。'
---

## 課題

症状: 主張「… distortions [ref] can therefore become relevant …」が決定論の分割で claim_span_003_sub01「…distortions [ref] can.」と
sub02「Become relevant… density field the long-wavelength…」（and が欠落）に割れる。split_required で残るが本文が壊れ、後段の材料になる。
原因（仮説）: 分割規則が語の手掛かりで切り、文法上の切れ目を見ていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2934）。

追加の出所（頭脳 T2）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 2957 — 同じ断片 claim_span_003_sub01 / sub02 が独立の主張として二層説明の対象に入り、skipped_reason=fragmentary_split_text で skip した）。

追加の出所（頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3283 — claim_span_002_82 が「Sect. Sec.」の略記のピリオドで機械分割され、sub02「Number density are fixed than by …」・sub03「Sec. 2.2).」の破片になる。頭脳は seq 3226 で分割なしと返していた）。

再発確認（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3663 — claim_span_007 の atomic 分割が「and」で切れ「The values of the nuisance parameters describing bias, RSD.」「Shot noise are chosen to …」と主語と述語が離れる。seq 3678 — 名詞句の断片「Upcoming Stage-IV galaxy surveys.」が D層スコープ抽出の対象になる）。決定論の分割か LLM の書き換えかは材料から区別していない。

## 発見の観点

atomic 子主張の本文を元の主張文と突き合わせた（data_inspection）。

## 解決の観点

切れ目の判定条件だけを直した（single_point_fix）。seq 2934 の文を逐語で回帰テストに置いた。

## 一般化

分割の単位を語の手掛かりで決めると、文法の切れ目でない所で切る。辞書の型 unit-of-work-undefined。
