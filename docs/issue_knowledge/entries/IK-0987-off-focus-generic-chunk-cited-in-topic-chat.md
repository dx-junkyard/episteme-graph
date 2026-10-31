---
id: IK-0987
title: "中性子星の論文のトピックで部品について尋ねた学習チャットの出典に、無関係な論文（連星ブラックホールの遅延時間分布）の「This paper is organized as follows」の段落が並ぶ — 焦点の論文が決まっているのに、論文の構成を述べる汎用の段落が焦点の外から通る"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生がトピックの部品について学習チャットで尋ね、回答の出典を確かめる"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [target]
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
    仮説: 接続軸 target — 焦点の優先規則（core/focus_document.py prefer_focus）は焦点の外の行を「焦点内の最良より厳密に高い」ときだけ残す。会話調の短い問い（「どこが違うの？」）では焦点内の行の点が低く、論文の構成を述べる汎用の段落（どの論文にもある）が焦点内の最良を上回って残った可能性。処理軸 logic は確信度 low（casual の経路が prefer_focus を通っているか自体も未確認）。確定には、同じ往復の検索結果の点と焦点の解決（source）をログで見ることが要る。
generalization:
  level: repo_pattern
  general_form: "対象を絞る規則が相対的な点の比較だけで外の候補を通し、どの文書にもある汎用の段落が無関係な出典として残る"
pattern: scope-widened-silently
discovery:
  perspective: [reproduction]
  note: >-
    st-07 seq11 の出典7（uxsim/runs/c-astro-r25-share-r5/20261027T071522Z-64789）。製品頭脳 A の 4056（学習チャット casual・選択あり）でも同じ論文の同じ段落が[出典7]に混入した。
resolution:
  perspective: [single_point_fix]
  note: >-
    FD3 の閾値は変えず、検索の非本文判定 non_content_chunk_reason の outline を _is_paper_outline_paragraph にした。TeX の \ref が解決されず番号の落ちた節参照（in Sec. .）も節参照に数え、構成の文から後ろが段落の過半ならそれだけで outline。長い導入に構成の 1 文が付いた区画は落とさない。仮説の確認: mailbox req-04056 の [出典7] の本文が「Sec. .」で番号を持たず、既存の outline 判定（番号付き節参照 2 つ以上）を通らなかったことを見た。焦点の解決の段（source）は見ていない。
  landed_in:
    - "backend/api/services.py"
    - "backend/tests/test_round25_wave5_chat_rag.py"
    - "docs/features/focus_document_design.md"
  verification:
    methods: [guardrail]
    unverified:
      - "同じ往復の検索結果の点と焦点の解決（source）"
      - "砂場での再演（st-07 の 2 問目）"
related: [IK-0580, IK-0986]
view_of: []
history: []
---

## 課題

症状: 焦点の論文と無関係な論文の構成の段落が出典に並び、学生は関係を推測できない。原因（仮説）: 焦点の外の行を通す規則が相対比較だけで、汎用の段落を止めない。

## 発見の観点

再現（st-07 (d) seq11）と製品頭脳の所見の突き合わせ。

## 解決の観点

第 25 周 第 5 波で解決（frontmatter の resolution）。当初の是正案: 焦点の論文が①〜③段で決まっているときは、焦点の外の行に採用の下限より一段高い絶対の下限を課す（相対比較に加えて）か、論文の構成を述べる段落（節「Introduction」の構成の文）を焦点の外では採らない。FD3 の規則の変更なので focus_document の設計書に追記してから。

## 一般化

相対比較だけの絞り込みが汎用の候補を通す型。確かめ方は、同じ問いで焦点の外の汎用段落が出典に出ないことを見ること。

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: STILL。st-08 の選択質問の回答が「検索結果に別分野の論文（磁場とフィラメント雲の論文）の抜粋が混ざっていますが、この部品とは関係がありません」と言う — 焦点の外の Cep B の段落が材料に入り、AI がそれを学生に語る（内部の検索の様子を学生に出す新しい面）。製品頭脳 A の 4240・4241 も Cep B の出典の混入を記した。run: uxsim/runs/c-astro-r25-share-r6/20261027T122930Z-7994 seq11

第 6 波（2026-10-27・面を跨ぐ chat / RAG）: STILL の根を別の見立てで追った。焦点の論文が PDF 版の document のとき、同じ論文の TeX 版の行が焦点の外に数えられ、焦点内に採用できる行が無いとみなされて FD3 の除外が働かない（汎用の段落かどうかに関係なく Cep B が通る）。焦点を同じ論文の別の版まで広げる是正を IK-1035 に記帳した。

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: 回答の面は再現せず（st-05 の 2 回の回答は Cep B に触れない）、材料の面は STILL — 2 回目の選択質問のプロンプトの [出典5] に Cep B 論文の「The structure of this paper is as follows…」の段落が入っていた（製品頭脳 A 4258・uxsim/runs/mailbox/req-04258.json・17:21 = st-05 seq11）。IK-1035 の是正の後も焦点の外の章構成の段落が通る。
