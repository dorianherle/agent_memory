# Continue a study after an input change

```sh
python examples/research_resume/run_demo.py --output /tmp/research-resume-demo
```

Use an output directory that does not exist. The example creates its own local
memory store and experiment files. It disables inherited embedding settings.

1. Measure a one-dimensional cavity at length 1.0.
2. Save the result with its config, samples, and report as evidence files.
3. Change the length to 1.7 after the save.
4. Start a new Python process.
5. Recover a memory packet that marks the saved result as stale.
6. Measure the current geometry and check observed convergence order.
7. Compare the extrapolated result with the separate analytical solution.
8. Save the verified result and the next research action.

The output includes config.json, samples.json, report.json, resume-packet.txt,
and demo-result.json. The numerical model is a fixed second-order finite
difference example. It does not measure a physical device. The analysis
function is specific to the generated grids.

This is a deterministic process demonstration. It makes no model calls. The
fresh-agent study is a separate test of research method recovery.
