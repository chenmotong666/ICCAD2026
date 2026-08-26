# ICCAD 2026 Problem A Alpha Submission

This directory is prepared for the alpha evaluation stage. The contest entry point is:

```bash
./cada1100_alpha -config <config_file_path>
```

The wrapper runs `bin/cada1100_alpha.bin` and prepends the bundled
`tools/oss-cad-suite/bin` directory to `PATH`, so Yosys/ABC are resolved from the
submission package instead of the host system.

## Environment Requirements

The submission is a self-contained PyInstaller package that runs on **Red Hat 8**
compatible Linux (AlmaLinux 8, Rocky Linux 8, RHEL 8, CentOS 8).

### Host System Dependencies

No additional Python packages, Yosys, or ABC installation is required on the host.
The bundled `tools/oss-cad-suite/bin` contains:

- Yosys 0.66+105 (with ABC 1.01)
- Full ABC synthesis toolchain
- All required shared libraries

The only host requirement is a standard Red Hat 8 glibc (2.28+).

### Bundled Python Dependencies (inside PyInstaller binary)

```
networkx>=3.2          # Graph algorithms (NetlistGraph core)
openai>=1.30           # OpenAI SDK
anthropic>=0.28        # Anthropic SDK
pyyaml>=6.0            # YAML config parsing
```

## Configuration

The evaluator supplies the `-config` YAML file. The included `config.yaml` is only a
local example and must not contain real API keys.

Supported provider blocks follow the contest format:

- `provider: "openai"` with model `gpt-4o-mini`
- `provider: "anthropic"` with model `claude-haiku-4-5`

API keys may also be supplied through `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`
environment variables during local testing.

Example config:
```yaml
provider: "anthropic"
anthropic:
  api_key: "<YOUR_KEY>"
  model: "claude-haiku-4-5"
generation:
  temperature: 0.2
  max_output_tokens: 4096
```

## Architecture

```
cada1100_alpha              # Shell entry point (sets PATH, invokes binary)
bin/cada1100_alpha.bin      # PyInstaller self-contained executable
tools/oss-cad-suite/        # Bundled Yosys + ABC toolchain
config.yaml                 # Example config (no real keys)
main.py                     # Entry point source (for reference)
config.py                   # Configuration parser
agent/                      # LLM agent + tool schemas
  llm_client.py             #   OpenAI/Anthropic unified client
  react_agent.py            #   ReAct agent with rule router
  tool_schema.py            #   Tool definitions + tier classification
eda/                        # EDA backend
  backend.py                #   Unified backend facade
  netlist_graph.py          #   Internal netlist graph (NetworkX)
  transformer.py            #   Netlist transformations (buffer, replace, remap)
  yosys_backend.py          #   Yosys/ABC subprocess interface
  optimizer.py              #   ABC optimization wrappers
  writer.py                 #   Verilog writer
  constants.py / decorators.py / tool_metadata.py
```

## Local Development (without PyInstaller rebuild)

```bash
# Install dependencies
pip install -r requirements.txt

# Set bundled tools PATH
export PATH="$(pwd)/tools/oss-cad-suite/bin:$PATH"
export YOSYSHQ_ROOT="$(pwd)/tools/oss-cad-suite"

# Run with Python directly
ANTHROPIC_API_KEY=<key> python3 main.py -config config.yaml
```

## Notes

- Docker images are not part of the submission format for the revised alpha Q&A.
  Do not submit a Docker image or `docker save` archive.
- The PyInstaller binary is built with `--onefile` against Python 3.9 on AlmaLinux 8.
- The evaluator's working directory is `/app`. All input/output files are relative
  to the working directory.
