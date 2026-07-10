"""検証スコープ候補の LLM 補助（D1-4, 非同期バッチ）。

tension / structure_anchor と同型の独立モジュール。教員の記帳負荷を下げるため、
claim の evidence・equation まわりの記述から検証スコープ **候補** を抽出する。

不変条項:
  - 候補は常に status='candidate' で epistemic_ledger.scope_candidates 列に保持。
    教員がタップで確定するまで verification_scopes 本体には入らない。
  - 候補は必ず逐語 evidence_quote・reason・confidence を持つ（P5）。
  - 同期パスに LLM を入れない（P6）: worker は threading.Thread の非同期バッチ。
  - コスト上限は DOUBT_SCOPE_MAX_CALLS_PER_DAY（既定 10、tension とは独立）。
"""
