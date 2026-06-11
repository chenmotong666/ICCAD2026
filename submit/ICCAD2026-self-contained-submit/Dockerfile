# syntax=docker/dockerfile:1

FROM python:3.11-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /src

RUN apt-get update \
    && apt-get install -y --no-install-recommends binutils ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt pyinstaller

COPY . .
RUN pyinstaller --clean --onefile --name cada0606_alpha \
    --paths=/src \
    --collect-submodules=agent \
    --collect-submodules=eda \
    --hidden-import=agent.llm_client \
    --hidden-import=agent.react_agent \
    --hidden-import=agent.tool_schema \
    --hidden-import=eda.backend \
    --hidden-import=eda.constants \
    --hidden-import=eda.decorators \
    --hidden-import=eda.netlist_graph \
    --hidden-import=eda.optimizer \
    --hidden-import=eda.tool_metadata \
    --hidden-import=eda.transformer \
    --hidden-import=eda.writer \
    --hidden-import=eda.yosys_backend \
    --hidden-import=openai \
    --hidden-import=anthropic \
    --hidden-import=yaml \
    --hidden-import=networkx \
    main.py

FROM debian:bookworm-slim AS runtime

ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates yosys \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /src/dist/cada0606_alpha /app/cada0606_alpha
COPY config.yaml README.md PACKAGING.md /app/

RUN chmod +x /app/cada0606_alpha \
    && yosys -V

ENTRYPOINT ["/app/cada0606_alpha"]
CMD ["-config", "/app/config.yaml"]
