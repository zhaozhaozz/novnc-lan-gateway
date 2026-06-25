FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VNC_TARGETS_FILE=/data/targets.json \
    NOVNC_ROOT=/usr/share/novnc

RUN apt-get update \
    && apt-get install -y --no-install-recommends novnc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

VOLUME ["/data"]
EXPOSE 6080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6080"]

