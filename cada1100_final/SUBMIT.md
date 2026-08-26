# Contest submission (packed tarball)

Run the launcher, not the PyInstaller binary:

```
./cada1100_final -config config.yaml
```

The launcher prepends `tools/oss-cad-suite/bin` to `PATH` and points `ABC`
at `bin/yosys-abc` (the +x wrapper).  Invoking `bin/cada1100_final.bin`
directly may miss the bundled Yosys.

Do not give execute permission to `tools/oss-cad-suite/lib/yosys-abc`.
DC and Conformal LEC are optional host modules (`module load dc lc` /
`module load conformal`); failures stay on stderr and never enter `#RESPONSE`.
