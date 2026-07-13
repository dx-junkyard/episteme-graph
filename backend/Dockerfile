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

# Issue #226: agents live at /app/src/episteme_graph/agents
ENV PYTHONPATH=/app:/app/src

EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
