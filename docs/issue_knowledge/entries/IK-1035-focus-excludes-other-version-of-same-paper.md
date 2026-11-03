---
id: IK-1035
title: "焦点の論文が PDF 版の document のとき、同じ論文の TeX 版のチャンクが焦点の外に数えられ、焦点内に採用できる行が無いとみなされて別の論文（無関係な分野の Cep B）の抜粋が出典に通る"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22"
  - "docs/features/focus_document_design.md"
feature_context:
  realizing: "学生がトピックの論文について学習チャットで尋ね、トピックの論文の出典だけで答えてもらう"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
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
    仮説: 接続軸 target — 焦点の集合（document 単位）と検索結果の行（同じ論文の別の版の document）の対象の単位が食い違う。
    FD3（prefer_focus）は焦点の行に採用の下限を満たすものがあるときだけ外の行を除外するので、焦点の PDF 版の行が下限未満で
    TeX 版の行だけが高いとき、何も除外されず Cep B が残る。第 5 波の版の畳み込み（IK-0996）は同じ箇所だけを畳むため、
    TeX 版の別の箇所は焦点の外のまま。確定には、該当往復の焦点の document（PDF 版か）と各行の document_id・類似度をログで見ることが要る。
generalization:
  level: repo_pattern
  general_form: "対象の集合を写しの 1 つだけで表し、同じ対象の別の写しから来た候補を対象の外として扱うため、絞り込みの規則が効かなくなる"
pattern: same-name-different-referents
discovery:
  perspective: [data_inspection, reproduction]
  note: >-
    製品頭脳 A 4240・B 4241 / 4245 に渡ったプロンプトで、中性子星のトピックの文脈に Cep B（2605.26810v1）のチャンクが出典として並んだ。
    第 6 周の再演で IK-0987 が STILL（uxsim/runs/c-astro-r25-share-r6/20261027T122930Z-7994 seq11）。
resolution:
  perspective: [carry_through, canonical_source]
  note: >-
    焦点を同じ論文の別の版まで広げる（core/focus_document.with_paper_versions。版の判定は IK-0919 の正本 same_paper を
    services.paper_version_siblings が呼ぶだけ・スコープとの積だけ = FD2）。学習チャットの本体と前提の説明の 2 経路が同じ関数を通る。
    resolve_focus_document は 1 回のまま、段（source）は変えない。FD3 の規則自体は変えていない。
  landed_in:
    - "backend/core/focus_document.py"
    - "backend/api/services.py"
    - "backend/api/routes/learning.py"
    - "backend/tests/test_round25_wave6_chat_rag.py"
    - "docs/features/focus_document_design.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "版の判定は same_paper の 1 つ、焦点の拡張は focus_document の 1 関数に置き、2 つの検索経路が同じ関数を通る。固有の差分: 拡張はスコープとの積だけで、段は変えない。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場で焦点の document が PDF 版であることと、是正後に Cep B の抜粋が出典から消えること"
      - "焦点の論文に版が無いときの挙動（従来どおりのはず）の実データでの確認"
related: [IK-0987, IK-0996, IK-0919]
view_of: []
history: []
---

## 課題

症状: トピックの論文（中性子星の PINN）について尋ねたのに、無関係な分野の論文（Cep B）の抜粋が出典に並び、回答が「関係のない抜粋が混ざっています」と検索の様子を学生に語る。原因（仮説）: 焦点の集合が PDF 版の document だけで、TeX 版の行を焦点の外に数えていた。

## 発見の観点

製品頭脳のプロンプトの点検と、学生の再演（IK-0987 の STILL）の突き合わせ。

## 解決の観点

焦点を版まで広げる（carry_through）。FD3 の閾値を変える案は、焦点の版が揃えば不要なので採らなかった。

## 一般化

写しの 1 つで対象を表す型。確かめ方: 焦点を PDF 版にして TeX 版の行だけが高い検索結果を作り、別の論文の行が除外されることを見る。

第 7 周（面 B の審判が製品頭脳の所見から写す・2026-10-28）: 製品頭脳 A 4258（PINN の二重ネットワークのトピック・選択箇所あり）で、Cep B 論文の章構成の抜粋が [出典5] として文脈に混ざった（第 6 波の是正後）。面 B の st-08 の 2 往復（t11・選択なし）の出典は PINN 論文だけで混入は見ていない。4258 は面 B の run ではない（選択箇所のある往復は他の面のレーン）ため、焦点に別版を足す是正が効いていないのか別の経路（選択箇所・構造ブロック）なのかの判定は、その run の transcript を持つ審判に委ねる。

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演（面 A）: STILL（材料）。PINN の TeX 版を素材にしたトピック（焦点は TeX 版）の選択質問で、プロンプトの [出典5] に Cep B 論文の章構成の段落（製品頭脳 A 4258）。焦点の論文の版を含める是正の後も、焦点の外の汎用段落が出典の末尾に残る。回答は使わなかった。

第 8 周（面 B）の観察（2026-10-28・他の面の是正を確かめる材料）: st-05 の tutor 回答の出典4「Neutron Star Equation of State … 冒頭「Figure 4 compares our R1.4 with…」」はチャンク 4d48f123（PINN TeX 版 ce667c63）で、固定コース 6be229e1 の sources（PDF 版）に無い。出典カードは本文 404 で一行が無く、学生には同じ題名で他の出典と区別できない・出所の印は「教材に基づく」のまま。焦点の是正の後でも、範囲外の別の版の引用が同題名として出る。run: uxsim/runs/c-r25-doubt-r8/20261027T224211Z-98239（seq8・9）。
