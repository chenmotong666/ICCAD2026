# ICCAD 2026 Problem A — cada1100_beta

LLM-Assisted Netlist Exploration and Transformation

## Quick Start

```bash
./cada1100_beta -config config.yaml
```

The launcher sets up the bundled Yosys/ABC toolchain and executes the agent binary.

## Package Structure

```
cada1100_beta/
├── cada1100_beta          # Launcher script
├── bin/cada1100_beta.bin  # PyInstaller onefile binary
├── tools/oss-cad-suite/   # Bundled Yosys + ABC toolchain
├── config.yaml            # Configuration (replace API keys for local testing)
├── agent/                 # Agent modules (source reference)
└── eda/                   # EDA backend modules (source reference)
```

## Configuration

Edit `config.yaml` to set your API keys and provider. The evaluation environment will supply its own configuration.

## Requirements

- Linux x86-64 (glibc 2.28+, e.g. AlmaLinux 8 / Rocky 8)
- Internet access for LLM API calls (OpenAI / Anthropic)
- No additional dependencies — Yosys and ABC are bundled

## Notes

- The binary is built with PyInstaller onefile mode (Python 3.11)
- All output files (logs, netlists) are written relative to the executable path
- Single-threaded execution only (per competition rules)
