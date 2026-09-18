---
id: IK-0004
title: 前提知識の習得を「質問した履歴がある」で判定し、説明にも出所が付かない
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-10
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §2 F4
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 4
  - docs/architecture/six_lenses_2026-09-10/known_issues_architecture.md H-08
feature_context:
  realizing: 学習の前に必要な前提が足りているかを確かめ、足りなければその場で補う
  layers: [rag_chat, guidance_g]
classification:
  primary: governance
  facets: [governance.completion, connection.information]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「習得という完了の判定が、直接の確認ではなく接触の痕跡という代理で定義されて
    いる」こと。判定処理は定義どおりに動いており、権限も版も欠けていない。完了の定義を
    変えないかぎり、痕跡の集め方を変えても同じ結論になる。確認手段は F4 行が引く判定箇所と、
    前提の説明を返す分岐が検索を通らず出所ラベルを持たないこと。
generalization:
  level: general
  general_form: 達成したかどうかを、直接の確認ではなく接触の痕跡で代理判定する
pattern: completion-defined-by-proxy
discovery:
  perspective: [invariant_audit, trace_walk]
  note: >-
    前提ゲートの判定材料を遡ると、本人の明示的な答えではなく会話履歴の有無に行き着いた。
    併せて、前提を説明する分岐だけが検索経路を通らず、回答の出所ラベルが空のまま返ることが
    分かった。コーパスに前提を扱う資料が無い場合は永久に未習得のままになる。
resolution:
  perspective: [first_class_state, carry_through]
  note: >-
    完了の定義を「本人が理解していると明示的に答えた記録」だけに絞り、履歴による自動スキップを
    撤去した。説明の側は三段で解き、どの段に着地したかを出所ラベルとして必ず返すようにした。
    どこにも無いときは閉世界の事実文で「このコーパスの中には無い」とだけ言い、分野全体の不在は
    主張しない。
  landed_in:
    - backend/api/services.py
    - backend/api/routes/learning.py
    - backend/tests/test_prerequisite_resolution.py
    - docs/backend/rag-chat.md §①
    - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 4
related: [IK-0001, IK-0003]
view_of: []
history: []
---

## 課題

症状は二つある。ひとつは、前提を扱うトピックで一度会話しただけで前提が満たされたとみなされ、
ゲートが黙って開くこと。もうひとつは、前提の説明を返す分岐だけが検索経路を通らないため、
その回答に「何に基づくか」の出所が付かないことである。コーパスに前提を扱う資料が無い場合、
前提は永久に未習得のまま残る。

原因は、**習得という完了が接触の痕跡で代理定義されていた**ことにある。質問したことは理解の
根拠にならないが、判定はそれを根拠として扱っていた。出所の欠落は、他の分岐が満たしている
出所表示の契約を、この分岐だけが満たしていなかったことによる。

## 発見の観点

不変条項（沈黙適応をしない・出所の正直さ）と実装の照合。判定材料を遡って会話履歴に行き着き、
続けて説明を返す分岐を辿ると、回答の出所ラベルが埋まらないまま返ることが分かった。二つは
別の穴だが、どちらも「本人に見えないまま学習の可否が決まる」点で同じ場所に出た。

## 解決の観点

判定材料を増やす案は採らなかった。痕跡をいくら積んでも理解の確認にはならないからである。
完了の定義を本人の明示的な答えだけに絞り、その答えを学習状態の一級の記録として残す形にして、
履歴による自動スキップを外した。説明側は段を明示して解き、
どの段に着地したかを出所として必ず返す。どこにも無いときは閉世界の言い方で不在を述べ、
分野全体については何も言わない。

## 一般化

「済んだかどうか」を扱う機能すべてで再発する。ログイン回数・閲覧回数・滞在時間といった
接触の量を達成の代理にした瞬間に起こる。辞書の `completion-defined-by-proxy` に対応し、
処方は「完了の定義を、本人の行為か検証可能な事実だけで書く」。
