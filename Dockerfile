# Build the SPA, then copy only the wheel and the static assets into the runtime
# image. Two stages so node and its 200 MB of tooling never ship.

FROM node:24-alpine AS web
WORKDIR /web
COPY web/package.json ./
# No lockfile here on purpose: the one committed was generated on macOS and
# pins only rollup's darwin binary, so `npm ci` on linux/arm64 dies with
# "Cannot find module @rollup/rollup-linux-arm64-musl". Resolving from
# package.json inside the target image picks the right native binary.
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMG_WEB_DIST=/app/web/dist
WORKDIR /app
COPY backend/pyproject.toml backend/README.md* ./
COPY backend/src ./src
RUN pip install --no-cache-dir .
COPY --from=web /web/dist ./web/dist

RUN useradd --system --uid 10001 --create-home omg && chown -R omg:omg /app
USER omg
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"
ENTRYPOINT ["omg"]
CMD ["serve"]
