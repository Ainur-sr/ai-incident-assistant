FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ANALYSIS_MODE=mock \
    RAG_ENABLED=false \
    HF_HOME=/home/app/.cache/huggingface

WORKDIR /app
COPY requirements.txt requirements-lock.txt ./
RUN python -m pip install --no-cache-dir -c requirements-lock.txt -r requirements.txt

ARG INSTALL_RAG=false
COPY requirements-rag.txt requirements-rag-lock.txt ./
RUN if [ "$INSTALL_RAG" = "true" ]; then \
        python -m pip install --no-cache-dir --no-deps -c requirements-rag-lock.txt torch \
            --index-url https://download.pytorch.org/whl/cpu && \
        python -m pip install --no-cache-dir -c requirements-lock.txt -c requirements-rag-lock.txt \
            -r requirements-rag.txt; \
    fi

RUN useradd --create-home app && install -d -o app -g app "$HF_HOME"
COPY app/*.py ./app/
COPY runbooks/*.md ./runbooks/
USER app

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "from http.client import HTTPConnection; conn = HTTPConnection('127.0.0.1', 8000, timeout=3); conn.request('GET', '/health'); raise SystemExit(conn.getresponse().status != 200)"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
