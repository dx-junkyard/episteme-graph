---
id: IK-0420
title: "GROBID が TEI を返さず PyMuPDF だけで解析された論文は、文書構造の題名を取り出す処理が一度も走らず metadata.title が null のまま残り、題名の書き戻し（IK-0366 / IK-0419）が効かず学習者の各画面に arXiv 番号で出続ける"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 論文 PDF の題名を文書構造（A層）で取り出し、教材の題名として学習者の各画面へ届ける
  layers: [pipeline_a]
classification:
  axes:
    processing: [input_handling]
    structure: [responsibility]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: low
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: 題名を決める判断が GROBID の TEI パーサ（grobid_parser._parse_metadata）の中にだけ置かれ、agent の2つの解析経路のうち PyMuPDF 経路（agent._extract_pymupdf）は DocumentMetadata(title=None) のまま返していた（responsibility）。接続軸: 1頁目のレイアウトブロック（文字の大きさ・位置）は PyMuPDF 経路でも手元にあるのに題名へ運ばれず、metadata.title を読む後段（extracted_document_title・起動時バックフィル）へ何も届かなかった（information）。処理軸: TEI 経路も titleStmt の最初の title だけを get_text(strip=True) で読み、空の要素・組版スタンプ（Draft version ...）・入れ子の空白を扱っていなかった（input_handling。今回の3本の直接原因ではないので low）。統制軸: GROBID の失敗から PyMuPDF への縮退自体は設計どおり（none。縮退理由を記録しない点は別件）。確認: 砂場 DB の読み取りで、題名が null の3本（2605.26810 / 2606.00411 / 2606.02318）はいずれも grobid_parse が status=fallback・tei_bytes=0、document_structure の blocks に grobid 由来が0件で、題名が取れた1本（2605.31198）だけが grobid_tei 経路だった。
generalization:
  level: repo_pattern
  general_form: 同じ出力を作る経路が2つあるとき、片方の経路にしか項目の取り出しが無く、縮退した経路ではその項目が黙って空になる
pattern: available-but-unwired
discovery:
  perspective: [symptom_report, data_inspection]
  note: 学生ペルソナが全画面で論文を番号で受け取り、砂場 DB の document_structure artifact と grobid_parse の記録を読んで、題名が null の論文がすべて PyMuPDF 経路だったことを確かめた。
resolution:
  perspective: [canonical_source, carry_through]
  note: >-
    題名の決め方を document_structure/title_extraction.py の1か所にまとめ、両経路から agent._resolve_title で呼ぶ。順序は (a) TEI ヘッダ（titleStmt の type="main" → level="a" → その他 → sourceDesc//analytic。参照文献内は読まない・組版スタンプに見える候補は採らない）→ (b) 1頁目の上半分で最も大きい文字の連なりを位置順につなぎ、大きさが下がったところで止める（組版ヘッダ・arXiv スタンプ・縦書き・日付・e-mail・URL は飛ばす・本文の 1.15 倍未満や 200 字超は採らない）→ (c) None。題名は常に1行（空白の畳み込み・行末ハイフンは残して改行だけ詰める）。出所は metadata.title_extraction（source = tei / font_size / none・confidence・needs_review・review_reasons・candidate_sources。author_extraction と同じ形）に残し、font_size 由来は needs_review=true。大きさの判定は RawBlock.dominant_font_size（文字数加重の最頻値）を使い、脚注記号で平均が下がる題名ブロックでも拾う。persistence.extracted_document_title は1行の文字列をそのまま受けるので変更なし。
  landed_in:
    - src/episteme_graph/agents/document_structure/title_extraction.py
    - src/episteme_graph/agents/document_structure/agent.py
    - src/episteme_graph/agents/document_structure/grobid_parser.py
    - src/episteme_graph/agents/document_structure/parser.py
    - src/episteme_graph/agents/document_structure/schema.py
    - src/tests/agents/document_structure/test_title_extraction.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再解析（3本を再解析して題名が付き、学習者の各画面に題名が出ること）
      - 実 PDF の文字の大きさでの判定（合成ブロックは組版の既定値の見当で置いた）
      - 既に解析済みの教材は再解析するまで題名が付かない（起動時バックフィルは artifact の metadata.title しか読まない）
      - GROBID が TEI を返さなかった理由（縮退理由が記録されていない）
related: [IK-0366, IK-0419]
view_of: []
history: []
---

## 課題

GROBID が失敗して PyMuPDF だけで解析された論文は、題名が一度も取り出されない。

## 発見の観点

実ペルソナの画面観測と、砂場 DB の artifact・grobid_parse の記録の突合。

## 解決の観点

題名の決め方を1か所にまとめ、両経路から同じ順序（TEI → 最大フォント → なし）で呼び、出所を残す。

## 一般化

経路が2つある出力は、項目ごとに「どちらの経路でも埋まるか」を確かめる。
