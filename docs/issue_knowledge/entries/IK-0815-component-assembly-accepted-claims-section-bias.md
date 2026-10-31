---
id: IK-0815
title: "component_assembly の採択主張（上限 48）が全体の重要度順で選ばれ、結果の節の主張が枠を埋めて定式化・モデルの節が 0 件になり、入力も 1 コール 404KB に膨らむ"
status: resolved
recorded_at: 2026-10-17
resolved_at: 2026-10-17
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "論文の主張・式・中心命題から再利用可能な理論部品を LLM に組み立てさせるため、入力の主張を上限内で選んで渡す"
  layers: [pipeline_a]
classification:
  axes:
    processing: [resource]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認済み: claim_selection.select_claim_rows は claim_tier → 中心命題との語の重なり → confidence の全体順位で上位を取り、
    結果の節の paper_core 主張が多い論文では定式化の節（paper_supporting）が 1 件も残らなかった。接続軸 information = 導出の
    中核部品の根拠となる主張が組立段の材料に届かない。処理軸 resource = 入力の大きさに上限が無く、available_claims（全主張・
    1 行に多項目）だけで約 280KB になった。構造軸は low（表現の欠落ではなく選び方の規則の問題と見た）。
generalization:
  level: general
  general_form: "上限内で入力を選ぶ規則が全体の重要度だけを見るため、重要度の高い一群が枠を埋めて別の部分が丸ごと材料から消え、上限の無い参照一覧は入力を膨らませる"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 W1 が component_assembly（2610.08751・seq 3283）の入力を読み、accepted_claims 48 件が sec_7〜16 に偏り sec_3/4 が 0 件、入力全体が 404KB であることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md）。"
resolution:
  perspective: [single_point_fix, order_and_budget]
  note: "select_claim_rows に節の層化（section_key_of、既存の stratified_sampling.select_indices を流用。各節に最低 1 枠・残りは比例・節内は重要度順）を足し、component_assembly だけが使う（他段の選び方は不変）。入力の文字数上限をコード定数 MAX_INPUT_CHARS に置き、超えたらプロンプトに出す available_claims だけを間引いて（重複 → 非 atomic → 節の層化）、ID の許可リストは全件のまま、間引いた事実を coverage 形式で diagnostics に残す。"
  landed_in:
    - "src/episteme_graph/agents/claim_selection.py"
    - "src/episteme_graph/agents/component_assembly/input_builder.py"
    - "src/episteme_graph/agents/component_assembly/prompt.py"
    - "src/tests/agents/component_assembly/test_refiner_radar_w1_regressions.py"
  verification:
    methods: [guardrail]
    unverified:
      - "2610.08751 の再解析での accepted_claims の節の内訳と入力の大きさの実測"
      - "間引いた available_claims から LLM が引けなくなった主張で部品の根拠が弱まらないか"
      - "thesis_reconstruction・dsl_linking の主張選抜（全体順位のまま）"
related: [IK-0807, IK-0733]
view_of: []
history: []
---

## 課題

症状: component_assembly の accepted_claims 48 件が結果の節（sec_7〜16）に偏り、定式化・モデルの節（sec_3/4）が 0 件。入力は 1 コール 404KB。
原因: 主張の選抜が全体の重要度順で、節の網羅を見ない。参照一覧 available_claims に大きさの上限が無い。

## 発見の観点

入力の主張の節の分布と論文の構成を突き合わせた（data_inspection）。

## 解決の観点

選抜を節で層化し、入力の大きさはコード定数の上限で参照一覧の表示だけを間引いて、間引いた事実を残した（single_point_fix + order_and_budget）。
許可リスト自体を削る案は、LLM が accepted_claims 以外の ID を正しく引いたときに検証で弾かれるため採らなかった。

## 一般化

上限内で選ぶ入力はどこでも同じ偏りを起こしうる（thesis_reconstruction / dsl_linking も同じ選抜関数を全体順位で使う）。辞書の型 extent-decided-by-proxy-signal。
