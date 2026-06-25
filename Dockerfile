FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VNC_TARGETS_FILE=/data/targets.json \
    NOVNC_ROOT=/usr/share/novnc

# noVNC is a static front-end (no build or install step needed) and is pinned
# as the vendor/novnc git submodule, so the image and local runs share the
# exact same commit. Fail the build loudly if the submodule was not
# initialised (an empty dir would otherwise produce a viewer-less image).
COPY vendor/novnc/ /usr/share/novnc/
RUN test -f /usr/share/novnc/vnc.html && test -f /usr/share/novnc/core/rfb.js \
    || { echo "ERROR: vendor/novnc is empty — run: git submodule update --init vendor/novnc" >&2; exit 1; }

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

VOLUME ["/data"]
EXPOSE 6080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6080"]

