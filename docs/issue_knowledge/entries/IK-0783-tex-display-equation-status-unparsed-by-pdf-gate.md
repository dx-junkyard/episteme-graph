---
id: IK-0783
title: "TeX 経路の表示式は原文が完全なのに、equation_semantics の受理ゲートが PDF 向けの字面判定（関係記号・式本体の正規表現）で extraction_status=unparsed を付け、再構成が要る式として扱われる"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "TeX 原文から取り込んだ表示式を、抽出の確かさ（extraction_status）付きで意味役割の判定に渡す"
  layers: [pipeline_a]
classification:
  axes:
    processing: [logic]
    structure: [none]
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
    仮説: input_builder は候補を extraction_status="unparsed" で作り、acceptance_gate が字面（_EQUATION_BODY_RE / _RELATION_RE）で
    complete / partial / unparsed を上書きする。この判定は PDF 文字層の壊れた式を見分けるためのもので、TeX 原文由来
    （parser_source=tex_archive）でも同じ規則が掛かるため、関係記号を含まない表示式（展開式・微分の counterterm 等）が unparsed に
    なる。処理軸 logic = 状態判定の条件が経路を見ていない。接続軸 information = 「TeX 原文由来で完全」という事実がゲートに
    届いていない（IK-0764 で inline 候補には tex_source を伝えたが、表示式の受理ゲートには伝わっていないと読める）。構造軸 none。
    統制軸 none。確定には、eq_tex_b99・eq_eqn_pairwise の候補の source_location と、ゲート通過前後の extraction_status を
    該当 run の artifact で確かめる。
generalization:
  level: repo_pattern
  general_form: "壊れた入力を見分けるための判定を、損失の無い経路の入力にも同じ条件で掛け、完全な入力に「未解析」の印を付ける"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で、2609.35757（TeX 経路）の equation_semantics 入力を頭脳 S1（seq 2839）と S2（seq 2864）が読み、extraction_status=unparsed なのに TeX 本文は完全で、方針（TeX trusted）と状態の印が食い違うことを見た。出所: uxsim/runs/radar_loop/BRAIN_NOTES_S1.md・uxsim/runs/radar_loop/BRAIN_NOTES_S2.md。"
resolution:
  perspective: [pending]
  note: "未解決。TeX 原文由来の表示式は受理ゲートの字面判定を通さず complete（原文境界で切り出した事実）とし、字面判定は PDF 文字層の候補だけに掛ける。IK-0764 で inline 候補に採った上流の派生形を表示式にも揃える。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0764, IK-0711]
view_of: []
history: []
---

## 課題

症状: equation_semantics の入力で、TeX 本文が完全な表示式（eq_eqn_pairwise・eq_tex_b99）に extraction_status=unparsed が付く。
LLM には「TeX は trusted」の方針と「未解析」の印が同時に渡り、partial_extraction 等の誤った印を付けさせる。

原因（仮説）: 受理ゲートの字面判定が経路（TeX 原文 / PDF 文字層）を見ずに掛かる。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_S1.md（seq 2839）、uxsim/runs/radar_loop/BRAIN_NOTES_S2.md（seq 2864）。

追加の出所（頭脳 S0）: uxsim/runs/radar_loop/BRAIN_NOTES_S0.md（seq 2868 — eq_eqn_cfog が extraction_status=unparsed だが式本文は完全）。

追加の出所（2610.08751・頭脳 W0）: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md（seq 3249 — extraction_status=unparsed なのに式本文は完全）。

追加の出所（2610.08751・頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3256・3262 — \supset や \rightarrow を含む完全な式が extraction_status=unparsed になる。関係記号の字面判定が等号以外の記号を扱えない傾向）。下流の可能性（seq 3289）: TeX 由来で source_backed の式（eq_tex_b37 等）が component_graph の review_required_equation_ids に入り has_review_required=true になる。unparsed の印が review_required に流れている疑いで、別原因なら別エントリにする。

追加の出所（頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3257 — eq_noise_terms が extraction_status=unparsed なのに式本文は完全な TeX（\supset・\left[ … \right]））。

残存の確認（頭脳 Y1/Y2・再解析 run 40ecf8df）: uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md（seq 3529 — eq_tex_b41 が unparsed、b37 は complete。seq 3542 — \supset を含む eq_noise_terms が unparsed、eq_Pgg・eq_post_laplace は complete。seq 3524 — TeX 経路でも developer 文に「PDF-extracted math は untrusted」が常に入り、user 側の TeX 方針と矛盾した指示が併置される）。

追加の出所（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3621 — TeX 由来・source_backed の eq_tex_b41 の reason に「not treated as source-extracted; downstream use is semantic_hint_only」と PDF 復元用の印が付く）。

残存の確認（頭脳 AA2・2609.35757 の再解析 run 54e85a2a）: uxsim/runs/radar_loop/BRAIN_NOTES_AA2.md（seq 3845 — TeX 経路で式本体が完全な eq_eqn_pairwise が extraction_status=unparsed）。

## 発見の観点

式の意味付けの入力で、状態の印と原文の完全さを突き合わせた（data_inspection）。

## 解決の観点

未解決。経路の事実をゲートに渡し、TeX 原文由来の式には字面判定を掛けない。

## 一般化

損失のある経路のための判定を損失の無い経路にも掛けると、完全な入力に不確かさの印が付く。確かめ方は、関係記号を含まない表示式を
含む TeX を通し、extraction_status が complete になることと、PDF 経路の壊れた式では従来どおり unparsed / partial になることを見る。
辞書の型 available-but-unwired（IK-0764 と同じ）。
