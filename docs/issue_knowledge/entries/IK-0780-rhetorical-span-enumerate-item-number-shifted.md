---
id: IK-0780
title: "役割判定の span 境界が列挙（enumerate）の項目番号の 1 つ手前でずれ、各 span の末尾に次項目の番号「2.」「3.」が付き、次の span は番号を失って始まる"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/extraction-units.md"
feature_context:
  realizing: "列挙で並ぶ性質（パリティ不変性・視線反転・確率保存など）を項目ごとの span に分け、項目単位で主張の採否と atomic 化を判定する"
  layers: [pipeline_a]
classification:
  axes:
    processing: [unknown]
    structure: [decomposition]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: low
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 列挙ブロック自体は正しく取り込まれている（同じ tex_b79 を equation_semantics の Text Blocks で見ると項目番号は行単位で
    正しい位置にある）。ずれは rhetorical_role の span 分割の段で起き、「… W(0)=1. 4. …」の「4.」を文末の一部とみなして直前の
    span に含めている。構造軸 decomposition = 列挙の項目番号が「次の単位の頭」なのか「前の文の終わり」なのかが分割の規則に
    定義されていない。処理軸 unknown = LLM が返した境界がずれているのか、サーバ側の resolve_span_offsets が文末（「. 」の後）へ
    寄せているのかを確かめていない。確定には、当該ブロックの rhetorical_role の LLM 生出力の span と、オフセット解決後の span を
    比べる。接続軸 none = 後段は受け取った span をそのまま使う。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "番号付きの並びを区切るとき、項目番号を前の項目の終わりと次の項目の頭のどちらに属させるかを決めずに境界を引き、各単位に隣の項目の番号が付く"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で、2609.35757（TeX 経路）の claim_qualification 入力を頭脳 S1（seq 2755・2758・2761）と S2（seq 2756・2759）が読み、tex_b79 の各 span 末尾に次項目の番号が付き Next Span の冒頭に番号が無いことを見た。同じブロックを equation_semantics の材料で読むと項目番号は正しく区切れていた（S1 seq 2860・S2 seq 2861）ので、取り込みではなく span 分割の段と切り分けた。出所: uxsim/runs/radar_loop/BRAIN_NOTES_S1.md・uxsim/runs/radar_loop/BRAIN_NOTES_S2.md。"
resolution:
  perspective: [pending]
  note: "未解決。案は、span の末尾が孤立した項目番号（行頭の「N.」相当）で終わるときにその番号を次の span の頭へ決定論で移す、または取り込みで列挙項目を項目ごとの区切り（改行・マーカー）付きで残して分割の手掛かりにする。どちらも LLM 呼び出しの回数は変えない。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0712, IK-0781]
view_of: []
history: []
---

## 課題

症状: 列挙ブロック tex_b79 の claim_qualification 入力で、span_001 の末尾に「2.」、span_002 の末尾に「3.」、span_003 の末尾に
「4.」が付き、次の span は番号の無い本文から始まる。項目の主張そのものは読めるが、逐語の evidence_quote に次項目の番号が混ざる。

原因（仮説）: 取り込みは正しく、rhetorical_role の span 境界が項目番号の扱いを決めていない。LLM の境界かオフセット解決の寄せかは未確認。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_S1.md（seq 2755・2758・2761）、uxsim/runs/radar_loop/BRAIN_NOTES_S2.md（seq 2756・2759・2861）。

追加の出所（頭脳 S0）: uxsim/runs/radar_loop/BRAIN_NOTES_S0.md（seq 2757・2760 — tex_b79 の項目 1 の末尾に「2.」、項目 2 の span が番号無しで始まる）。

追加の出所（2609.35757・TeX 経路・頭脳 T1）: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2923 — tex_b79 の inline 式の span で次項目の「\n2.」「\n3.」「\n4.」が前の span 末尾に付く）。

追加の出所（頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2931 — thesis_reconstruction の材料で claim:tex_b79:span_002 の末尾に「3.」、claim_span_001_30 の末尾に「2.」の列挙番号の残骸）。

解消の確認（頭脳 Z2・2609.35757 の再解析 run 54e85a2a と 2610.08751 の再解析 run の後段）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3626・3638 — TeX の列挙番号「1.」〜「4.」が本文に保たれ、項目ごとに span が切れる。前の span の末尾に次の番号が付く症状はこの run の材料には無い）。

解消の確認（頭脳 AA2・2609.35757 の再解析 run 54e85a2a）: uxsim/runs/radar_loop/BRAIN_NOTES_AA2.md（再解析 run の claim_qualification で列挙番号のずれの記録なし。頭脳 Z2 の seq 3626・3638 と合わせて、列挙番号が保たれる観察が続く）。

## 発見の観点

同じブロックを 2 つのステージの材料で読み比べ、片方（equation_semantics）では正しく片方（claim_qualification）でずれることを見た（data_inspection）。

## 解決の観点

未解決。LLM の生出力とオフセット解決後を比べて、ずれを作る段を確定してから直す。

## 一般化

番号付きの並びの区切りで番号の帰属を決めないと、各単位に隣の番号が付く。確かめ方は、列挙を含む TeX を通し、各 span が自分の
項目番号で始まり次項目の番号で終わらないことを見る。辞書の型 unit-of-work-undefined（IK-0712 と同じ段の別の境界の問題）。
