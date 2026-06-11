# Self-Contained Submission Package

This directory is a Docker/PyInstaller packaging project for ICCAD 2026 Problem A.

The runtime image contains:

- `/app/cada0606_alpha`: PyInstaller-built executable for the Python agent.
- `yosys`: installed inside the image and available on `PATH`.
- `/app/config.yaml`: placeholder config with no real API keys.

Do not put real API keys into this directory or into the Docker image. During evaluation,
use the contest-provided config. For local testing, mount a config file at runtime.

## Build

```bash
docker build -t iccad2026-self-contained:latest .
```

## Run Interactively

```bash
docker run --rm -it iccad2026-self-contained:latest -config /app/config.yaml
```

## Run With A Local Config And Testcases

PowerShell example:

```powershell
docker run --rm -i `
  -v "E:\ICCAD\606\local_config.yaml:/app/config.yaml:ro" `
  -v "E:\ICCAD\A_release testcase_0510-20260512T200851Z-3-001\A_release testcase_0510:/app/testcases" `
  iccad2026-self-contained:latest `
  -config /app/config.yaml
```

The program reads requests from stdin. Input Verilog paths should be the paths visible
inside the container, for example `/app/testcases/test01/test01.v`.

## Export Image

If the submission flow needs a Docker image archive:

```bash
docker save -o iccad2026-self-contained_latest.tar iccad2026-self-contained:latest
```

This package already includes an exported image at:

```text
docker-image/iccad2026-self-contained_latest.tar
```

To load it on another machine:

```bash
docker load -i docker-image/iccad2026-self-contained_latest.tar
```
