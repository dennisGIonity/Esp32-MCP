# AEDI - IONITY GLOBAL | ESP32-MCP fleet server image | Policy 986 AED
# --- portal build stage (npm workspaces: packages/* + apps/portal) ----------
FROM node:22-alpine AS portal
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
WORKDIR /src
COPY package.json package-lock.json ./
COPY packages/ packages/
COPY apps/portal/package.json apps/portal/
RUN npm ci --no-audit --no-fund
COPY apps/portal/ apps/portal/
RUN npm run build -w @ionity/portal

# --- runtime -----------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/server \
    IONITY_HOST=0.0.0.0 IONITY_PORT=8099 IONITY_SQLITE_PATH=/data/fleet.db \
    IONITY_FIRMWARE_DIST=/app/firmware/dist
WORKDIR /app
COPY services/fleet-host/server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
 && useradd --system --uid 10001 --home /app ionity \
 && mkdir -p /data /app/firmware/dist && chown -R ionity:ionity /data /app
COPY --chown=ionity:ionity services/fleet-host/server    /app/server
COPY --chown=ionity:ionity services/fleet-host/dashboard /app/dashboard
# The board portal at /portal/ (the 2.x flasher is retired; /flasher/ redirects here)
COPY --chown=ionity:ionity --from=portal /src/apps/portal/dist /app/portal/dist
USER ionity
EXPOSE 8099
# The LAN DNS resolver binds :53, which a non-root process cannot: run it on
# IONITY_DNS_PORT=5353 and map 53:5353/udp, or add cap_add: [NET_BIND_SERVICE].
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8099/api/v1/health', timeout=3).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8099", "--app-dir", "/app/server", "--proxy-headers"]
