FROM registry.access.redhat.com/ubi9/python-314-minimal:9.8-1790838707 AS builder

WORKDIR /opt/app-root/src
COPY pyproject.toml uv.lock README.md ./
COPY gateway/ gateway/
COPY runtime/ runtime/
COPY tools/ tools/

RUN pip install --no-cache-dir uv && \
    uv export --no-dev --frozen --no-hashes \
      | grep -v -E '^(nvidia-|cuda-|triton)' \
      > requirements.txt && \
    uv pip install -r requirements.txt --torch-backend cpu

ENV HF_HOME=/opt/app-root/hf-cache
RUN python -c \
    "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

FROM registry.access.redhat.com/ubi9/python-314-minimal:9.8-1790838707

COPY --from=builder /opt/app-root /opt/app-root
COPY --from=builder /opt/app-root/hf-cache /opt/app-root/hf-cache

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/app-root/hf-cache \
    GATEWAY_HOST=0.0.0.0 \
    GATEWAY_PORT=18789

WORKDIR /opt/app-root/src
COPY gateway/ gateway/
COPY runtime/ runtime/
COPY tools/ tools/
COPY templates/ templates/
COPY web/ web/
COPY cli.py env_config.py config.yaml ./

COPY workspace/SOUL.md workspace/AGENTS.md workspace/MEMORY.md.example \
     workspace/USER.md.example workspace-seed/

RUN mkdir -p workspace/memory workspace/schedules workspace/skills \
             workspace/sessions workspace/agents workspace/providers \
             workspace/knowledge workspace/chroma workspace/tmp \
             workspace/agent_templates && \
    chgrp -R 0 /opt/app-root/src && chmod -R g=u /opt/app-root/src && \
    chgrp -R 0 /opt/app-root/hf-cache && chmod -R g=u /opt/app-root/hf-cache

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
