---
id: IK-0215
title: コース全体の入口が教材ひとつだけを対象にし、件数と一覧の母数も食い違う
status: resolved
recorded_at: 2026-07-16
resolved_at: 2026-08-13
sources:
  - docs/architecture/issue_494_implementation_review_2026-07-16.md
  - docs/architecture/doc_review_findings_2026-08-13.md §5
feature_context:
  realizing: コースに含まれる全教材の要レビュー項目を、教員がひとつの入口から片付ける
  layers: [reconstruction_r, lecture_studio, frontend_admin_ui]
classification:
  primary: connection
  facets: [connection.target, structure.aggregation]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    取得の処理も描画の処理も、与えられた対象については正しく動く。原因は、入口が示す対象
    （コース全体）と問い合わせが使う対象（先頭の一件）が段階の間でずれていること。対象の
    受け渡しを直さない限り教材が増えるほど漏れる。件数と一覧で母数の定義が別々に置かれて
    いる点は副次の集約の問題。
generalization:
  level: general
  general_form: >-
    入口が示す対象の範囲より実際の問い合わせの範囲が狭く、差分が黙って落ちる
discovery:
  perspective: [adversarial_review, trace_walk]
  note: >-
    コース全体の入口に見えるボタンから、実際に投げられる問い合わせまで辿ると、対象として
    渡るのが一件だけであることが分かった。さらに、件数のバッジが数える条件と一覧が描く条件が
    別々に書かれており、バッジが零でも一覧に項目が並ぶ組み合わせが存在した。
resolution:
  perspective: [carry_through, canonical_source]
  note: >-
    対象をコース単位で受け取り、全教材の項目をサーバ側で集約して並べ替える方向を採った。
    画面側で複数回問い合わせて結合する案は、並べ替えと権限の判定が画面側に散るため退けた。
    件数と一覧が同じ条件を使うよう母数の定義も一本化した。
  landed_in:
    - backend/api/routes/reconstruction.py
    - frontend/public/js/admin-lecture-studio.js
related: [IK-0214]
view_of: []
pattern: entry-scope-mismatch
history: []
---

## 課題

**症状**: コースの見出しにある「要レビューの確認」は、コース全体の作業入口に見える。しかし
複数の教材を含むコースでは、二件目以降の項目がバッジからも一覧からも欠落する。加えて、
件数のバッジは未処理のものだけを数えるのに、一覧は処理済みのものも並べるため、バッジが零でも
一覧に項目が出るという表示の矛盾が起きる。

**原因**: 入口はコース全体を示しているのに、問い合わせに渡る対象が「いま選ばれている教材、
無ければ先頭の教材」の一件だけになっている。入口の意味と問い合わせの対象が段階の間でずれた。
母数の定義が件数側と一覧側に別々に書かれていることが、ずれを見えにくくしていた。

## 発見の観点

`adversarial_review` と `trace_walk`。入口のラベルが示す範囲と、実際に投げられる問い合わせの
対象を突き合わせる。教材がひとつのコースでは完全に正しく動くため、複数教材の場合を想定して
歩かないと見えない。

## 解決の観点

`carry_through`（対象をコース単位で受け渡す）+ `canonical_source`（母数の定義を一本化し、
件数と一覧が同じ条件を使う）。画面側で教材ごとに問い合わせて結合する案は、全教材をまたぐ
並べ替えと権限の判定が画面側に散るため採らなかった。

## 一般化

「全体の入口」に見えるボタンが、実際には現在の選択や先頭の一件だけを対象にする型は、
一覧・ダッシュボード・一括操作の入口で再発する。対象がひとつしか無い環境では完全に正しく
動くため、テストでも見落とされやすい。辞書に同型が無いため新しい型として提案する
（`entry-scope-mismatch`）。
