---
id: IK-0733
title: "component_assembly の headline に指定された主張 ID が非 atomic / available_claims に無い旧形式で、refiner の振り分けが支持先の主張と構成要素の主張を区別できない"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/features/knowledge_objects_design.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "部品ごとに支持する主結果（headline）と構成要素の主張を分け、refiner で子部品に構成要素だけを振り分ける"
  layers: [pipeline_a, knowledge_objects]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [target, contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: claim_centered_plan の headline_claim_ids が span 由来の旧形式 ID（claim:tex_b249:span_002）や非 atomic の
    親主張を指し、available_claims（claim object の名前空間）に存在しない。さらに refiner の artifacts_to_route は、部品の
    supports_claim_ids（支持先 = headline 系）と構成要素の主張を区別せず再配分候補に入れている。構造軸 representation =
    「支持先」と「構成要素」の役割の区別と、主張 ID の名前空間が入力の表現に無い。接続軸 target = headline が後段で
    解決できず、支持先の主張が子に振られて unassigned になる。接続軸 contract = component_assembly の出力（旧形式 ID を
    許す）と refiner の入力（atomic な available_claims のみ）が両立しない。処理軸 none / 統制軸 none。確定には
    headline_claim_ids の検証規則と、artifacts_to_route を組み立てるコードを読む。
generalization:
  level: general
  general_form: "役割の違う 2 種類の参照（支持先と構成要素）と 2 つの名前空間の ID が 1 つの列に混ざり、後段が参照を解決・振り分けできない"
pattern: id-namespace-conflated
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 M0 が component_assembly（seq 2079）と component_refiner の振り分け（seq 2082・2085・2088）の材料を読み、headline の ID が使えないこと、主結果の主張が再配分候補に混入して unassigned になることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_M0.md）。seq 2055 では headline が main_result でない個別結果文だった。"
resolution:
  perspective: [pending]
  note: "未解決。headline は available_claims の atomic な ID だけを許し（旧形式は写像して解決・解決不能は review_required）、refiner の再配分候補から supports_claim_ids を外す案。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0719, IK-0724, IK-0728]
view_of: []
history: []
---

## 課題

症状: claim_centered_plan の headline_claim_ids が非 atomic の主張と available_claims に無い旧形式 ID（claim:tex_b249:span_002）で、
制約上そのまま支持先に使えない。refiner の振り分けでは、部品の supports_claim_ids に入れた主結果の主張が artifacts_to_route に
混入し、どの子にも振れず unassigned になる（3 部品で同型）。
原因（仮説）: 支持先と構成要素の役割、主張 ID の名前空間が入力の表現で区別されていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_M0.md（seq 2055・2079・2082・2085・2088）。

追加の出所（2609.35757・頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2934 — claim_centered_plan の headline_claim_ids・thesis_nodes・DSL の source_refs が旧形式 claim:tex_b166:span_003 で、available_claims（claim_span_*_sub* 形）に存在せず、部品から中心命題への supports_claim_ids を結べない）。

追加の出所（2610.08751・頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3283 — headline_claim_ids・thesis_nodes.claim_ids・dsl_nodes.source_refs.claim_ids がすべて旧形式 claim:tex_b142:span_001 で、available_claims は claim_span_001_77 形。変換表が材料に無く supports_claim_ids に headline を直接使えない。accepted_claims の legacy_claim_id は 48 件分しか無い。seq 3289 — DSL 辺の evidence claim も旧形式 claim:tex_b40:span_002）。

追加の出所（頭脳 X1・是正前の run（seq 3289〜3310・2026-10-17 03:00〜03:11））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3289 — DSL 辺の evidence claim が旧形式 claim:tex_b40:span_002 で、Material 側の claim_span_* と対応表が無い）。

追加の出所（第 2 波 deploy 後の run・頭脳 Z1）: uxsim/runs/radar_loop/BRAIN_NOTES_Z1.md（seq 3616 — headline_claim_ids が旧形式 claim:tex_b23:span_002 で available_* に無く中心命題を evidence_refs に張れない。seq 3667 — 要旨の中心主張 claim_span_002 と atomic 子の upper_context が空で、thesis が旧形式 ID で結んでいるため新形式の主張から逆引きできない）。

## 発見の観点

headline の ID と available_claims、supports_claim_ids と artifacts_to_route を突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

2 つの名前空間・2 つの役割の参照が 1 列に混ざる。辞書の型 id-namespace-conflated（IK-0719 の派生）。
