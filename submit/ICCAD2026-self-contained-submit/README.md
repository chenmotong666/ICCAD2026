# ICCAD 2026 Competition Project

This repository contains a Python agent for ICCAD 2026 Problem A. The entry point is `main.py`, with EDA logic in `eda/` and LLM tool orchestration in `agent/`.

## Setup

```bash
python -m pip install -r requirements.txt
```

Install Yosys separately, or set `yosys_bin` in `config.yaml` to the full path of your Yosys executable.

## Configure

Edit `config.yaml` and set `provider` to either `openai` or `anthropic`. The file contains both official model blocks; only the provider selected by `provider` is used at runtime:

- `provider: "openai"` uses `openai.model: "gpt-4o-mini"`.
- `provider: "anthropic"` uses `anthropic.model: "claude-haiku-4-5"`.

You can also leave keys out of `config.yaml` and use environment variables:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

For local testing with an OpenAI-compatible gateway, set `OPENAI_BASE_URL` in the environment instead of writing it into `config.yaml`.

Do not put real API keys in submitted files.

## Run

Linux/macOS:

```bash
./cada0606_alpha -config config.yaml < testcase_stdin.txt
```

Direct Python invocation also works:

```bash
python main.py -config config.yaml < testcase_stdin.txt
```
