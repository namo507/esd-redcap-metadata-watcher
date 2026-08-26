# The ESD Visitboard: the live engine, not the frozen demo snapshot.
#
# Build context is this project directory, not the repository root. The repo
# holds several unrelated projects and none of them belong in this image.

FROM python:3.11-slim

# tesseract is what turns a calendar screenshot from "state the hours yourself"
# into "the hour column is read for you". It is small and it is the one system
# package worth having; if it is unavailable the screenshot path still works
# and simply asks for the time range.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements first so a code change does not reinstall the wheels.
COPY requirements-core.txt requirements-optional.txt ./

# Core must succeed: without PyMuPDF a calendar upload cannot be read at all.
RUN pip install --no-cache-dir -r requirements-core.txt

# Optional is best-effort on purpose. These are large wheels and a platform
# that cannot build one of them should still get a running board; every code
# path that uses them already degrades with a clear message.
RUN pip install --no-cache-dir -r requirements-optional.txt \
 || echo "optional extras unavailable; screenshot reading will be limited"

COPY . .

# The audit database. Mount a volume here to keep assignment history across
# deploys; without one the board starts with a fresh database each time, which
# is what it already does locally on a clean checkout.
ENV ESD_DATA_DIR=/data
RUN mkdir -p /data

# A container that binds 127.0.0.1 accepts nothing from outside itself.
ENV ESD_BIND=0.0.0.0
ENV PORT=8080
EXPOSE 8080

# --port and --no-browser are the flags backend/server.py actually defines, and
# --port already defaults to $PORT, so the platform can move the port without
# this line changing.
# Whether the board is actually serving, not merely whether the process is
# alive. A container that has booted but cannot answer /api/health is exactly
# the state an orchestrator needs to see, and a bare process check hides it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/api/health', timeout=4).status==200 else 1)"

CMD ["python3", "-m", "backend.server", "--no-browser"]
