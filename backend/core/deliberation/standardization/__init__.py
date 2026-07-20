"""標準化判定 worker（Phase S、知識ネットワークビジョン §3 修正③・§6・§7）。

三角測量（①LLM 事前知識 ②L層凍結版類似 ③コーパス内反復）で共通部品（L層
``library_entries``）の ``standardization_status`` を決定論的に導出し、常に
``status='candidate'`` の ``element_annotations``（``kind='standardization'``）へ書く。
本パッケージは FastAPI にも ``routes``/``services`` にも依存しない（core 共通ルール）。
"""
