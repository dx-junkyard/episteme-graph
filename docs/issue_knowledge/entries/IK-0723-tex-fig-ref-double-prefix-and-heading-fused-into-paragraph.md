---
id: IK-0723
title: "TeX 取り込みで参照が「Fig 図 1」と二重接頭辞になり、段落末尾に次節の見出し（\\paragraph の「-1PN Expression:」等）が融合する"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/features/url_material_upload_design.md"
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "TeX ソースの本文を平文化し、参照と見出しを読める形・正しい段落境界で後段に渡す"
  layers: [pipeline_a, url_material_fetch]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定（2026-10-17・tex_archive._render_one_ref を読んだ）: 直前の種類語の判定が「Secs?.」「Figs?.」「Tabs?.」のピリオド付きしか見ず、
    「Sect.」「Fig」（ピリオドなし）を種類語と読まなかった。加えて \cref / \Cref / \autoref は直前の語に関わらず常に種類名を付けていた
    （2610.08751 の「Sect.~\cref{sec:…}」→「Sect. Sec. 3」が 31 箇所）。\paragraph の融合（②）はこの是正の範囲外（再解析 run で再発の報告なし）。
    以下は起票時の仮説: ①参照の置換は直前が「Fig.」（ピリオド付き）なら番号だけにするが、「Fig」（ピリオドなし）や言語判定の残り（round 4）で
    「図 1」をそのまま足し、「see Fig 図 1」になる。②\paragraph{...} の見出しを段落境界として切らず、直前の段落テキストに連結している
    （tex_b851 末尾の「-1PN Expression:」・tex_b871 末尾の「0PN Expressions:」）。処理軸 input_handling = TeX コマンドの平文化の
    境界条件の不良。構造軸 none / 接続軸 none（見出しを表す受け皿・参照の置き先はある）/ 統制軸 none。確定には、
    backend/core/document_pipeline/tex_archive.py の参照置換の直前語判定と \paragraph の扱いを読む。
generalization:
  level: repo_pattern
  general_form: "形式変換の置換規則が直前の語の表記ゆれや見出しコマンドの境界を取りこぼし、本文に重複語や融合した見出しが残る"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 L0 が equation_semantics（seq 1884・1908・2001）の文脈窓を読み、見出しの融合と「Fig 図 1」に気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_L0.md）。"
resolution:
  perspective: [single_point_fix, pending]
  note: "直前語の判定を Sect./Sects./ピリオドなしの Sec・Fig・Tab・Eq に広げ、\\cref / \\Cref / \\autoref も直前（と並びの先頭）の語が同じ種類を言っていれば番号だけを置く（番号の正本は従来どおり label の印字番号）。原本 4 本で二重接頭辞 33 → 0（2610.08751 = 31・2610.08908 = 2）、他の参照の出力は不変。"
  landed_in:
    - "backend/core/document_pipeline/tex_archive.py"
    - "backend/tests/test_latex_display.py"
related: [IK-0697, IK-0696]
view_of: []
history:
  - date: '2026-10-17'
    field: status
    from: "resolved（resolved_at 2026-10-17・resolution.perspective=[single_point_fix, guardrail_fix]・verification methods=[guardrail, reproduction_rerun]・unverified=[\\paragraph の見出しの融合（起票時の②）は扱っていない / パイプラインの再解析（DB 上の run）は行っていない — 原本 4 本の取り込みをローカルで読み直したのみ]）"
    to: open
    reason: 第 2 波 deploy 後の再解析 run でも tex_b32・tex_b44・tex_b71 の脚注に「Sect. Sec. N」が残った（頭脳 Z0 seq 3573・3579・3588）。是正の規則（_render_one_ref の直前語の判定）に当たらない形の参照があり再発として開き直す。
---

## 課題

症状: 本文に「see Fig 図 1」が現れ、段落末尾に次節の見出し（「-1PN Expression:」「0PN Expressions:」）が連結される。
原因（仮説）: 参照置換の直前語判定がピリオドなしの「Fig」を拾わない（言語判定の残り）こと、`\paragraph` を段落境界として切っていないこと。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_L0.md（seq 1884・1908・2001）。

追加の出所（2610.08751・頭脳 W0）: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md（seq 3141・3162・3189・3231・3264 — 参照が「Sect. Sec. 3」と二重接頭辞になり、主張本文にも残る）。TeX マクロ是正の前に取り込んだ素材。

追加の出所（頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3160・3187・3226 — 「Sect. Sec. 4」「Sect. Sec. 2.2」の二重接頭辞）。

追加の出所（頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3161・3188 — 「Sect. Sec. 4」「Sect. Sec. 4.2」の重複）。

再発確認（頭脳 X1・全是正入りの再解析 run 40ecf8df（seq 3313 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3313・3355 — 是正後の run でも「Sect. Sec. 2.1」「Sect. Sec. 4.1」の二重表記が残る。TeX マクロ是正（IK-0804〜0806）の対象外の参照展開の残骸）。

未是正の確認（頭脳 X0・全是正入りの再解析 run 40ecf8df（seq 3315 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3324・3363・3399 — 「Sect. Sec. 2.1 / 2.2」の二重化が残る）。

再発確認（頭脳 X2・再解析 run 40ecf8df（seq 3317 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3323 — 「In Sect. Sec. 2」の重複が再解析でも残る）。

残存の確認（頭脳 Y0/Y1/Y2・再解析 run 40ecf8df）（この是正がその run の取り込みより後に入ったかは照合していない）: uxsim/runs/radar_loop/BRAIN_NOTES_Y0.md（seq 3426・3450・3471・3495・3510）、uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md（seq 3427・3448・3469・3511・3449・3467・3494 — 「Sect. Sec. N」の二重化が claim_qualification の材料に残る）。

再発（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3573 — tex_b32 に「Sect. Sec. 2」、seq 3579 — tex_b44 の脚注に「Sect. Sec. 3」、seq 3588 — tex_b71 の脚注に「Sect. Sec. 4」）。是正（直前語の判定の拡張）の後の run でも残るので、_render_one_ref の規則に当たらない参照の書き方（脚注の中・\cref 系の別の形）がある。旧 run 40ecf8df の残存（頭脳 Y0/Y1/Y2）も同じ症状。

## 発見の観点

実際の文脈窓の本文を読んだ（data_inspection）。

## 解決の観点

2026-10-17: 直前語の判定を表記ゆれ（Sect. / ピリオドなし）に広げ、\cref 系にも同じ規則を効かせた（tex_archive._render_one_ref）。原本 4 本（1471ff7f / c795b2cf / a8d731be / 842bd9cb）の取り込みを読み直し、「Sect. Sec. N」「Fig Fig. N」が消えたこと・他の参照の出力が変わらないことを見た。\paragraph の融合は扱っていない。

## 一般化

形式変換の境界条件の漏れは、本文の読みやすさと段落単位の判定を同時に崩す。IK-0697（参照の削除）・IK-0696（レイアウト残骸）と同じ取り込み段の課題。
