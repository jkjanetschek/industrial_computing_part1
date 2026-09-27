
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY server.py database.py discount.py audit.py schema.sql ./

ENV INVENTORY_DB=/data/inventory.db
ENV AUDIT_LOG=/data/audit.log
RUN mkdir -p /data
VOLUME ["/data"]

# Expose the internal port matching your mcp.run configuration
EXPOSE 8000

CMD ["python", "server.py"]