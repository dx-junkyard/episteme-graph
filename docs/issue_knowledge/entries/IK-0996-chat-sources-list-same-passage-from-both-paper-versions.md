---
id: IK-0996
title: "同じ論文を TeX と PDF で取り込んだとき、学習チャットの出典に同じ段落が 2 回（TeX 版の抜粋と PDF 版の抜粋）並び、出典の枠を同じ内容で占める"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生が学習チャットで論文について尋ね、回答の出典を確かめる"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 構造軸 aggregation — 検索結果を出典に束ねるとき、同じ論文の別の版（題名または arXiv ID が一致・IK-0919 の same_paper）のチャンクを別の資料として扱い、ほぼ同じ本文の 2 行を両方残す。接続軸 target: 出典は document 単位で数え、論文単位の同一性を見ない（IK-0846 が分野の地図の反復の数え方で直した同じ根の、RAG の面）。確定には、問いのスコープ（コースの sources か閲覧可能な全体か）と、2 行の document_id が PINN の 2 版であることをログで見ることが要る。
generalization:
  level: repo_pattern
  general_form: "同じ対象の 2 つの写しを別々の対象として扱い、一覧の枠を重複で占める"
pattern: same-name-different-referents
discovery:
  perspective: [data_inspection, reproduction]
  note: >-
    製品頭脳 B の 4055（学習チャット casual・音声）に渡ったプロンプト（uxsim/runs/mailbox/req-04055.json）で、[出典1] と [出典4] が同じ段落（「Phase 4 re-trains … Reproducibility is assessed across N=15 random seeds.」）の TeX 版と PDF 版の抜粋（空白の入り方だけ違う）だった。学生 st-07 の回答（uxsim/runs/c-astro-r25-share-r5/20261027T071522Z-64789 seq10）でも同じ題名の出典が 4 件並んだ。
resolution:
  perspective: [canonical_source, representation_change]
  note: >-
    search_chunks_with_metadata の後処理で core/source_versions.collapse_paper_versions を通し、同じ論文の別の版（判定は IK-0919 の正本 identity_candidates.same_paper）の同じ箇所（式のプレースホルダを除いた本文の一致）を 1 行に畳む。残す版は 焦点の論文（prefer_document_ids）→ AI が復元した式の少ない版 → TeX の取り込み → 検索の順位。残した行は群の最高の類似度を引き継ぎ、version_note（事実文・数値なし）を sources[] と履歴メタ・出典カードに載せる。仮説のうち確かめたのは、検索結果の組み立てが document 単位で版を見ていなかったことをコードで見たところまで。
  landed_in:
    - "backend/core/source_versions.py"
    - "backend/api/services.py"
    - "backend/api/routes/learning.py"
    - "backend/api/schemas.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_round25_wave5_chat_rag.py"
    - "docs/backend/rag-chat.md"
  verification:
    methods: [guardrail]
    unverified:
      - "TeX 版と PDF 版を取り込んだ砂場で、同じ問いの出典に同じ段落が重ならないこと"
      - "表示中の教材の出典（get_chunks_for_prompt）・前の回答の持ち越しと検索結果の間の版の重複（後処理は検索結果の中だけ）"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "同一視の規則は IK-0919 の same_paper を呼ぶだけにし、新しい規則を作らなかった。畳むのは検索結果の後処理 1 箇所（学習チャット・前提の説明・講義の検索が同じ関数を通る）。固有の差分: 焦点の論文の版を先に残す（FD3 の除外で落ちないため）。"
related: [IK-0846, IK-0919, IK-0987, IK-0956]
view_of: []
history: []
---

## 課題

症状: 出典の数件が同じ段落の 2 版で占められ、学生には同じ題名の同じ文が繰り返し見える（どちらが TeX 版かも分からない）。原因（仮説）: 出典の束ねが論文の版を同一視しない。

## 発見の観点

データの点検（製品頭脳に渡ったプロンプトの出典）と再現（st-07 (d)）。

## 解決の観点

第 25 周 第 5 波で解決（frontmatter の resolution）。当初の是正案: 検索結果の後段で、同じ論文の別の版のチャンクどうしが正規化本文でほぼ一致するなら 1 行に畳み、焦点の論文（またはコースの sources にある版）の行を残す。判定の正本は IK-0919 の same_paper（新しい同一視の規則を作らない）。

## 一般化

写しを別物として扱う型。確かめ方は、2 版を取り込んだ砂場で同じ問いの出典に同じ段落が 1 回しか出ないことを見ること。

面 B 第 6 周（2026-10-27）: st-05 の回答の出所ラベルは「〔出所: 教材に基づく〕」だが、出典の半数はコース教材の外（同題名の PDF 版）で、出典タブでは本文が開けない — 出所の表示と出典の中身が食い違って見える。製品頭脳 B（4241）は別論文（Cep B）のチャンクが中性子星のトピックの文脈に載ったことも記した。run: uxsim/runs/c-r25-doubt-r6/20261027T123134Z-9179（seq7・8）。
