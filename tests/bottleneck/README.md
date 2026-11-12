# Bottleneck measurements

Ten inputs are read from `tests/stage1/test_inputs.txt`. This folder measures the
current product Stage2 and Stage3 paths without changing product code or rerunning
ASR/FA. Results describe execution and latency, not content quality acceptance.

```bash
.venv/bin/python tests/bottleneck/test_meter.py
.venv/bin/python tests/bottleneck/run.py
.venv/bin/python tests/bottleneck/analyze.py tests/bottleneck/runs/batches/first-two
```

## Campaign directories

Open the [browser index](results/index.html) to browse the ten selected HTML
results. The standalone copies are all in `results/`. The [source run list](../README.md#영상별-결과-html)
records the corresponding originals. Both `results/` and `runs/` are local output directories.

| Directory | Original campaign ID (UTC) | Purpose |
|---|---|---|
| [`diagnostics/timeouts`](runs/diagnostics/timeouts/report.md) | `20261107T115137Z` | Initial measurement with time limits |
| [`diagnostics/unknown-calls`](runs/diagnostics/unknown-calls/report.md) | `20261107T130441Z` | Check unresolved calls and input failures |
| [`batches/first-two`](runs/batches/first-two/report.md) | `20261107T161201Z` | Batch for the first two completed inputs |
| [`batches/remaining-eight`](runs/batches/remaining-eight/report.md) | `20261107T181155Z` | Batch for the other eight inputs |
| [`recovery/rerun-two`](runs/recovery/rerun-two/report.md) | `20261108T002306Z` | Rerun for `iEMuJiVY9Oc` and `wFikV0Y7NIk` |
| [`recovery/h2wn130ozlg`](runs/recovery/h2wn130ozlg/resume_20261108T172522Z.json) | `20261108T075014Z` | Separate Stage 2 resume and Stage 3 completion for `H2WN130OzLg` |

New runs default to `runs/batches/<UTC timestamp>/` when there is no wall-clock
cutoff and to `runs/diagnostics/<UTC timestamp>/` when a cutoff is set. Use
`--run tests/bottleneck/runs/<category>/<name>` to name a run by purpose.
Saved manifests and logs retain their original paths. Use the table to resolve
those paths and pass the current directory to `analyze.py` or `measure_stage3.py`.

## Measurement method

The runner hashes and preserves original Stage1 artifacts and snapshots all product
source hashes. It uses separate work directories with fresh model-response caches.
Immutable upstream files are hard-linked to avoid copying many gigabytes; metadata
is copied. Do not edit linked source artifacts in the measurement directories.

Execution is serial on the already-loaded Qwen VLM server. Model load time is not
included; the server's existing prefix cache is not cleared. By default, phase and
stage wall-clock cutoffs are disabled (`None`) and cumulative HTTP/call budgets are
raised so Stage2 and Stage3 run to completion (optional cutoffs can be enabled via
`--phase-limit`, `--stage2-limit`, `--stage3-limit`). Single HTTP requests retain
the product's 300-second socket timeout. No interrupted request is retried by
the harness. Existing product T1 format retries and bounded Stage3 repairs remain
enabled and their actual calls are counted.

Stage3 uses the newly completed Stage2 handoff (`same_run`) when available.
Otherwise it uses the most recent existing product handoff (`historical_handoff`)
seeded with that handoff's exact Stage1 lineage, and executes the current product's
complete input validation. It copies no prior Stage3 result or inference cache.
Without a valid completed handoff, Stage3 is `blocked_upstream`; partial Stage2
output is never fabricated into valid input. `analyze.py` is automatically invoked
after each video so `analysis.json` and `report.md` stay current during long runs.

Each campaign contains:

- `manifest.json`: exact inputs, code hashes, settings, and stop policy.
- `measurements/<video>/<stage>/events.jsonl`: UTC timestamps, monotonic elapsed
  times, nested function spans, and product progress messages.
- `measurements/<video>/<stage>/result.json`: stop/failure/completion, timings,
  actual call metadata, and a snapshot of product state/plan.
- `data/`, `stage3data/`: unmodified product requests, responses, and artifacts.
- `summary.json`, `analysis.json`, `report.md`: campaign results and tables.
- `historical.json`: separate retrospective evidence, never added to new timings.

`stopped_slow` is a measurement outcome. Product state can still say `running`
because the harness intentionally does not rewrite product journals after its
cutoff. Consult the measurement result and pending call records before any manual
resume; the harness does not automatically resume or resend unknown calls.

HTTP time includes queueing, transport, prefill, and decoding. It is not a model-only
timer. Function wall times overlap; self times exclude instrumented child spans.
Stage3 input verification is included. A cutoff reports a lower bound on total
completion time and only the first phase that crossed the threshold, not proof
that it is the globally slowest phase. Historical runs can have different code;
the report labels hash equality and keeps them separate. Small instrumentation
I/O overhead is included.

The first campaign is `runs/diagnostics/timeouts/`. Its initial historical Stage3
fixture combined current Stage1 files with an older handoff, so those attempts
failed input validation before inference. `measure_stage3.py <campaign>` records a separate
`validated_stage3` pass using the selected handoff's exact Stage1 lineage. The
analyzer uses that pass for Stage3 and retains the excluded original attempts.
