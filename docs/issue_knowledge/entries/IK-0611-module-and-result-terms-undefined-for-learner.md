---
id: IK-0611
title: "理論モジュールと実行結果の画面語（外枠・内側・番号なし・吸い込み口・未測定・一致・分離）の定義が画面にもマニュアルにも無く、使い方の質問にも目次が返る"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-04
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "学習者が理論モジュールを選んで動かし、結果の段階の言葉を読んで次の操作を決める"
  layers: [simulation_bridge_sb, frontend_learning_ui, help_kb]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [meaning]
    governance: [completion]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: low
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: docs/manual/student/02-student.md の §19（simulation-lab）は手順・一致は検証ではない・予想は採点されない・共有の節を持つが、外枠・内側・番号なし・吸い込み口・未測定・分離の定義の節が無い。frontend/public/js/simulation-lab.js にも凡例が無い。使い方の質問は HELP 経路で §19 の最上位節（何ができるか）に当たり目次的な答えが返った。処理軸 wording = 設計の語が定義なしで画面に出る。接続軸 meaning = 設計書の語義が学習者の画面へ伝わらない。統制軸 completion（low）= 画面語を足したときに定義を揃える手続が完了条件に入っていない、と読んだが手続の課題かは迷った。構造軸 none。
generalization:
  level: repo_pattern
  general_form: "設計の内部語を画面の見出し・ラベルにそのまま使い、その語の定義を画面にも利用者向けの文書にも置かないため、利用者が意味を問うても定義が返らない"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, doc_code_diff]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、全員が外枠・番号なしの意味で迷い、未測定や予想の扱いを尋ねた学生には目次や手順の節が返った。マニュアル §19 の見出しと画面の語を突き合わせ、定義の節が無いと分かった。"
resolution:
  perspective: [single_point_fix, representation_change]
  note: "第 21 波で定義の節（#simulation-lab-modules / #simulation-lab-levels）と凡例を足したが、その節が HELP 検索で引けなかった。第 22 周（2026-10-04）の trace_walk で根を 4 つに分けた: ①目次節が documented ヒットになる（構造で判定して候補から外す）②日本語の採点が 1 字切りで機能語に支配される（マニュアル検索だけ 2 字 n-gram・ひらがな 1 字を数えない）③英語の問いは日本語の節に語で当たらない（語の照合をやめベクトル補助層 → 開いているパネルの節・英語の前置き 1 行・LLM 0 回）④結果の意味の問いが HELP に行くと実行の事実文が届かない（パネルが開いていて結果の言葉を含む発話は DOMAIN_RAG へ）。正本は manual_help_kb_design.md §9。"
  landed_in:
    - docs/manual/student/02-student.md
    - frontend/public/js/simulation-lab.js
    - backend/core/help_kb/index.py
    - backend/core/help_kb/manual.py
    - backend/api/routes/learning.py
    - backend/core/simulation_bridge/schema.py
    - "docs/features/manual_help_kb_design.md §9"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "ベクトル補助層が英語の問いで simulation-lab-share に当たるか（第 22 周 (j) は実行の事実の経路で答えたため HELP 側は通っていない）"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "採点・目次除外は search_manual 1 箇所、結果語の判定は schema の 1 関数に置き、pre-route と分類の両経路が同じ判定を通る"
related: [IK-0612, IK-0619]
view_of: []
history:
  - date: 2026-10-04
    field: resolution.verification
    from: 記録時の範囲
    to: 第 22 周の再演で更新
    reason: "第 22 周 (c)(j) の再演で GONE"
---

## 課題

症状: 一覧の「外枠 / 内側」「番号なし」「吸い込み口」、結果の「未測定」「一致」「分離」が何を意味するか学習者が分からず、チャットで尋ねても手順の節が返る。原因: これらは設計の語で、画面に凡例が無く、マニュアル §19 にも定義の節が無い。HELP は存在する節の中から最上位を返すため、無い定義の代わりに目次的な答えになる。

## 発見の観点

ペルソナの通し受講（reproduction）で詰まりと聞き直しを観測し、画面の語とマニュアルの見出しを突き合わせた（doc_code_diff）。

## 解決の観点

第 21 波は定義の節と凡例を足す表現の是正、第 22 周は「節があるのに引けない」検索側の根（目次・採点・言語・経路）を 1 箇所ずつ塞いだ境界の是正。

部分着地（第 21 波）。定義の節 2 つをマニュアル §19 に足し、画面の一覧に凡例 1 行を置いた。英語の問いに対して HELP 経路が目次だけを返し、新しい節が引けない症状は再演でも残った（分割はせずこのエントリで追う）。

第 22 周の再演: (c) で「未測定とは」「点数になるか」に段階の言葉の節が返り、(j) で英語の 2 問が実行の事実で答えられた（GONE）。

## 一般化

画面語とその定義を別の場所で持つと、語だけが先に出て定義が追いつかない。型は wording-mismatch（文言の出所が 1 つでも定義が無いなら本型）。確かめ方は、画面に出る語を列挙し、各語の定義がマニュアルの節に逐語で現れ HELP で引けることを見ること。
