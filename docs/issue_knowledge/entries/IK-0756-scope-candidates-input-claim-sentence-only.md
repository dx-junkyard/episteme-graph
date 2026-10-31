---
id: IK-0756
title: "doubt:scope_candidates の LLM 入力が主張文 1 文だけで、前後の本文（条件・系・精度の記述）が渡らず、検証された条件を読めないまま低信頼の候補しか出ない"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/stakes_ledger_design.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "認識的地位台帳の主張に、検証された条件・領域・精度（検証スコープ）の候補を LLM で提案する"
  layers: [doubt_d]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: scope_candidates の input_builder が対象主張の本文（evidence_quote 相当の 1 文）だけを LLM に渡し、
    同じ主張の出典チャンク・前後の段落（検証された系・条件・精度を述べる文）は chunks / evidence_registry に
    あるのに入力へ配線されていない。接続軸 information = 上流にある周辺本文が scope 候補生成の段に届かない。
    処理軸 none = 渡された 1 文の扱い自体は誤っていない。構造軸 none（低確信）= 周辺本文の受け皿（chunk_id・
    block_id）は表現に既にある見込みだが、台帳行から出典チャンクへ辿れるかは未確認で、辿れないなら representation。
    統制軸 none = 回数・順序の問題ではない。併記: 対象ラベルが語の途中で切れる（'whe'）のは表示の切り詰めで、
    同じ入力構築の副症状か別の処理不良かは未確認。確定には scope_candidates の input_builder が組む入力と、
    台帳行（target_id）から chunk / evidence へ辿る経路の有無を読む。
generalization:
  level: general
  general_form: "条件や範囲を読み取らせる処理に、対象の文だけを渡して周辺の文脈を渡さないため、文脈にしか書かれていない条件を原理的に読めない"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 O2 が doubt:scope_candidates（seq 2285・2303・2306・AdS 論文の主張）に応答し、出典テキストが主張本文 1 文だけで周辺本文が渡らず、検証スコープは読めず主張の限定語を拾うしかないことに気付いた。seq 2306 では対象ラベルが語の途中（'whe'）で切れていた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_O2.md）。"
resolution:
  perspective: [pending]
  note: "未解決。対象主張の出典チャンク（または同節の前後段落）を上限付きの untrusted 区画として入力に足し、候補の evidence_quote はその範囲の逐語に限る案。ラベルは語境界で切る。"
  landed_in:
    - "docs/features/doubt_layer_issues.md"
related: [IK-0731]
view_of: []
history: []
---

## 課題

症状: 検証スコープ候補が 1 件ずつ・低信頼（system のみ / domain のみ）しか出ない。LLM に渡る出典テキストが主張本文 1 文だけで、
検証された条件・系・精度は前後の本文にしか書かれていないため、主張の限定語を拾う以外に手が無い。
併記: 対象ラベルが語の途中で切れている（'whe'）。

原因（仮説）: 周辺本文（出典チャンク・前後段落）は解析成果にあるが、scope_candidates の入力構築に配線されていない。

IK-0731（対象選別が背景文・問題提起文に偏る）とは別の課題。あちらは「どの主張を対象にするか」、こちらは「対象に何を材料として渡すか」の範囲。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_O2.md（seq 2285・2303・2306）。

追加の出所（2610.08751・頭脳 X1・是正前の run（seq 3289〜3310・2026-10-17 03:00〜03:11））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3304・3307 — 出典テキストが claim 本文 = atomic rewrite の言い換え 1 文（[S1] のみ）だけで、原文の段落（どの赤方偏移・どのモデルか）が渡らない）。親子の重複と脚注の対象化は IK-0816。

追加の出所（頭脳 X0・是正前の run（seq 3288〜3312））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3303・3309・3312 — 出典テキストが [S1] の主張本文だけで、元の段落・節・evidence が渡らず空が正解になる構造）。

追加の出所（頭脳 X2・是正前の run（seq 3290〜3314））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3305 — 出典テキストが主張本文 1 行だけで、何の簡略化か（IR 再和の補正）が渡らない。再解析 run の seq 3437 で、その主張が脚注由来で周辺文に条件があったことが分かった）。

## 発見の観点

LLM に渡った入力の範囲と、検証スコープが論文のどこに書かれているかを突き合わせた（data_inspection）。

## 解決の観点

未解決。台帳行から出典チャンクへ辿れるか（受け皿の有無）が分かれば、配線で足りるか表現の追加が要るかが決まる。

## 一般化

「条件・範囲を読む」処理に対象の文だけを渡す形は、反証条件候補（falsification_conditions）や他の候補生成でも再発しうる。
素材はあるが呼び出し側に配線されていない点で辞書の型 available-but-unwired。周辺本文の受け皿自体が無いと分かれば
information-dropped-as-unrepresentable に寄せる。
