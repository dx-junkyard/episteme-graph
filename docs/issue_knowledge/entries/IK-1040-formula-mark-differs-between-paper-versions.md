---
id: IK-1040
title: "同じ式 (1)〜(4) が、学習チャットの文脈の表示中の教材では印なし（原文）、出典の抜粋では［AI復元］と食い違う — 教材の式は TeX 版の記録、抜粋は PDF 版のチャンクの記録で印を判定し、判定の材料が版ごとに違う"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22"
  - "docs/features/focus_document_design.md"
  - "docs/backend/rag-chat.md"
feature_context:
  realizing: "学生がチャットで式の出所（原文か AI の復元か）を尋ね、教材と出典で同じ答えを受け取る"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version]
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
    仮説: 接続軸 version — 印の判定は 1 つ（equation_latex_is_reconstructed）だが、材料が版ごとの記録で、PDF 版のチャンクの式は原文の
    LaTeX が崩れているので復元と判定され、TeX 版の記録（教材の content_blocks）は原文と一致して印なしになる。復元の LaTeX は TeX 版の
    原文と同じ（mailbox req-04248 の [出典5] の L_TOV と教材の L_TOV が一致）。確定には、教材の式の記録の document と [出典5] の
    chunk の document が同じ論文の TeX 版・PDF 版であることを砂場の DB で見ることが要る。
generalization:
  level: repo_pattern
  general_form: "同じ判定規則を同じ対象の別の写しの記録に当てるが、写しごとに判定の材料が違うため、同じ対象の判定が食い違う"
pattern: id-not-stable-across-versions
discovery:
  perspective: [data_inspection, reproduction]
  note: >-
    製品頭脳 A 4248 / B 4251 で、教材の「この箇所の式: 式 (4)、式 (2)、式 (1)、式 (3)」と [出典5] の「式 (1)［AI復元］…式 (4)［AI復元］」が
    並んだ。回答は食い違いを学生に伝え「安全側に読むなら復元の可能性がある」とした。IK-1009 の是正（式単位化）の後に残った。
resolution:
  perspective: [canonical_source, carry_through]
  note: >-
    IK-0895 の規則（原文と一致する復元には印を付けない）を版またぎに広げた。services.paper_original_formula_latex がスコープ内の
    同じ論文の別の版のチャンクから印の付かない式の LaTeX を集め、formula_fact_line(confirmed_latex=) が一致する復元式の印を外す。
    出典の抜粋（confirm_formula_facts_across_versions）と表示中の教材（_topic_formula_fact_line）の両方が同じ材料を使う。
    一致の判定は reconstruction_matches_source の 1 つ。
  landed_in:
    - "backend/core/formula_facts.py"
    - "backend/api/services.py"
    - "backend/api/routes/learning.py"
    - "backend/tests/test_round25_wave6_chat_rag.py"
    - "docs/backend/rag-chat.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "上流の損失（PDF 版の原文の崩れ）を下流で読み替えず、判定の材料（同じ論文の原文）を揃えた。固有の差分: 材料はスコープ内の版に限り、無ければ印は従来どおり（情報を落とさない側）。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の DB で、教材の式と [出典5] の式の document が同じ論文の別の版であること"
      - "照合の SQL（同じ論文の別の版のチャンクの式を読む）が往復の応答時間に与える影響"
related: [IK-1009, IK-0997, IK-0895, IK-0996]
view_of: []
history: []
---

## 課題

症状: 同じ式番号が教材では原文、出典では復元と言われ、AI も学生もどちらを信じるかを決められない。原因（仮説）: 版ごとの記録で印を判定していた。

## 発見の観点

製品頭脳に渡ったプロンプトの点検と、学生の回答（食い違いを伝えた）の突き合わせ。

## 解決の観点

判定の材料を揃える（同じ論文の原文の式で照合する）。印の規則そのものは変えない。教材の側を PDF 版に合わせて印を付ける案は、原文と照合できている式に印を付けることになるので採らなかった。

## 一般化

写しごとの材料の違いで判定が割れる型。確かめ方: TeX 版と PDF 版を取り込み、PDF 版の抜粋の式が TeX 版の原文と一致するとき印が外れ、一致しない式だけに残ることを見る。
