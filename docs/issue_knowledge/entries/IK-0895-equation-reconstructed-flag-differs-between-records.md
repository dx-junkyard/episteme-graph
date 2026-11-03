---
id: IK-0895
title: "TeX 経路の教材で、同じ式が教材の「この節で使う数式」では原文の式として出るのに、部品の文脈では「AI が文脈から復元した式です（原文の数式とは未照合）」と添えられる — 同じ式の表示式ブロックと本文中の式の候補が別々の式の記録を持ち、復元の印をそれぞれの記録の reconstruction.status で付けるため、画面ごとに式の確かめられ方が食い違う"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生が教材と部品の文脈で同じ式を読み、それが原文の式か AI の復元かを確かめる"
  layers: [learner_experience_b, pipeline_a, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
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
    仮説: 部品 EOS feature vector の式は eq_eqcand_inline_tex_b14_383_…（本文中の式の候補）で、learner_context_common.equation_latex_is_reconstructed（backend/core/learner_context_common.py:398）は reconstruction.status が none 以外で復元本文があれば復元と判定する。教材の数式一覧は chunks.formulas[].reconstructed（course_content_builder._equation_is_reconstructed :4116 と同じ規則）を使うが、表示されている式は別の記録（表示式ブロック）由来で reconstructed が付かない。構造軸 representation: 同じ数式が式ブロックと本文中の候補の 2 つの記録になり、どちらが正本かを持たない。接続軸 meaning: TeX 経路では原文に LaTeX があるので、equation_semantics が reconstruction を付けても「原文と未照合」とは限らないが、判定は取り込み形式を見ない（IK-0870 の但し書きと同じ形）。確定には採用 run の equation_semantics artifact で eq_eqcand_inline_tex_b14_383_… と同じ式の表示式ブロックの記録の reconstruction.status を比べ、TeX 経路の inline 候補で status が付く条件を読めばよい。
generalization:
  level: repo_pattern
  general_form: "同じ対象が抽出経路の違いで 2 つの記録になり、属性（真正性の印）を記録ごとに判定するため、画面ごとに印が付いたり付かなかったりする"
pattern: duplicate-canonical-sources
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    学生 st-06 が隣の部品へ移ると、教材では通常表示の F_EOS の式に部品の文脈では「AI が文脈から復元した式です」が付いていた。同じコースの別の版（6be229e1）でも「式 (4)」「式 (2)」が両方復元扱いで中身が無かった（st-09）。run: uxsim/runs/c-astro-r25-share-r2/20261022T220149Z-75546 seq19・20261022T220528Z-78210 seq7。
resolution:
  perspective: [canonical_source]
  note: >-
    復元の印の判定の正本を learner_context_common.equation_latex_is_reconstructed に 1 つ置き、復元本文が原文の LaTeX（source_extraction.latex）と正規化後（空白・間隔命令・\left/\right・\label・末尾の句読点を除く）に一致するなら印を付けない（reconstruction_matches_source）。course_content_builder._equation_is_reconstructed は委譲し、パイプラインの chunks.formulas の reconstructed も同じ判定を使う（新しい run から）。式ブロックと inline 候補の読み時の同一視（是正案②）は見送った。
  landed_in:
    - "backend/core/learner_context_common.py"
    - "backend/core/course_content_builder.py"
    - "backend/core/document_pipeline/persistence.py"
    - "backend/tests/test_share_round25_r2_fixes.py"
    - "backend/core/lecture.py"
    - "backend/tests/test_lecture_studio_round25_r5.py"
    - "backend/core/equation_reconstruction.py"
    - "backend/api/services.py"
    - "backend/api/routes/lecture.py"
    - "backend/api/routes/learning.py"
    - "backend/core/component_context.py"
    - "backend/core/formula_facts.py"
    - "backend/tests/test_lecture_studio_round25_r6.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "本文・付録・講義・チャット・部品の文脈の 5 か所が、生きた式の記録の索引（EquationReconstructionIndex）と 1 関数 equation_is_reconstructed を通る。固有の差分: 写し（凍結の content_blocks・根拠リンク）は書き換えず読み時に引く（保存物を書き直さない — オーナー判断 1）。"
  verification:
    methods: [guardrail]
    unverified:
      - "固定コース 6be229e1 の t11（eq_4 / eq_2）・t16（作用の式 S）で 5 か所の印が一致するかは実機で見ていない（第 7 周 st-07 の場面）"
      - "索引は採用 run の equation_semantics artifact と chunks.formulas の平坦形から引く。平坦形に原文の LaTeX が無い旧い run の判定そのものは再解析まで変わらない"
      - "論文が分からず答えが割れる鍵（同じ id が別の論文にもある）は索引で引かず、写し自身の判定に戻る"
      - "式ブロックと inline 候補の同一視（是正案②）は未実装"
related: [IK-0870, IK-0681, IK-0443]
view_of: []
history: []
---

## 課題

症状: 同じ式が教材では原文の式、部品の文脈では AI の復元として出る。原因（仮説）: 同じ数式が別々の式の記録になり、復元の印を
記録ごとに、取り込み形式を見ずに付ける。

## 発見の観点

学生ペルソナの通し受講（reproduction）。教材の数式一覧と部品の文脈の式の記録を並べた（data_inspection）。

## 解決の観点

第 2 波（2026-10-23）で是正。復元の判定を 1 つの正本にし、TeX 経路で原文と一致する式には印を付けない。

## 一般化

抽出経路の違いで同じ対象が 2 つの記録になり、属性が食い違う型。確かめ方は、TeX 経路の教材で同じ式を教材・部品の文脈・要素の
文脈の 3 画面で開き、復元の印が一致することを見ること。

第 4 周の再演: 残存（unverified に書いた範囲）。固定コース 6be229e1 の t11 で、学習チャットは表示中トピックの出典チャンクの式（chunks.formulas の reconstructed — 旧い判定のまま）から L_TOV の式を「AI が文脈から復元した式で原文と未照合」と言い、同じ式を教材・講義・記号の吹き出しは印なしで出した。学生 st-06 はここで諦めた。画面ごとに印が違う直接の原因は、式の写しへ印が運ばれないこと（IK-0977 に分けた）。run: uxsim/runs/c-astro-r25-studio-r4/20261024T234308Z-52347 seq13・15。

第 4 波で是正（2026-10-25）: 旧い run の平坦な記録（chunks.formulas の `reconstructed` だけ）にも判定の正本を効かせた — `equation_latex_is_reconstructed` は平坦形でも表示の LaTeX が原文の LaTeX（`source_latex`）か原文（`raw_text`）と正規化後に一致すれば印を付けない。`annotate_reconstructed_formulas`（チャットの formula_facts・教材・講義）と `course_content_builder._equation_is_reconstructed` もこの正本を通す。画面ごとの印の違いの直接の原因（写しへ運ばない）は IK-0977 で是正。ガードレール `backend/tests/test_lecture_studio_round25_r4.py`。

第 5 周の再演（2026-10-27）: **STILL**。同じ作用の式 S（eq_blk_003_0065）が、講義・教材の formulas では印なし（`reconstructed: false`）、チャットの出典 6 の chunk の formulas では `reconstructed: true` で、チャットは「復元」と言う（読み取りの API で確認）。平坦形の記録に原文の LaTeX が無いと「原文と一致すれば印なし」が効かない。run: uxsim/runs/c-astro-r25-studio-r5/20261027T072352Z-12975 seq15。

第 5 波で是正（2026-10-27）: 印の正本を式の単位にした。`![[equation:id]]` の埋め込みと `[[FORMULA_N]]` の解決は、写しの LaTeX の文字一致ではなく同じ id の記録（根拠リンク ∪ content_blocks）のどれかが復元なら復元とする（`lecture._equation_unit_reconstructed`）。作用の式 S は根拠リンクの LaTeX（描く本体）と content_blocks の記録（復元の印あり）の文字が違い、写しの照合で印が落ちていた。印と表示の可否の組は `learner_context_common.equation_display_fields` の 1 関数（IK-1008）。未確認: 固定コースの t16 で講義・チャットの印が一致するかは実機で見ていない／平坦形の chunks.formulas に原文の LaTeX が無い式の判定そのもの（再解析で揃う）は変えていない。

第 6 周の再演（2026-10-27）: **STILL**（第 5 波の「同じ id の記録のどれかが復元なら復元」は効いていない）。固定コース 6be229e1 の講義の式 253 件で `reconstructed: true` が 0 件（t16 の作用の式 S を含む）。t11 の `![[equation:eq_4]]`（L_TOV）・`eq_2`（N_TOV）は本文・講義・付録で印なし、同じ id を部品の文脈（comp_005）は「AI が文脈から復元した式です（原文の数式とは未照合）」と出し、チャットは「教材では印なし、[出典5] では式 (1)〜(4) すべてに［AI復元］」と食い違いを自分で言う（5 か所のうち 3 対 2）。教員の course-structure の content_blocks / evidence_items の当該記録には復元の判定に使える欄（reconstruction / reconstructed）が無く、部品の文脈だけが生きた式の記録を読む — 写し（凍結の content_blocks・根拠リンク）に復元の状態が載っていないのが残る根（仮説）。`latex_withheld` の式は講義に無し（吹き出しは伏せる — IK-1008 GONE）。run: uxsim/runs/c-astro-r25-studio-r6/20261027T123926Z-13979 seq6・8〜14・15・19、読み取りの API（sequence 全 20 トピック・component context・course-structure）。

第 6 波で是正（2026-10-27）: 根は「写し（凍結の content_blocks の式・根拠リンク・講義の式）に復元の状態が無い」こと。`core/equation_reconstruction.py` が document ごとの生きた式の記録（採用 run の `equation_semantics` artifact ∪ `chunks.formulas`）を集め、`learner_context_common.build_equation_reconstruction_index` が式の id → 印字番号 → 正規化した LaTeX の鍵で引ける索引にする（同じ鍵は OR・論文が割れる鍵は使わない・120 秒キャッシュ・失敗は空の索引）。判定の入口は `equation_is_reconstructed(record, *, index, document_id)` の 1 関数で、`equation_display_fields` / `annotate_reconstructed_formulas` / `formula_fact_line` / 部品の文脈がこれを通る。本文・付録・講義は `services.overlay_topic_equation_reconstruction` が状態を載せた複製を読む（保存データは書き換えない）。新しい freeze は `_frozen_reconstruction_state` で写しに状態（`reconstructed` + `reconstruction_checked`）を書く。5 か所が同じ答えになることを `test_lecture_studio_round25_r6.py::TestFivePlacesShareOneAnswer` で固定。

第 7 周の再演（2026-10-28）: **GONE**。t11 の 5 か所（本文・付録・講義・⚓ 部品の文脈・チャット）が同じ側（AI復元）を言った。読み取りの学生 API で講義 t11 の表示式 `[[FORMULA_16]]`（N_TOV）・`[[FORMULA_17]]`（L_TOV）・付録の 3 式が `reconstructed: true`（第 6 周は講義 253 式で 0 件）、本文・付録にも〔AI復元〕、部品の文脈の eq_4 / eq_2 と同じ。チャットは「式1から式4までは、どれもAIが文脈から復元した式という印」と言い、第 6 周の「教材では印なし、[出典5] では…」の食い違いの言明が消えた。run: uxsim/runs/c-astro-r25-studio-r7/20261027T173357Z-61213 seq6・9・14・15・19、uxsim/runs/c-astro-r25-studio-r7/20261027T172130Z-55411 seq18。

第 8 周の再演（2026-10-28）: **GONE 継続**。st-08 の t11 で本文の表示式・講義・付録 3 式・⚓ の部品の文脈・チャットの 5 か所が同じ「AI 復元」の側（st-01 の t11 教材も同じ）。講義中のチャットの後の［▶ 再開］で 5 / 6 から続いた。run: uxsim/runs/c-astro-r25-studio-r8/20261027T224929Z-2551 seq6・14・15・18・19。
