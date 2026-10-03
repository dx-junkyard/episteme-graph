---
id: IK-0611
title: "理論モジュールと実行結果の画面語（外枠・内側・番号なし・吸い込み口・未測定・一致・分離）の定義が画面にもマニュアルにも無く、使い方の質問にも目次が返る"
status: open
recorded_at: 2026-10-03
resolved_at: null
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
  perspective: [pending]
  note: "部分着地（第 21 波・2026-10-03）: 学生マニュアル §19 に #simulation-lab-modules（外枠・内側・番号なし・この手順の式）と #simulation-lab-levels（一致・分離・記載値・未測定）の 2 節を足し、simulation-lab.js の一覧見出し直下に凡例 1 行を置いた。英語の問いに使い方の HELP 経路が目次だけを返す（§19 が引けない）のは再演でも残ったため open のまま。"
  landed_in:
    - docs/manual/student/02-student.md
    - frontend/public/js/simulation-lab.js
    - "docs/features/simulation_bridge_design.md §12.4"
related: [IK-0612, IK-0619]
view_of: []
history: []
---

## 課題

症状: 一覧の「外枠 / 内側」「番号なし」「吸い込み口」、結果の「未測定」「一致」「分離」が何を意味するか学習者が分からず、チャットで尋ねても手順の節が返る。原因: これらは設計の語で、画面に凡例が無く、マニュアル §19 にも定義の節が無い。HELP は存在する節の中から最上位を返すため、無い定義の代わりに目次的な答えになる。

## 発見の観点

ペルソナの通し受講（reproduction）で詰まりと聞き直しを観測し、画面の語とマニュアルの見出しを突き合わせた（doc_code_diff）。

## 解決の観点

部分着地（第 21 波）。定義の節 2 つをマニュアル §19 に足し、画面の一覧に凡例 1 行を置いた。英語の問いに対して HELP 経路が目次だけを返し、新しい節が引けない症状は再演でも残った（分割はせずこのエントリで追う）。

## 一般化

画面語とその定義を別の場所で持つと、語だけが先に出て定義が追いつかない。型は wording-mismatch（文言の出所が 1 つでも定義が無いなら本型）。確かめ方は、画面に出る語を列挙し、各語の定義がマニュアルの節に逐語で現れ HELP で引けることを見ること。
