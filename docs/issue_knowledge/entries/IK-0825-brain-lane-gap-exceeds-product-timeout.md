---
id: IK-0825
title: "自律周回で頭脳の交代や使用上限による空白があると、LLM 肩代わりの応答が製品の打ち切りを越えて遅れ（約 25 分）、要求は製品側で縮退済みになって後から置いた応答が使われない"
status: open
recorded_at: 2026-10-17
resolved_at: null
sources:
  - "docs/features/paper_radar_autonomous_loop_design.md"
  - "docs/architecture/persona_enactment_testing_design.md"
feature_context:
  realizing: "検証用の自律周回で、製品の LLM 呼び出しを頭脳（エージェント）が mailbox 経由で肩代わりし、解析を通しで流す"
  layers: [cycle_verification]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [ordering, resume]
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
    仮説: 頭脳のレーンは交代（前任の停止と後任の起動の間）や使用上限で無人になる時間があり、その間に出た要求は製品の打ち切り（IK-0715 の
    AGENT_LLM_TIMEOUT_SECONDS 等）を越えて待たされる。製品は打ち切りで縮退して先へ進み、後から頭脳が置いた応答は呼び出し元が去っているので
    使われない。統制軸 ordering = レーンの交代が「前任の担当が尽きてから後任が起動」の順で、空白が生まれる。統制軸 resume = 空白中の
    要求を後から拾い直す仕組みが無い（置いた応答は捨てられ、縮退結果だけが残る）。処理軸 none / 構造軸 none / 接続軸 none。製品側の害
    （縮退結果に印が無く後段が材料にする）は IK-0715 に記録した。確定には、空白中に出た要求の作成時刻と打ち切りの時刻、頭脳の応答時刻を
    mailbox の req / res で突き合わせる。
generalization:
  level: general
  general_form: "応答者の交代や停止の空白が呼び出し側の打ち切りより長いと、呼び出しは縮退して先へ進み、遅れて届いた応答は誰にも使われない"
pattern: context-lost-across-execution-boundary
discovery:
  perspective: [symptom_report, data_inspection]
  note: "論文レーダー自律周回（2026-10-17・2610.08751）で、頭脳 Z1 が seq 3571（eq_tex_b102 = TCM 再パラメータ化の中心式）を作成から約 25 分後に答えたが、seq 3616 の component_assembly の材料ではその式が「Equation semantics could not be inferred / can_support_claim=false」のままだった（出所: uxsim/runs/radar_loop/BRAIN_NOTES_Z1.md）。頭脳 Z0 も seq 3573 の応答が同様に失われ、dsl_linking（3612）・振り分け（3627・3636）・二層説明（3651）で unknown のまま流れていることを確かめた（uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md）。前任 Y0〜Y2 が使用上限で停止してから Z 系が起動するまでの空白に出た要求で起きた。"
resolution:
  perspective: [pending]
  note: "未解決（運転の記録）。レーンの交代を前任の停止前に重ねて起動する、空白中の要求は打ち切りを越える前に検知して再試行を促す、縮退した要求の一覧を周回の後に出して再解析の対象にする、のいずれか。製品の打ち切りを周回のために延ばすことはしない。"
  landed_in:
    - "docs/features/paper_radar_autonomous_loop_design.md"
related: [IK-0715, IK-0726, IK-0716]
view_of: []
history: []
---

## 課題

症状: 頭脳の交代・使用上限で無人になった時間に出た要求（seq 3571・3573 ほか）は、作成から約 25 分後に応答が置かれたが、製品側はすでに打ち切りで縮退して
いた。2610.08751 の中心式 eq_tex_b102（TCM の再スケール）・eq_tex_b96・eq_tex_b78・CPL 式が「意味を推定できない」まま後段に流れ、振り分けで部品に入らず、
二層説明では失敗の文言が材料になった。
原因（仮説）: レーンの交代に空白があり、空白中の要求を後から拾い直す仕組みが無い。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_Z1.md（seq 3571・3616）、uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3573・3612・3627・3636・3651）。

## 発見の観点

頭脳の応答時刻と、後段の材料に残った縮退結果を突き合わせた（symptom_report / data_inspection）。

## 解決の観点

未解決。

## 一般化

応答者の空白が呼び出し側の打ち切りより長いと、遅れた応答は使われず縮退だけが残る。辞書の型 context-lost-across-execution-boundary。
