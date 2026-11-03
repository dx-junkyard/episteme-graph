---
id: IK-1160
title: "学習チャットが前の回答で出典付きで示した本文の数値を、次の往復の検索でその出典のチャンクが渡らないと「抜粋で確認できない」と撤回し、さらに次の往復で「本文に書かれている、撤回が誤り」と復活させる往復を繰り返す"
status: open
recorded_at: 2026-11-03
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/backend/rag-chat.md"
feature_context:
  realizing: "学生が学習チャットに論文の数値を尋ね、続けて出所を確かめる質問を重ねながら、前の回答の数値を信じてよいかを判断する"
  layers: [rag_chat]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [unknown]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 接続軸 information — 往復ごとに検索で渡る抜粋（top_k のチャンク）が入れ替わり、前の回答が引用したチャンクの id と本文が次の往復の
    文脈に運ばれないため、モデルは前の回答の根拠を見られない。処理軸 wording — 撤回の判断が「今回の抜粋に無い」を「本文に無い」と言い表す。
    撤回・訂正を保持する仕組み自体は設計どおりで、構造軸は none と見た。統制軸は撤回の前に根拠を確かめる手続の有無を見ていないので unknown。
    確定に要る確認: 手 8・10 の往復でモデルに渡った抜粋に、手 7・9 の出典 9 のチャンク（document 7ef9e487 の chunk_index 6〜7 付近）が
    含まれていなかったことをプロンプトのログで確かめる。
generalization:
  level: repo_pattern
  general_form: "往復ごとに根拠の抜粋を選び直す対話で、前の答えの根拠が次の往復に運ばれず、今回の抜粋に無いことを根拠そのものの不在と取り違えて前の答えを撤回する"
pattern: context-lost-across-execution-boundary
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    ペルソナ通し受講（campaign c-sandbox-p01-eos・st-11-m1-neutron-first-sim・s-ask-where-from・コース 1bad7d9f・対象版 5fd05d3c）の手 7〜11。
    手 7 で P(ρ0)=3.03–3.06 MeV/fm^3 を出典 9 付きで提示 → 手 8 で「抜粋に確認できない、撤回」→ 手 9 で「出典 9・13 の本文にある、撤回が誤り」。
    手 10 で M_max=2.062 と c_s^2(2ρ0)=0.241 を撤回 → 手 11 で「出典 9 に明記、撤回が誤り」。砂場 DB の chunks（document 7ef9e487・chunk_index 7）に
    P(ρ0)=3.03-3.06 MeV/fm^3 が実在し、出典 9 のチャンク付近に M_max もあることを確かめた。審判 C は手 7・9・10・11 を confused
    （finding f-20261103T040912Z-81314-0002）。run: uxsim/runs/c-sandbox-p01-eos/20261103T040912Z-81314/report.md・transcript.jsonl。
resolution:
  perspective: [pending]
  note: >-
    未解決。候補: ①撤回を言う前に、前の回答が引用したチャンク id を今回の文脈に必ず含める（carry_through）。②「今回の抜粋に無い」と
    「本文に無い」を言い分け、前者では撤回せず確認できない旨だけを言う（wording）。原因の確定（渡った抜粋の確認）が先。
  landed_in: []
related: [IK-1159]
view_of: []
history: []
---

## 課題

症状: 同じ数値が往復ごとに「撤回」と「復活」を繰り返し、最初の答えを信じる newcomer（P-01 型）は、本文に書いてある数値まで疑うことになる。

原因（仮説）: 往復ごとに検索で渡る抜粋が変わり、前の回答の根拠チャンクが次の往復に渡らない。モデルは「今回の抜粋に無い」を
「本文に無い」と取り、撤回する。撤回の判断が前の回答の出典チャンクを参照していない。

## 発見の観点

ペルソナの通し受講で回答の撤回と復活が往復した（reproduction）。砂場 DB のチャンクを読み、撤回された数値が本文に実在することを確かめた（data_inspection）。

## 解決の観点

未解決。何が分かれば解けるか: 撤回した往復でモデルに渡った抜粋に、前の回答の出典チャンクが含まれていたか。含まれていなければ根拠の運搬
（前回答の引用チャンクを文脈に固定）、含まれていたなら言い表し（抜粋に無いと本文に無いを分ける）が主になる。

## 一般化

根拠を往復ごとに選び直す対話全般（検索付きチャット・質疑）で再発しうる。確かめ方: 数値を出典付きで答えさせた後、別の観点の質問で抜粋を
入れ替え、前の数値を撤回しないか・撤回するなら前の出典を確かめた上かを見る。
