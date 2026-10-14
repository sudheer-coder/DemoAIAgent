FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py index.html buyer.html sw.js manifest.webmanifest icon.svg icon-192.png icon-512.png ./
COPY agripilot/ ./agripilot/
ENV HOST=0.0.0.0 PYTHONUNBUFFERED=1
CMD ["python", "server.py"]
