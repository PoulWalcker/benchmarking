# Independent verifier

These generic mechanisms judge recorded evidence without importing the compiler,
operation implementations, runtime or coordinator. A selected benchmark supplies
its independent behavior, role contract, result fields, rubric and identity through
`FixtureEvaluator`; there is no benchmark lookup or default business catalog here.

`verify.plan(..., fixture=...)` states the definitions to observe. Execution records
those definitions and immutable native evidence. `verify.evaluate(..., fixture=...)`
checks that evidence matches the plan, then writes separate decisions beside it.
Benchmark entrypoints compose these calls; the native trusted worker runs them.

The verifier's input is the recorded plan/evidence plus explicitly supplied independent
contracts and callbacks. Its process checks native provenance and those obligations;
its output is acceptance and separate optional quality. Benchmark-owned evaluators
provide business rules and expected values. These files provide reusable independent verifier mechanisms.


| Module | Role |
| --- | --- |
| `verify.py` | observation plans, evidence integrity and case judging |
| `fixture.py` | explicit independent behavior and evaluation data |
| `roles.py` | bind submitted occurrences to an explicit role contract and output fields |
| `n8n_provenance.py` | tie observations and graph lineage to native n8n records |
| `refinement.py` | generic native history checks with independent callbacks |
| `rubric.py`, `rubric_facts.py` | optional quality beside acceptance, using an explicit card |

Acceptance requires independent obligations and native provenance. Evaluation never
reruns a workflow or rewrites evidence. Quality remains separate; unavailable scores
stay null. Current occurrence acceptance requires the submitted graph. Historical
formats remain readable through the normalized result reader; archived evaluator
execution is deferred. Current native re-evaluation requires the recorded source
identity and preserves original evidence.
