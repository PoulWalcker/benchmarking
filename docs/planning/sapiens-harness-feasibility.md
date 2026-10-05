# Sapiens harness feasibility

Preparation only, inspected 2026-10-04. No upstream code, model, installer,
container, or desktop action was executed. The recommended first experiment is
an unpaid Linux/Harbor smoke test of the existing Sapiens conversation runtime.
Replacing n8n with a Sapiens workflow executor is a separate, currently blocked
proposal: the pinned repository implements conversations and delegation, while
its declarative WorkFlows/SapiHarness/AgencyRun remain future capabilities.
[Runtime entry](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/sapiens/runtime/turns.py#L13-L77),
[explicit capability status](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/documentation/specs/CLI.md#L70).

## Revisions and evidence

The lab specification stays at `06ddd3333109cea8a2cb3071609070d7a3c0d3ff`.
Pinned source was downloaded through GitHub's archive API and inspected as text.
On 2026-10-04, `GET /repos/aleski-green/sapiens4/commits/main` resolved to that
same SHA, with committer timestamp `2026-10-03T15:55:14Z`; thus there was no
newer main revision to compare in this inspection. This was a direct API read,
not a search snippet. Any future adoption must resolve a fresh SHA and show
material specification/runtime differences first.
[Pinned commit](https://github.com/aleski-green/sapiens4/commit/06ddd3333109cea8a2cb3071609070d7a3c0d3ff).

The pinned tree's `blindly4` gitlink resolves to
`6f64f69c53047b311654e3d09b4d5abf25734d5f`; its sources were inspected separately.
Harbor tag `v0.21.0` resolves through annotated tag
`b9d7d83aa2f75dc3733197eb7bad83783b27ca69` to commit
`64afbbcb62165950301e1a6407c729aa26d844ff`. Its `agents/base.py` matched the
installed 0.21.0 file byte-for-byte. Links below use these exact revisions.
[Sapiens tree](https://github.com/aleski-green/sapiens4/tree/06ddd3333109cea8a2cb3071609070d7a3c0d3ff),
[Harbor release](https://github.com/harbor-framework/harbor/releases/tag/v0.21.0).

## Three distinct experiments

| Experiment | Existing implementation | Missing work and interpretation |
| --- | --- | --- |
| **A. Sapiens authors lab YAML** | A conversation accepts text and can return an exact string through the host/CLI | Add a Harbor agent adapter and explicit prompt transport; retain the lab compiler, n8n, and independent verifier. A pass evaluates authoring, not Sapiens execution of YAML. |
| **B. Sapiens executes a workflow instead of n8n** | The lab has a typed backend seam; upstream has a conversational `TurnRunner` | No supported `sapi-lab/v0` loader/executor was found in the inspected runtime. WorkFlow specification notation and exported task YAML are not executable support. A new semantic adapter/runtime and native evidence checker would be substantive implementation. |
| **C. Harbor evaluates the Sapiens harness** | Harbor can load a custom agent; upstream exposes injectable model factories and host interfaces | A deterministic conversation smoke can test submission, completion, failure, persistence, and cleanup without an LLM or computer. It proves integration plumbing, not model quality or workflow semantics. |

This separation follows the lab's [profile](../PROFILE.md),
[backend interface](../ARCHITECTURE.md#adding-another-executor), and
[recorded results](../RESULTS.md). Upstream's Haskell file explicitly identifies
itself as design notation; actual runtime admission accepts `chat` or `computer`.
[Notation](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/documentation/haskell-notation-specs/SapiensSpecNotation.hs#L1-L4),
[runtime admission](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/sapiens/runtime/turns.py#L21-L37).

## Implemented entry points and constraints

- `python3 -m sapiens --port PORT --data-dir PATH` starts the local host;
  `./sapiens4 chat "..." --sapi chief --wait --format json --url ...` is its
  one-shot client. One-shot use does not need `prompt_toolkit`; interactive
  terminal use does. These are existing entry points, not commands run during
  this research. [Host entry](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/sapiens/__main__.py#L12-L44),
  [CLI contract](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/documentation/specs/CLI.md).
- The HTTP host binds `127.0.0.1`. `GET /api/state` returns agents/turns;
  `POST /api/agents/<id>/messages` accepts a message and returns a queued turn
  identifier. JSON writes require `X-Sapiens-Local: 1`; Host and Origin checks
  are local-only. A remote bridge is therefore new integration work, not an
  existing exposed endpoint. [Server](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/sapiens/corpora/host/server.py#L14-L54),
  [routes](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/sapiens/corpora/host/server.py#L79-L126).
- `Service(data_dir, factory_builder=..., start_worker=False)` is an implemented
  injection seam. A factory supplies `spawn(LLMSpec)` and an object with
  `complete(prompt) -> str`; upstream integration tests use `ScriptedFactory`
  and isolated temporary data. This permits a small external Harbor adapter
  while retaining the real host/turn persistence. Do not infer the actual
  provider from `/api/health` or snapshot `provider`: both report `codex` even
  with an injected factory. Record the chosen factory independently.
  [Composition](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/sapiens/corpora/host/service.py#L51-L139),
  [scripted fixture](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/tests/test_integration.py#L14-L98),
  [snapshot label](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/sapiens/corpora/host/service.py#L293-L304).
- **Authoring input does not fit unchanged.** `Service.submit` caps message text
  at 16,000 characters. Current lab generation packages contain 19,626
  (invoice), 19,668 (routing), and 19,876 (research) characters, measured from
  the task text plus current FORMAT/profile/catalog and section delimiters.
  Attachments are supported, but the prompt receives file references rather
  than inline file contents. File reading changes the original prompt-only
  condition; silent truncation is unacceptable. [Submission limit](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/sapiens/corpora/host/service.py#L230-L265),
  [attachments](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/sapiens/corpora/sapis/attachments.py#L17-L89).
- A real conversation can include multiple decision/model invocations. The
  `Assessing` response can finish directly in `Repl` mode, or progress through
  execution/delegation; it is not automatically one model call per task.
  [Decision loop](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/sapiens/corpora/host/delegation.py#L273-L315).

## Harbor contract and platform fit

Harbor 0.21.0's external-agent interface requires `name()`, `version()`, async
`setup(environment)`, and async `run(instruction, environment, context)`.
`--agent module.path:ClassName` is supported without modifying Harbor.
The environment supplies lifecycle, `exec(command, cwd, env, timeout_sec, user)`,
and file/directory upload/download. Use these existing seams; a new environment
provider is unnecessary for the first Linux test.
[Agent interface](https://github.com/harbor-framework/harbor/blob/64afbbcb62165950301e1a6407c729aa26d844ff/src/harbor/agents/base.py#L169-L239),
[custom import](https://github.com/harbor-framework/harbor/blob/64afbbcb62165950301e1a6407c729aa26d844ff/src/harbor/agents/factory.py#L110-L165),
[environment interface](https://github.com/harbor-framework/harbor/blob/64afbbcb62165950301e1a6407c729aa26d844ff/src/harbor/environments/base.py#L900-L953).

Task packages retain `instruction.md`, `task.toml`, environment files, and a
verifier script writing reward to `/logs/verifier/reward.txt`. Set an explicit
execution deadline and `network_mode = "no-network"` for the deterministic
smoke; Harbor's default network policy is public. Unknown token/cost values stay
unset in `AgentContext`, with a separate explicit synthetic-provider label.
[Task contract](https://github.com/harbor-framework/harbor/blob/64afbbcb62165950301e1a6407c729aa26d844ff/docs/content/docs/tasks/index.mdx),
[agent context](https://github.com/harbor-framework/harbor/blob/64afbbcb62165950301e1a6407c729aa26d844ff/src/harbor/models/agent/context.py).

| Environment | Source-supported assessment |
| --- | --- |
| Linux/Docker | Sapiens' launcher explicitly permits chat on non-macOS; its Python host uses Unix facilities including `fcntl`. A no-computer-use scripted-provider run is a viable hypothesis for a disposable Linux container, not a runtime result established by this memo. |
| macOS desktop | **Blindly4**, not “Blightly,” is a Swift executable using native Accessibility/AppKit. Its package requires macOS 13+ and Swift tools 6.0. Live AX commands require permission for the launching process. |
| Harbor `apple-container` | Runs lightweight **Linux** VMs on Apple silicon; this does not provide macOS Accessibility. |
| Harbor `use-computer` / `cua-cloud` | Both pinned implementations recognize `macos`. `use-computer` implements macOS SSH/AX execution and requires its optional SDK/credentials. Available VM images, Swift version, AX permission inheritance, and compatibility with Blindly4 were not tested. These are candidates, not a reason to provision paid infrastructure now. |

[Cross-platform launcher](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/start.sh),
[Blindly package](https://github.com/aleski-green/blindly4/blob/6f64f69c53047b311654e3d09b4d5abf25734d5f/Package.swift),
[AX permission check](https://github.com/aleski-green/blindly4/blob/6f64f69c53047b311654e3d09b4d5abf25734d5f/Sources/blindly4/Accessibility/AXElement.swift#L273-L276),
[Apple containers](https://github.com/harbor-framework/harbor/blob/64afbbcb62165950301e1a6407c729aa26d844ff/src/harbor/environments/apple_container.py#L29-L47),
[use-computer](https://github.com/harbor-framework/harbor/blob/64afbbcb62165950301e1a6407c729aa26d844ff/src/harbor/environments/use_computer.py#L124-L184),
[Cua platforms](https://github.com/harbor-framework/harbor/blob/64afbbcb62165950301e1a6407c729aa26d844ff/src/harbor/environments/cua_cloud.py#L66-L74).

Blindly4 provides `tree`, `find`, `inspect`, `focused`, and guarded input commands
through a stateless CLI. Sapiens invokes its binary through
`sapiens/computer/commands.py`, acquires shared-computer ownership, and bounds
reads. Neither a Docker bind mount nor a container running on a Mac grants this
desktop capability. A controlled bridge to a dedicated macOS session would be
a separate trust boundary: it must own identity, permitted operations, exclusive
desktop use, timeouts, audit, and reset. Do not expose or mount the personal
desktop by default. [Blindly interface](https://github.com/aleski-green/blindly4/blob/6f64f69c53047b311654e3d09b4d5abf25734d5f/README.md),
[Sapiens wrapper](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/sapiens/computer/commands.py#L55-L91).

## Proposed tickets, in dependency order

### H1 — Deterministic Harbor/Sapiens conversation smoke

**Depends on:** frozen upstream/Harbor revisions above; existing local checks.
Independent of the generated-YAML/live-runtime pilot.

Implement one custom Harbor agent and one Linux task. Run the pinned `Service`
with an injected scripted factory inside the task environment, a new data
directory, and loopback `Server(0, service)` when exercising HTTP. Submit a short
nonce-bearing chat, return it through the `Assessing`/`Repl` decision contract,
and save the actual terminal turn/output. Copy only required public upstream
sources/assets/prompts; no user data, Codex credentials, desktop, or Docker socket.

**Pass:** exact output and completed persisted turn; exactly one scripted
invocation; restart preserves output without replay; deliberately failing
provider yields a failed turn; nop/missing output gets reward 0; clean shutdown.
**Fail/stop:** unexpected subprocess/model invocation, outbound access, timeout,
external state path, or reward accepted solely from an agent's self-report.
Use a hard 60-second smoke deadline and zero retries. Label results synthetic;
this is not evidence of live Sapiens intelligence or YAML execution.

### H2 — Sapiens YAML-authoring transport and adapter

**Depends on:** H1; a chosen prompt transport and later explicit live-call budget.

First implement unpaid tests for the 16,000-character constraint, scoped document
attachment staging, complete catalog/profile hashes, exact returned YAML bytes,
and failure/timeout preservation. Recommended candidate: short message plus
immutable reference documents inside the isolated task filesystem. Record that
this is a tool-enabled authoring condition; do not compare it as identical to
the existing prompt-only pilot. Run returned YAML with the current n8n backend
and independent verifier. Keep this separate from the generated-YAML/live-runtime
plan, which measures execution-time model operations.

**Pass:** no reference solutions/test answers in supplied inputs; every required
document is staged unmodified; valid/invalid synthetic answers receive their
expected scores; source and prompt hashes are reproducible. **Fail/stop:**
truncation, answer repair, fallback to reference YAML, ambiguous submission retry,
or an unbounded delegated model-call chain.

Before any live phase, explicitly choose the provider adapter and enforce a call
budget outside Sapiens. Its default `CodexFactory` uses
`--dangerously-bypass-approvals-and-sandbox`; a real run therefore requires an
isolated container/VM with scoped access, not execution on the user's desktop.
The installer/preflight also makes a real model call and is excluded from unpaid
setup. [Codex invocation](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/sapiens/runtime/codex.py#L145-L162),
[preflight](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/sapiens/preflight.py#L28-L55).

### H3 — Workflow-executor decision gate

**Depends on:** an explicit decision to investigate B; neither H1 nor H2
establishes that capability.

Prepare a small mapping for the invoice Pipeline: input binding, deterministic
Script operation, dependencies, failure, output, and independent execution
evidence. Locate an executable upstream interface for each item, or mark it
missing. At this pin the gate is blocked; do not implement a misleading adapter
that asks a conversational model to impersonate execution records.

**Pass:** either a pinned runnable upstream contract plus a minimal parity plan,
or an explicit scoped decision to build a new executor. **Stop:** only notation,
task-export YAML, or model-authored claims support a required semantic rule.
Any new backend must satisfy the lab's business criteria and provide its own
provenance checker, rather than reusing n8n-specific evidence fields.

## Decisions left open

The unpaid H1 smoke needs no product choice. Before H2 goes live, select the
authoring transport/comparison condition and provider/budget. Computer use is an
optional separate track: choose a disposable macOS VM/provider or dedicated
test session, then verify Swift/AX and a harmless fixture app before proposing
any bridge. No account connection, paid VM, installer, or personal desktop
access is implied by this preparation. Future implementation commits should use
Conventional Commits.
