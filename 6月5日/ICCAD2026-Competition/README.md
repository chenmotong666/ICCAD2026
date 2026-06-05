# ICCAD 2026 Competition Project

This repository contains a Python agent for ICCAD 2026 Problem A. The entry point is `main.py`, with EDA logic in `eda/` and LLM tool orchestration in `agent/`.

## Setup

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

Install Yosys separately, or set `yosys_bin` in `config.yaml` to the full path of your Yosys executable.

## Configure

```bash
cp config.example.yaml config.yaml
```

Then fill in either the OpenAI or Anthropic API key. You can also leave keys out of `config.yaml` and use environment variables:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

Do not commit `config.yaml`; it is ignored by git.

## Run

Linux/macOS:

```bash
./cada0001_alpha -config config.yaml < testcase_stdin.txt
```

Windows:

```bat
cada0001_alpha.bat -config config.yaml < testcase_stdin.txt
```

Direct Python invocation also works:

```bash
python main.py -config config.yaml < testcase_stdin.txt
```

## Test

The smoke tests do not require Yosys:

```bash
python -m pytest tests
```

Full netlist I/O, optimization, and equivalence checks require Yosys.

