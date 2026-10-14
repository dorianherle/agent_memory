FROM node:22-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-dev g++ git && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install numpy==2.2.6 hnswlib==0.8.0
RUN npm install --global @openai/codex@0.155.1
ENV PATH="/opt/venv/bin:${PATH}" PYTHONDONTWRITEBYTECODE=1
RUN mkdir -p /opt/backend /agent /workspace && chmod 777 /workspace
COPY agent_memory /opt/backend/agent_memory
COPY worker.py /opt/backend/worker.py
# The task generator and oracle are never in this build context.
RUN chmod 700 /opt/backend && git config --system user.email study@localhost && git config --system user.name "Study Worker" && git config --system --add safe.directory /workspace
WORKDIR /agent
