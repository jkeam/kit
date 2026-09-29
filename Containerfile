FROM python:3.14-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY gateway/ gateway/
COPY runtime/ runtime/
COPY tools/ tools/

RUN uv venv .venv && \
    uv export --no-dev --frozen --no-hashes \
      | grep -v -E '^(nvidia-|cuda-|triton)' \
      > requirements.txt && \
    uv pip install --python .venv -r requirements.txt --torch-backend cpu

RUN .venv/bin/python -c \
    "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

FROM python:3.14-slim

COPY --from=builder /build/.venv /opt/venv
COPY --from=builder /root/.cache/huggingface /opt/hf-cache

ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV=/opt/venv \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/hf-cache \
    GATEWAY_HOST=0.0.0.0 \
    GATEWAY_PORT=18789

WORKDIR /app
COPY gateway/ gateway/
COPY runtime/ runtime/
COPY tools/ tools/
COPY templates/ templates/
COPY web/ web/
COPY cli.py env_config.py config.yaml ./

COPY workspace/SOUL.md workspace/AGENTS.md workspace/MEMORY.md.example \
     workspace/USER.md.example /app/workspace-seed/

RUN mkdir -p workspace/memory workspace/schedules workspace/skills \
             workspace/sessions workspace/agents workspace/providers \
             workspace/knowledge workspace/chroma workspace/tmp \
             workspace/agent_templates && \
    chgrp -R 0 /app && chmod -R g=u /app && \
    chgrp -R 0 /opt/hf-cache && chmod -R g=u /opt/hf-cache

EXPOSE 18789

USER 1001

CMD ["sh", "-c", "\
  for f in SOUL.md AGENTS.md; do \
    [ ! -f workspace/$f ] && cp workspace-seed/$f workspace/$f; \
  done; \
  [ ! -f workspace/MEMORY.md ] && cp workspace-seed/MEMORY.md.example workspace/MEMORY.md; \
  [ ! -f workspace/USER.md ] && cp workspace-seed/USER.md.example workspace/USER.md; \
  mkdir -p workspace/memory workspace/schedules workspace/skills \
           workspace/sessions workspace/agents workspace/providers \
           workspace/knowledge workspace/chroma workspace/tmp \
           workspace/agent_templates; \
  exec python -m gateway.server"]
