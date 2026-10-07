# Deferred architecture

Verified against 9690ba5 before implementation. These are real extension costs, not current execution defects.

| Finding | Why real | Why not now | Revisit when |
| --- | --- | --- | --- |
| F04 | Evidence decoding, corruption probes, timing estimates, UI and host solution identity assume n8n beyond WorkflowBackend. | Only one real backend exists; preserve strict native proof. | A second real execution backend is implemented. |
| F05 | AutoWFBench uses provenance as configuration; seed 0 is fixed. | Current scenarios need exactly these inputs; provider can own sidecars. | A second provider needs validated provider configuration or a real multi-seed experiment. |
| F07 | LifecycleController defaults to digest acceptance. | Only one lifecycle use case exists, with an injected verifier seam. Catalog propagation is the sole required change now. | A second real lifecycle task needs a different operational verifier. |
| F12 | New private SQL/CSV/provider assets need deliberate packaging and provenance enrollment. | Existing result-determining assets are covered; a universal asset framework adds unused policy. | A real provider introduces new asset types; test identity and candidate visibility together. |
| F15 | Isolation tests enumerate current provider modules. | Present coverage is precise; generic plugin discovery has no second implementation to validate. | A second real provider is registered, including packaged isolation/native controls. |

Keep intentional pins, independent verifier arithmetic/schema checks, stable protocol paths and schema identifiers, source and prompt hashes, explicit resource limits, provider internals and benchmark-owned truth. F09 transport receipt extraction is deferred until a second provider needs it; current lifetime inconsistencies are in ticket 05.
