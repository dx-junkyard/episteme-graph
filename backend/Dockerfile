FROM python:3.11-slim

WORKDIR /app

COPY backend/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy core engine, API and agents
COPY backend/core/ /app/core/
COPY backend/db/ /app/db/
COPY backend/cartridges/ /app/cartridges/
COPY backend/api/ /app/
COPY src/ /app/src/

# AI アシスタントの知識源（Admin Copilot KB + 利用者マニュアル KB）。
# docs/ 全体は COPY しない — 設計書等の開発文書をイメージに存在させないことが
# audience 越境の fail-closed（ビルド時前倒し）になる（manual_help_kb_design.md §2-1）。
COPY docs/admin_operations/ /app/docs/admin_operations/
COPY docs/manual/ /app/docs/manual/

# Issue #226: agents live at /app/src/episteme_graph/agents
ENV PYTHONPATH=/app:/app/src

EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
