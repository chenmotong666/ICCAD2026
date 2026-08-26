# ICCAD 2026 Problem A - Local Development Project

This repository now contains source code only. It does not bundle a Python
runtime, a PyInstaller executable, Yosys, ABC, or an OSS CAD Suite directory.
All subsequent tests use tools installed on the local machine.

## Local requirements

- Python 3.9 or newer
- Python packages from `requirements.txt`
- Yosys and ABC installed locally; `yosys` should normally be available on
  `PATH`
- A valid OpenAI or Anthropic API key for real model-interaction tests

Install the Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Check the local EDA tools:

```bash
yosys -V
abc -h
```

If Yosys is not on `PATH`, set `yosys_bin` in the YAML configuration to its
absolute path.

## Running locally

Run directly with Python:

```bash
python main.py -config config.yaml
```

On a POSIX shell, the source wrapper is also available:

```bash
./cada1100_alpha -config config.yaml
```

The wrapper uses `python3` by default. Set `PYTHON_BIN` to select another local
interpreter:

```bash
PYTHON_BIN=python3.11 ./cada1100_alpha -config config.yaml
```

For PowerShell:

```powershell
$env:ANTHROPIC_API_KEY = "<YOUR_KEY>"
python .\main.py -config .\config.yaml
```

API keys should be supplied through `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`, or
through a private configuration file. Do not commit real keys.

For a relay service, copy `apikey.example.txt` to `apikey.txt`, then copy
`config.relay.example.yaml` to `config.relay.local.yaml`. The local key file is
loaded automatically and both local files are ignored by Git. Most relay
services are OpenAI-compatible, so use `provider: "openai"` even if the relay
offers a Claude-family model; only use `provider: "anthropic"` for a native
Anthropic `/v1/messages` endpoint.

Local netlists and prompt sequences belong under `testcases/`; see
`testcases/README.md` for the directory format.

## Project layout

```text
cada1100_alpha       Source-mode POSIX launcher
main.py              Protocol entry point
config.py            Configuration parser
config.yaml          Local example configuration
requirements.txt     Local Python dependencies
agent/               LLM client, routing, and tool schemas
eda/                 Netlist graph, transforms, Yosys integration, and writer
```

The project no longer contains `bin/`, `tools/oss-cad-suite/`, or a prebuilt
submission archive. A competition package should only be rebuilt when a new
submission artifact is explicitly required.
