---
id: IK-0754
title: "component_assembly のプロンプトに、component_graph の validator が main graph で hard error にする汎用 operation（limit / parameterize / verify / couple / qualify / classify 等）の語彙表が無く、LLM が選んだ語が後段で error になる"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-22
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
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認済み: prompt.py の operation の記述は開いた例示だけで、classify_operation の表を渡していなかった。以下は起票時の見立て:
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
  perspective: [canonical_source, guardrail_fix]
  note: "component_graph/schema.py の operation 語彙表（_OPERATION_PREFIX_MAP / _OPERATION_FULL_MAP）を同一オブジェクトのまま公開名 OPERATION_PREFIX_FAMILIES / OPERATION_FULL_NAMES で出し、component_assembly のプロンプトが operation_vocabulary_text() でそれを閉じた表（prefix → グラフ関係・完全名 → グラフ関係・汎用語）として渡す（写しを作らない・遅延 import で循環を避ける）。開いた例示『define/linearize/…/forecast/compare/...』は撤去し、limit / parameterize / verify / couple / qualify / classify / forecast が汎用扱いになることを明示した。validator は operation / primary_operation が classify_operation で汎用と判定されたら warning operation_outside_graph_vocabulary を出す（error にしない・語は残す = 情報を落とさない）。"
  landed_in:
    - "src/episteme_graph/agents/component_graph/schema.py"
    - "src/episteme_graph/agents/component_assembly/prompt.py"
    - "src/episteme_graph/agents/component_assembly/validator.py"
    - "src/tests/agents/component_assembly/test_operation_vocabulary_ik0754.py"
  verification:
    methods: [guardrail]
    unverified:
      - "live LLM run not performed（実 LLM が閉じた表に従って語を選ぶかの実測）"
      - "component_graph の修復要求で main_graph_generic_operation が消えることの再解析での確認"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: >-
        採った部品は「規則を 1 箇所に置き、他は参照する」。固有の差分: 正本（classify_operation の表）は既に 1 つで、前段のプロンプトが別の開いた列挙を持っていたため、写しを作らず同一オブジェクトを公開名で参照させた。下流での読み替え（修復段で語を直す）ではなく上流の入力を変えた。
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

正本（classify_operation の語彙表）を前段のプロンプトへ同一オブジェクトのまま閉じた表として渡し（canonical_source）、語彙外を warning で報告する検査と、プロンプトに全 prefix・完全名が載ることのテストを置いた（guardrail_fix）。

## 一般化

後段が閉じた語彙で検査する欄を前段に開いた例示で渡すと、前段の選んだ語が後段で落ちる。辞書の型
contract-changed-one-side（語彙の列挙が二重にあるなら duplicate-canonical-sources）。
