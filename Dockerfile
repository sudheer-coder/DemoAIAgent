FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py index.html buyer.html sw.js manifest.webmanifest icon.svg icon-192.png icon-512.png ./
# The app's code files sit at the top of the repository; put them back into the agripilot package here.
COPY agents.py config.py db.py notify.py supervisor.py web.py ./agripilot/
ENV HOST=0.0.0.0 PYTHONUNBUFFERED=1
CMD ["python", "server.py"]
