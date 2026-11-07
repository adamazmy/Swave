# Swave API, production (private deployment). Built by docker-compose.private.yml.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY pyproject.toml ./
COPY swave ./swave
RUN pip install . && useradd --system --uid 10001 swave
# the deployed commit, reported by /health (make private-deploy checks it)
ARG SWAVE_GIT=unknown
ENV SWAVE_GIT=$SWAVE_GIT
USER swave
EXPOSE 8000
# serve.create strips the /api prefix the tunnel forwards; Cloudflare Access is verified in-app.
CMD ["uvicorn", "swave.serve:create", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "1"]
