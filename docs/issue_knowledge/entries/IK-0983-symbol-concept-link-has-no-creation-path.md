---
id: IK-0983
title: "学生の記号の吹き出しの「どの概念か」（concept_ref）は記号の同一性リンクの確定からしか出ないのに、記号を共通部品へ結ぶ経路が画面にも候補づくりにも無く、5 周とも 4 記号すべてで概念が出ない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生が教材の式の記号をタップし、その記号がどの概念に当たるかを確かめる"
  layers: [concept_registry, deliberation_w, learner_experience_b, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 target: core/symbol_lookup.py は symbol instance の confirmed な element_identity_links があるときだけ concept_ref を返す（P3-5）。しかし symbol は「同一性リンクの source としてだけ」の型で W層の要素型・対話セッションの対象ではなく（core/deliberation/schema.py ELEMENT_SYMBOL・routes/deliberation.py の 422）、候補づくり（identity_candidates）にも記号の規則が無い（規則 ④ は主張）。確定の装置（リンク・吹き出しの読み手）はあるが、そこへ至る作成の入口が無い。統制軸は none（誰が確定するかの問題ではなく、確定する対象が作られない）。
generalization:
  level: repo_pattern
  general_form: "確定済みの記録だけを読む表示を作ったのに、その記録を作る入口を作っておらず、表示がいつまでも空になる"
pattern: last-mile-missing
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    st-10 が r・P・F_EOS・L_BC の 4 記号をタップし概念が 1 つも出なかった（uxsim/runs/c-astro-r25-share-r5/20261027T071307Z-52079 seq7〜10・応答の concept_ref は全て null）。第 1〜4 周の (e) も同じ。concept_ref の供給元を逆に辿り、記号の同一性リンクを作る経路が無いことを確かめた。
resolution:
  perspective: [responsibility_move, carry_through]
  note: >-
    記号 → 概念の書き手を作った。規則 ⑥ core/library/symbol_concepts.py derive_symbol_concept_candidates: knowledge_symbols_live の定義の文（definition_evidence_texts）に、確定・公開中のエントリの name / alternate が text_mentions_alias で語として現れたら、記号（agent_symbol_id）からエントリへの candidate リンク（lexical_match・reason は記号と概念の名前・evidence は定義の文）。記号のような語・hidden は使わない。LLM 0 回・embedding 0 回・DELETE なし・1 document 20 本・1 記号 2 エントリ。入口は identity_candidates 段と POST /api/admin/library/entries/{id}/symbol-candidates/derive（確定済みだけ・閲覧できる document だけ）。ライブラリ詳細に「結びついた記号」区画（アンカー knowledge-library.bound-symbols / symbol-derive）と候補の行の［確認］［見送り］。describe_instance が記号の表記と定義の文を返す。確定は既存のリンクの確定の弁だけ（KR2）。
  landed_in:
    - "backend/core/library/symbol_concepts.py"
    - "backend/core/library/identity_candidates.py"
    - "backend/core/deliberation/identity_links.py"
    - "backend/api/routes/library.py"
    - "frontend/public/js/admin.js"
    - "backend/core/help_kb/admin_ui_anchors.py"
    - "docs/manual/teacher/19-admin-knowledge-library.md"
    - "backend/tests/test_share_round25_r5_fixes.py"
    - "backend/tests/test_concept_registry_identity_candidates.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実データで記号の候補が立ち、教員が確定し、凍結版のあるエントリで学生の吹き出しに概念名が出る通し"
      - "吹き出しで対応が無いことを言う事実文（学習画面・次の波）"
related: [IK-0922, IK-0961, IK-0920]
view_of: []
history: []
---

## 課題

症状: 吹き出しは定義の文・使用例・別論文の同字形を言い分けるようになったが、「この記号はどの概念か」は 5 周とも出ない（場面の目標が構造的に未達）。原因: 記号 → 共通部品のリンクを作る入口（教員の画面の操作・候補の規則）がどこにも無い。

## 発見の観点

再現（st-10 (e)）と供給経路の追跡。

## 解決の観点

是正案（オーナー判断を要しない範囲）: ① 候補づくりに記号の規則を足す — symbol_registry の定義の文に含まれる語が確定済みエントリの名前・別名と語境界一致（alias_matching）なら symbol → entry の candidate リンク（KR5・LLM 0 回）。② ライブラリのエントリ詳細に「記号を結ぶ」入口（論文と記号を選ぶ）を置き、確定は既存のリンク確定の弁を通す。③ それまでは吹き出しに「この記号と概念の対応は、まだ記録されていません」の事実文（空を黙らない）。

## 一般化

読み手だけ作って書き手を作らない型。確かめ方は、記号の候補が立ち、教員が 1 本確定すると学生の吹き出しに概念名が出ることを見ること。

## 第 5 波の是正（第 25 周・面 A、2026-10-27）

是正済み。記号 → 概念の書き手を作った。規則 ⑥ core/library/symbol_concepts.py derive_symbol_concept_candidates: knowledge_symbols_live の定義の文（definition_evidence_texts）に、確定・公開中のエントリの name / alternate が text_mentions_alias で語として現れたら、記号（agent_symbol_id）からエントリへの candidate リンク（lexical_match・reason は記号と概念の名前・evidence は定義の文）。記号のような語・hidden は使わない。LLM 0 回・embedding 0 回・DELETE なし・1 document 20 本・1 記号 2 エントリ。入口は identity_candidates 段と POST /api/admin/library/entries/{id}/symbol-candidates/derive（確定済みだけ・閲覧できる document だけ）。ライブラリ詳細に「結びついた記号」区画（アンカー knowledge-library.bound-symbols / symbol-derive）と候補の行の［確認］［見送り］。describe_instance が記号の表記と定義の文を返す。確定は既存のリンクの確定の弁だけ（KR2）。

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: 教員側の入口は GONE（エントリ詳細に「結びついた記号」区画と［記号の候補を探す］。候補のエントリでは押せず「確定した概念にだけ、記号の候補を探します。」）。押されず候補づくりの結果は未評価。学生側は STILL: st-01 が r・P・F_EOS・L_BC をタップして概念名 0（確定・凍結の記号リンクが砂場に無い）。F_EOS は照合キーの不備で使用例まで消えた → IK-1022。run: uxsim/runs/c-astro-r25-share-r6/20261027T122225Z-3503 seq4、uxsim/runs/c-astro-r25-share-r6/20261027T122544Z-5649 seq3、uxsim/runs/c-astro-r25-share-r6/20261027T123326Z-10141 seq7-10

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: 学生側は未評価（前提の欠けの形が変わった）。前提②で確定・凍結した記号リンクは PDF 版の F_EOS で、学生の教材（TeX 版）の F_EOS には候補も無く、吹き出しの concept_ref は null（uxsim/runs/c-astro-r25-share-r7/20261027T172824Z-59013 seq8）。しかもその確定は概念として誤った組（F_EOS ⇄ TOV residual loss）→ NEW IK-1052。教員側は te-05 の詳細に「結びついた記号」の確定・候補の行が出た（seq5）。

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

未評価（学生側）。te-03 の (c) の後に TeX 版の記号候補が残っておらず（前提スクリプトの自動確定を取り消した行が revoked で残り、同じ組は立て直せない — IK-1062）、指揮者は SYMBOL_SKIP_R8。さらに st-04 の固定コース 6be229e1 は PDF 版の論文なので、TeX 版の記号を確定しても吹き出しには出ない組だった（H48）。吹き出しの concept_ref は 3 記号とも null（uxsim/runs/c-astro-r25-share-r8/20261027T224425Z-99386 seq7〜10）。

## 第 9 周の再演（第 25 周・面 A 第 9 周、2026-10-31・judge_share_r9）

未評価（3 周目・前提未成立）。記号のリンクは 5 本とも〔確定を取り消し〕（第 7・8 周の前提の自動確定の後始末）で、詳細には［候補に戻す］が出るが、場面 (c) に記号の戻す手が無かった（harness H54）。［記号の候補を探す］は「閲覧できる教材の記号の定義の文に、この概念の名前・別名は語として現れませんでした。」— 足した別名でも新しい候補なし。st-04 の 4 記号の吹き出しに概念名は無い。run: uxsim/runs/c-astro-r25-share-r9/20261031T015651Z-40470（seq3・9）・uxsim/runs/c-astro-r25-share-r9/20261031T020038Z-42487（seq7〜10）。

## 第 10 周の再演（第 25 周・面 A 第 10 周、2026-10-31・judge_share_r10）

未評価（4 周目・前提未成立）。場面 (c) に記号の［候補に戻す］→ 確認 → 凍結の手は入ったが、材料 `entry_hint: TOV` が 2 つのエントリに当たり、te-03 は記号 5 本の付いていない「TOV 方程式（静水圧平衡）」を開いた（harness H59）。結びついた記号は「まだありません」で手は対象なしで skip、凍結は確認を読んでキャンセル。st-04 の 4 記号の吹き出しに概念名は無い。run: uxsim/runs/c-astro-r25-share-r10/20261031T052922Z-65582（seq3・8・9）・uxsim/runs/c-astro-r25-share-r10/20261031T053407Z-68377（seq7〜10）。

## 第 11 周の再演（第 25 周・面 A 第 11 周、2026-10-31・judge_share_r11）

未評価（5 周目）。te-03 の (c) が初めて記号を［候補に戻す］→［確認］→ 凍結版 6 まで通したが、場面の材料 link_hint「TeX 版の「L_TOV」」が runner の部分一致で PDF 版の L_TOV に当たり（harness H66）、教材（TeX 版）の L_TOV は取り消し済みのまま。st-04 の TeX 版 L_TOV の吹き出しは concept_ref: null（正しい）（uxsim/runs/c-astro-r25-share-r11/20261031T101202Z-98535 seq9〜11・uxsim/runs/c-astro-r25-share-r11/20261031T101956Z-2094 seq8）。
