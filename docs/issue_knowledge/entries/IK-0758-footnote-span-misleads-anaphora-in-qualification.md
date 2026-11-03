---
id: IK-0758
title: "claim_qualification の span 列に脚注が独立 span として挟まり、Previous Span が脚注になって代名詞（This）の照応先を脚注に誘導する"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/features/url_material_upload_design.md"
feature_context:
  realizing: "主張の採否で、前後の span を文脈として渡し、代名詞の照応を本文の正しい先行文に解決させる"
  layers: [pipeline_a, url_material_fetch]
classification:
  axes:
    processing: [none]
    structure: [decomposition]
    connection: [information]
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
    仮説: TeX 取り込みで脚注本文が本文と同列の span として span 列に入り、claim_qualification が「直前の span」を文脈として渡すとき、
    本文の先行文ではなく脚注が Previous Span になる（seq 2381: tex_b26 span_004 の 'This' の正しい先行文は孤立立方体に置く本文だが、
    Previous Span は L の近似を述べる脚注）。構造軸 decomposition = 本文の流れと脚注を分けずに 1 列の span に切っている。
    接続軸 information = 照応に要る先行文が後段へ渡らない。処理軸 none（確信度 medium: 判定そのものは正しく受理されている）。
    IK-0713（脚注だけの span が主張候補に入る）とは害が別で、こちらは脚注が本文 span の文脈を置き換える照応の誤り。
    型は文脈の単位（何を「前の span」とするか）が定義されていない unit-of-work-undefined と読んだ（information-dropped-as-unrepresentable
    の読みも残る）。併記: 同じ論文の seq 2495 で、式の後続ブロックが caption の無い表 3 つで、どの係数（counterterm）の表か材料から
    読めない。seq 2372 でも記号定義 span の直前の式ブロックが Previous Span に無く、どの式の記号か読めない（文脈の単位が本文 span だけ）。
    確定には、claim_qualification の input builder が Previous/Next Span をどう選ぶか（脚注・表・式ブロックの扱い）を読む。
generalization:
  level: general
  general_form: "文脈として渡す『前後の単位』の定義が本文以外の付随物（脚注・表・式）を区別しないため、文脈が付随物に置き換わり、あるいは必要な付随物が落ちる"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 P2 が claim_qualification（seq 2381・tex_b26 span_004）の入力を読み、Previous Span が脚注で照応 'This' の先行文が渡っていないことに気付いた。併せて seq 2372（式ブロックが文脈に無い）・2399 / 2471（前の式ブロック無し）・2495（caption の無い表 3 つ）を記録（出所: uxsim/runs/radar_loop/BRAIN_NOTES_P2.md）。"
resolution:
  perspective: [pending]
  note: "未解決。span 列から脚注を本文の流れと分け（脚注は参照元の span に付随情報として添える）、Previous/Next Span は本文 span から選ぶ。式ブロック・表は役割付きの付随文脈として別区画で渡し、caption の無い表は本文の参照と結べないことを事実として残す。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0713, IK-0698]
view_of: []
history: []
---

## 課題

症状: claim_qualification の tex_b26 span_004 で、照応 'This' の正しい先行文（本文）ではなく脚注（L の近似）が Previous Span として渡る。
受理判定自体は正しかったが、脚注が照応先に見える文脈になっている。
原因（仮説）: 脚注が本文と同列の span として span 列に挟まり、前後 span の選び方が本文と付随物を区別しない。
併記: seq 2495 で後続ブロックが caption の無い表 3 つで、どの counterterm の表か読めない。seq 2372 で記号定義 span の直前の式ブロックが文脈に無い。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_P2.md（seq 2381 / 2372 / 2399 / 2471 / 2495）。

## 発見の観点

実際のステージ入力の Previous/Next Span と本文の流れを突き合わせた（data_inspection）。

## 解決の観点

未解決。脚注を本文の span 列から外して参照元に添え、式・表は付随文脈として別区画で渡す。

## 一般化

文脈の単位を定義しないと付随物が文脈を置き換える。辞書の型 unit-of-work-undefined（IK-0713 の「脚注が候補に入る」とは別の害）。
