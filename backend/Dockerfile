# Vakya backend: a small Python image. Speech is Gnani and the language model is hosted, so nothing is downloaded or cached here.
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libsndfile1 curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8020
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD curl -fs http://localhost:8020/health || exit 1
CMD ["python", "-m", "uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8020"]
