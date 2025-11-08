# ----------------------------------------------------------------------------
# ARC Hackathon Dashboard Dockerfile (with auto-running agent)
# ----------------------------------------------------------------------------

## Stage 1: Build the React frontend
FROM node:20-alpine AS build_frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

## Stage 2: Python backend runtime
FROM python:3.11-slim
WORKDIR /app

# System deps (optional, keeps minimal)
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

# Install backend requirements
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend, built frontend, agent, and start script
COPY backend/ ./backend/
COPY --from=build_frontend /frontend/dist ./frontend_dist
COPY migrations/ ./migrations/
COPY improvise/ ./improvise/
COPY start.sh ./start.sh
RUN chmod +x /app/start.sh

# Prepare data directory and default DB path
RUN mkdir -p /data
ENV DB_PATH=/data/app.db

# Optional defaults for agent auto-run (can override in deploy.sh / cloud env)
ENV RUN_AGENT_ON_START=1
ENV AGENT_INTERVAL_SECONDS=0
# If you want to force real chain only: ENV FORCE_ONCHAIN=1

# Expose FastAPI port
EXPOSE 8000

# Entrypoint
CMD ["/app/start.sh"]

