# Architecture cleanup sequence

Audit read completely; at verification HEAD equaled audited 9690ba5, with no post-audit fixes to reopen. Read implementation paths named in each ticket before choosing changes.

| Finding | Verified evidence | Classification |
| --- | --- | --- |
| F01 | verify.py business/planning/call switches and rubric_facts.py scenario switches | Fix together, 03 |
| F02 | operations.js lacks advertised hosted LLM stubs; live.py requires replay | Fix now, 01 |
| F03 | packages/observe/live_evidence/lifecycle read global catalog | Fix together, 02 then 03 |
| F04 | observer/native verifier/host metadata assume n8n | Defer second backend |
| F05 | providers reads provenance; staging seed is 0 | Defer second provider/config use |
| F06 | environment callbacks call contract_for | Fix together, 04 |
| F07 | lifecycle controller defaults to digest verifier | Defer second lifecycle; catalog fix in 02 |
| F08 | terminal_submission rejects native success without narrative; result uses termination | Fix together, 04 |
| F09 | fixed 600 grant/240 RPC; no evaluator duration | Fix together, 05; receipt framework deferred |
| F10 | only finish writes evidence; timer just finalizes session | Fix together, 05 |
| F11 | review-export searches container rubric layout only | Fix now, 06 |
| F12 | explicit asset suffix/module/source lists | Defer new asset types |
| F13 | generate.py rewrites invoice/order/material fields | Fix together, 03 |
| F14 | provider callable accepts Namespace/raw dict | Fix together, 04 |
| F15 | boundary provider module names explicit | Defer real second provider |
| F16 | author/worker import stdlib urlopen | Fix together, 07 |
| F17 | stale executor prose and CRM activation names | Fix touched terminology, 04; keep durable IDs |
| F18 | hosted nop ignores trial exception | Fix together, 07 |

Ordering reviewed: 01 addresses the current admission gap first; 02 establishes inputs for 03; 04 defines facts/contracts used by 05 and 06; 07 finishes policy checks before the all-scenario gate. No generic backend/provider/lifecycle/plugin framework is planned. Each implementation commit includes its targeted tests and ticket outcome. Final report follows the full unpaid eleven-scenario gate.
