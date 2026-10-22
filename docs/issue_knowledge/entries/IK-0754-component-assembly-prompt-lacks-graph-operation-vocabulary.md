---
id: IK-0754
title: "component_assembly のプロンプトに、component_graph の validator が main graph で hard error にする汎用 operation（limit / parameterize / verify / couple / qualify / classify 等）の語彙表が無く、LLM が選んだ語が後段で error になる"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/theory-graph.md"
feature_context:
  realizing: "部品の operation を LLM に選ばせ、その語から理論操作グラフの edge_type と theory stage を決定論で導く"
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 後段の component_graph は schema.classify_operation の prefix 語彙（define / linearize / solve / eliminate /
    derive / constrain / diagnose / compare …）で edge_type を決め、それ以外の汎用 operation を main graph で hard error
    にする。前段の component_assembly のプロンプトは『define/linearize/…/diagnose/forecast/compare/...』と開いた例示
    しか出さず、許される語の閉じた表を渡していない。接続軸 contract = 前段の出力契約（任意の動詞）と後段の入力契約
    （閉じた prefix 語彙）が両立しない。構造軸 aggregation = 許される operation 語彙の正本が classify_operation に
    あり、プロンプト側が別の（開いた）列挙を持つ（low）。処理軸 none（validator の判定は規律どおり）。確定には
    component_assembly の prompt.py の operation の記述と、classify_operation の語彙・generic 判定を突き合わせる。
generalization:
  level: general
  general_form: "後段が閉じた語彙で検査する欄を、前段の生成者には開いた例示だけで渡すため、生成者の選んだ語が後段で不合格になる"
pattern: contract-changed-one-side
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 M1 が component_graph の修復要求（seq 2092 = AdS/dS 連星 PN 論文・2089 の再提出）を読み、main graph の汎用 operation として limit / parameterize / verify / couple / qualify / classify が error になっていること、それが同じ頭脳が component_assembly（seq 2077）で選んだ語だったことに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_M1.md）。"
resolution:
  perspective: [pending]
  note: "未解決。classify_operation の語彙を正本にして component_assembly のプロンプトへ閉じた表として渡し（言い換えない）、語彙外は generic として扱う旨を明示する案。一致は逐語ミラーの検査で固定する。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0729, IK-0727]
view_of: []
history: []
---

## 課題

症状: component_graph の修復要求で、main graph の汎用 operation として limit / parameterize / verify / couple / qualify /
classify が hard error になる。これらは component_assembly で LLM が選んだ語で、プロンプトは開いた例示しか出していない。
辺しか返せない修復段ではこの誤りを直せず、修復の空回り（IK-0729）につながる。
原因（仮説）: 後段が閉じた語彙で検査する operation を、前段のプロンプトに語彙表として渡していない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_M1.md（seq 2077・2092）。

## 発見の観点

修復要求の検証エラーの語と、それを選んだ前段のプロンプトの記述を突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

後段が閉じた語彙で検査する欄を前段に開いた例示で渡すと、前段の選んだ語が後段で落ちる。辞書の型
contract-changed-one-side（語彙の列挙が二重にあるなら duplicate-canonical-sources）。
