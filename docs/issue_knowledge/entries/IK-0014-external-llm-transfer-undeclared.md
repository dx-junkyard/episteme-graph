---
id: IK-0014
title: 外部のモデルへ入力が渡る事実が当事者に告げられず、可視性が一軸に畳まれている
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-10
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §2 F10
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §5 B2
  - docs/architecture/six_lenses_2026-09-10/04_community.md
  - docs/features/disclosure_axes_design.md
feature_context:
  realizing: 学習者と教員が対話しながら学ぶ環境を、外部のモデルを用いて成り立たせる
  layers: [disclosure_axes, rag_chat, deliberation_w]
classification:
  primary: structure
  facets: [structure.representation, governance.review]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「開示の範囲が一つの軸に畳まれていて、外部へ渡るという開示を表せる場所が無い」
    こと。転送そのものは仕様どおりで、条件も情報も落ちていない。軸を分けないかぎり、文章を
    足しても宣言の置き場所が定まらない。確認手段は F10 行が引く学習者向け記述の不在と、
    可視性の語彙が三値一軸であること。
generalization:
  level: general
  general_form: 実際に起きている開示を表す軸が無く、当事者に宣言されないまま運用される
pattern: disclosure-not-declared
discovery:
  perspective: [invariant_audit, doc_code_diff]
  note: >-
    可視性を六つの軸に分けて現物を当てた。独立に扱えていたのは二軸だけで、名前の開示は
    全経路で固定、外部への転送は固定かつ不告知だった。学習者向けの文書にも記述が無く、
    送っている当事者が読める場所がどこにも無い。
resolution:
  perspective: [explicit_contract, canonical_source]
  note: >-
    同意を取る方向ではなく、事実を宣言する方向で解いた。六つの軸とデータ種別の対応を
    一つの宣言モジュールに置き、当事者が読める読み取り専用の経路で公開する。対話の画面には
    常設の事実文を置き、文言はサーバの宣言をそのまま描いてフロントに焼き込まない。
    初回の告知カードを置くかどうかは判断待ちとして分けた。
  landed_in:
    - backend/core/disclosure_axes.py
    - backend/api/routes/disclosure.py
    - docs/features/disclosure_axes_design.md
related: [IK-0012, IK-0021, IK-0022]
view_of: []
history: []
---

## 課題

症状は、学習者が入力したものが外部のモデルへ渡ることを、学習者が読める場所がどこにも無い
ことである。可視性は三値の一軸しか持たず、閲覧できるか・名前が出るか・引用できるか・評価に
使うか・外部へ渡るか・撤回できるか、という区別を表せなかった。

原因は、開示という事実を表す軸が構造として無いことにある。転送自体は設計どおりで、隠している
わけでもない。宣言する場所が無いので、結果として宣言されないまま運用されていた。

## 発見の観点

可視性を六つの軸に分解して現物を当てた。独立に扱えていたのは二軸だけで、残りは固定か不告知
だった。特に外部への転送は、実際に起きているのに学習者向けの文書にも一行も無い。送っている
当事者が読めないなら、それは告知ではない。

## 解決の観点

同意を取りに行く案は採らなかった。同意は押し付けを生み、しかも軸が分かれていないと何に
同意したのか定まらないからである。六つの軸とデータ種別の対応を宣言として一箇所に置き、
当事者が読める経路で公開した。画面の事実文はサーバの宣言をそのまま描き、文言をフロントに
複製しない。初回の告知カードを置くかは、機関の要件に関わるので判断待ちに分けた。

## 一般化

外部サービスへデータが渡る構成すべてで再発する。「隠していない」ことと「宣言している」ことは
別で、宣言の置き場所が無いと前者だけが成立する。辞書の `disclosure-not-declared` に対応し、
処方は「開示の軸を先に分け、当事者が読める場所に宣言を置く」。
