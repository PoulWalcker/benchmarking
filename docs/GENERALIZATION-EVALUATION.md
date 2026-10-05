# Two finite composition experiments

These tasks test whether model-authored YAML can compose the existing catalog into new input/output relationships while allowing different safe graphs. They do not test invention of new operation implementations. The compiler, catalog, pinned Sapiens source and static profile remain unchanged.

| Task | Business obligation | Accepted composition examples | Declared model-occurrence cap |
| --- | --- | --- | --- |
| billing-bulletin-packet | Validated invoice total plus source-grounded article preview | Separate branches; serialization with a redundant ancestor edge; direct object output in place of report/preview wrappers | 1 |
| two-audience-briefs | Separate reports for two audience sources and the same product | Shared or separate product analysis; combine operations or inline objects; safe serialization, guards and redundant edges | 6 |

Examples are controls, not an exhaustive graph whitelist. The verifier interprets symbolic input and operation origins, accepts references and nested object composition, and checks the actual native graph separately. It requires every consumed value to be available in that case. Unused model work within the public cap is reported as inefficiency; skipped work must have no native dispatch. Logical actor permissions are checked against the generic least-operation rule disclosed in the task.

The catalog supplies invoice validation/arithmetic, digest preparation and specialized product/marketing/writing instructions. The model chooses operations, reuse, dependencies, bindings and final structure. This is evidence about composition of predefined domain operations, not arbitrary workflow invention or proof that all semantically equivalent programs are recognizable.

Independent acceptance calculates monetary sums/counts with Python integers, checks source/validation origins, validates all article IDs and actual digest preservation, and compares evidence to the actual corresponding source and writer inputs. Prose checks use explicit fixture facts, common paraphrases and bounded contradiction/absent-fact probes. They are **lexical coverage checks**, not general factual entailment or exhaustive contradiction detection. The final live outputs also need recorded independent agent content review; no human approval or hidden model judge is implied.

Each task has two different manual control DAGs in `generation/generalization/controls/`. Before paid authoring, both execute through native Harbor oracle controls on two inputs, followed by nop controls. The verifier also runs engine-successful literal-output and input-bypass mutations, empty-source rejection and, for billing, mixed-currency rejection. Local JavaScript probes test composition without claiming native n8n evidence.

The bounded sequence is G1 authoring twice, G1 live on two cases, G2 authoring twice, G2 live on two cases. Each authoring is a fresh one-shot wrapper call with no feedback or repair; the first accepted original is selected only after both attempts pass. The public prompt bytes freeze before fresh private IDs, amounts and source markers are created. Returned YAML, copied verifier submission, prompt, private corpus and runtime source hashes must agree.

The immutable series has **4 authoring attempts and at most 14 runtime wrapper attempts**, with zero runner retries. G1 admits one declared occurrence per case; G2 admits six per case. The latter may execute fewer calls through sharing or skipped work. Exact observed native requests must reconcile with outgoing Agency audit records. Provider-internal calls/retries remain unknown. A failed/unknown attempt or failed final phase stops the series; it is not automatically resumed or re-created.

Use the same absolute series directory on the host and inside the isolated container because the reused admission ledger retains absolute report paths. For example, choose a new `/private/tmp/sapi-generalization-UNIQUE` directory. Build the isolated lab image from the frozen source, then run on the host:

```sh
python -m sapi_config_lab.experiments.generalization \
  --series-dir /private/tmp/sapi-generalization-UNIQUE \
  --scenario billing-bulletin-packet --phase author \
  --upstream http://127.0.0.1:8765/run --image sapi-config-lab-n8n:2.41.5
```

Copy the complete frozen source and series directory into the isolated lab container, retaining that exact absolute series path. Set `PYTHONPATH` and `SAPI_LAB_ROOT` to the copied source. Run inside the container:

```sh
python -m sapi_config_lab.experiments.generalization \
  --series-dir /private/tmp/sapi-generalization-UNIQUE \
  --scenario billing-bulletin-packet --phase live \
  --upstream http://host.docker.internal:8765/run
```

Preserve all artifacts and copy the finalized checkpoint, including `series.json`, back to the same absolute host directory. Only after G1 passes, repeat those two commands with `--scenario two-audience-briefs`. Do not overlap model activity with another experiment. Runtime applies the frozen private input case and a recorded 600-second deadline overlay; the original selected YAML bytes remain preserved.

The experiment uses the existing `ExpansionSeries` admission interface, Harbor `WrapperYamlAgent`, catalog Agency transport and native `run_case` backend. Its new namespace does not register aliases in the original scenario registry or change default suites. Failed runs retain prompts, raw submissions, native exports/records, controls, audits and reports. `qualitative_review` stays pending until the independent reviewer actually examines the generated prose.
