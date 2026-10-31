# sim-runner — 検証シミュレーション層（SB層）の隔離実行コンテナ。
# 正本: docs/features/simulation_bridge_design.md §5.5。ネットワーク・ports を持たない（compose 側で制限）。
FROM python:3.11-slim

RUN pip install --no-cache-dir numpy \
    && useradd --create-home --uid 10001 simrunner

WORKDIR /app
COPY server.py /app/server.py

USER simrunner
EXPOSE 8080
CMD ["python", "-I", "-S", "/app/server.py"]
