-- Sapiens: navigation and workload notation
-- Revised 2026-10-03 from the slide and Admin's clarifications.
-- Design notation, not the current Python implementation.
module SapiensSpecNotation where

-- rTernarity: recursively split meaningful responsibilities into three.
-- Distinguish composition, alternatives, and UI views. Do not fabricate a
-- third state or initiator: Pulse has two states; workflows have two origins.
-- Leaves remain leaves until there is a meaningful reason to subdivide.

-- 1. Navigation ----------------------------------------------------------
-- These are UI sections, not ownership or execution-state declarations.
-- Work replaces the old Tasks/Memo navigation with three menus.
data Navigation = Chat | Work WorkMenu | Updates UpdateMenu
  deriving (Eq, Show)
data WorkMenu = Profile ProfileMenu | Tasks TaskMenu | Automation AutomationMenu
  deriving (Eq, Show)
data ProfileMenu = Memo | Kit | Workspace deriving (Eq, Show)
data TaskMenu = Adhoc | WorkflowTasks | Agile deriving (Eq, Show)
data AutomationMenu = Scheduler SchedulerMenu | Callback | Workflow WorkflowMenu
  deriving (Eq, Show)
data SchedulerMenu = RoutineMenu | CronMenu | PulseMenu deriving (Eq, Show)
data WorkflowMenu = PipelineMenu | GanttMenu | WBSMenu deriving (Eq, Show)
data UpdateMenu = Runtime | Events | Archived deriving (Eq, Show)

-- Profile is the work-related menu: memory, capabilities, working environment.
-- WorkflowTasks shows work being carried out through workflows; Automation /
-- Workflow holds workflow definitions and their builder. These are linked
-- views, not duplicate copies of the work (proposed UI relationship).

-- 2. Work ----------------------------------------------------------------
-- IDs refer to saved objects; Text is an intentionally unexpanded leaf.
type Text = String
type SapiId = Text
type TaskId = Text
type WorkflowId = Text
type AttemptId = Text
type RuleId = Text
type EventId = Text

data Actor = Admin | Sapi SapiId | System deriving (Eq, Show)

data Task = MkTask
  { taskId :: TaskId
  , assignee :: Actor
  , content :: Text
  , form :: TaskForm
  } deriving (Eq, Show)

data TaskForm
  = AdhocTask AdhocKind
  | WorkflowTask WorkflowRef
  | AgileTask AgileStage
  deriving (Eq, Show)

data AdhocKind = Issue | Mention | Inquiry deriving (Eq, Show)
-- Backlog / InProgress are established; the remaining board stages are open.
newtype AgileStage = AgileStage Text deriving (Eq, Show)

-- Adhoc receives immediate attention at the next Pulse. Its disposition is:
-- execute, requalify into another work form, or dismiss with a reason.
-- Attention at the next Pulse does not promise completion within that tick.
-- Agile holds planned, nonurgent work: backlog, in progress, etc.
-- Workflow tasks represent multistep work, in the sense of n8n.

data Disposition
  = Execute
  | Requalify TaskForm
  | Dismiss Text
  deriving (Eq, Show)

-- 3. Scheduler: Routine / Cron / Pulse -----------------------------------
newtype Schedule = Schedule Text deriving (Eq, Show)
newtype PromptTemplate = PromptTemplate Text deriving (Eq, Show)

-- A Routine injects a templated message into the target Sapi's chat.
-- It invokes Agency through the chat path, as a call would. Preserve the real
-- automated provenance; do not impersonate an actual human-authored message.
data Routine = MkRoutine
  { routineSchedule :: Schedule
  , routineTarget :: SapiId
  , routinePrompt :: PromptTemplate
  } deriving (Eq, Show)

-- A Cron starts a workflow by time. Its reaction policy may instead create
-- Adhoc work, retry a failed action a bounded number of times, or dismiss it.
data Cron = MkCron
  { cronId :: RuleId
  , cronSchedule :: Schedule
  , cronWorkflow :: WorkflowRef
  , cronReaction :: ReactionPolicy
  } deriving (Eq, Show)

-- Pulse is a short periodic, rule-based GTD-style workload assessment.
-- Example periods: 3 seconds or 10 seconds; these are configurable examples.
-- The classifier itself invokes neither an LLM nor a workflow.
-- Its selected work can subsequently invoke Agency or progress admitted work.
newtype Pulse = MkPulse { intervalSeconds :: Int } deriving (Eq, Show)
data PulseState = Vacant | Busy deriving (Eq, Show)

-- Proposed operational meaning: Vacant = this tick selects no new execution;
-- Busy = it selects execution. This is separate from an already running task.
-- Confirm this meaning before implementation; capacity-based meaning is open.
-- Rules inspect workload AND running activity; they cannot repeatedly start
-- the same item merely because another heartbeat occurred.
data Workload = MkWorkload
  { tasks :: [Task]
  , runningTasks :: [TaskId]
  , pendingEvents :: [EventId]
  } deriving (Eq, Show)

data Decision = MkDecision TaskId Disposition deriving (Eq, Show)
type Rules = Workload -> [Decision]

-- Pure notation for one classifier tick. The concrete GTD rules are not yet
-- specified. Dispatch is separate: classification does not perform effects.
pulse :: Rules -> Workload -> (PulseState, [Decision])
pulse rules workload =
  let decisions = rules workload
      starts (MkDecision _ Execute) = True
      starts _ = False
  in (if any starts decisions then Busy else Vacant, decisions)

-- Each tick reassesses the workload: reply to Admin, handle Adhoc, or admit
-- planned work according to explicit rules. An unchanged/running item is not
-- a fresh invocation. Priority, capacity, scope and fairness remain open.

-- 4. Callback: Hook / Condition / Reaction -------------------------------
newtype Hook = MkHook Text deriving (Eq, Show)
newtype Condition = MkCondition Text deriving (Eq, Show)

data Callback = MkCallback
  { callbackId :: RuleId
  , hook :: Hook
  , condition :: Condition
  , reaction :: ReactionPolicy
  } deriving (Eq, Show)

-- Hook receives an event; Condition gates its Reaction.
-- Three active reactions; dismissal is absence of action with a saved reason.
data Reaction
  = Trigger WorkflowRef
  | CreateAdhoc AdhocKind Text
  | Retry AttemptId
  deriving (Eq, Show)

data ReactionChoice = Act Reaction | DismissReaction Text deriving (Eq, Show)
data ReactionPolicy = MkReactionPolicy
  { choices :: [(Condition, ReactionChoice)]
  , retryLimit :: Int
  , otherwiseDismiss :: Text
  } deriving (Eq, Show)

-- Policy order/condition semantics still need definition. Retry limits must
-- be nonnegative, persist across Pulses, and terminate on exhaustion.
-- A retry creates a new attempt linked to the failed attempt; it does not
-- erase the prior result. Reconcile uncertain effects before repeating them.

-- Exactly two workflow initiation paths:
data Initiator = ByCron RuleId | ByCallbackTrigger RuleId deriving (Eq, Show)
-- Pulse admits queued starts from these paths; it is not a third initiator.
-- Retry retains the original initiation provenance. Manual requests and WBS
-- output must reach one of these paths if they are to start a workflow.
-- Which callback represents those requests remains an open routing decision.

-- 5. Workflow: Pipeline / Gantt / WBS ------------------------------------
-- Pipeline and Gantt are executable definitions. WBS creates/rebuilds work.
-- Thus this menu is a responsibility split, not three executable subtypes.
newtype Revision = Revision Int deriving (Eq, Show)
data WorkflowRef = WorkflowRef WorkflowId Revision deriving (Eq, Show)
newtype Script = Script Text deriving (Eq, Show)
newtype AgencyCall = AgencyCall Text deriving (Eq, Show)
newtype StepId = StepId Text deriving (Eq, Show)
data Dependency = After StepId StepId deriving (Eq, Show)

data Plan a = MkPlan
  { steps :: [(StepId, a)]
  , dependencies :: [Dependency]
  , acceptance :: Text
  } deriving (Eq, Show)

data WorkflowBody
  = Pipeline (Plan Script)
  | Gantt (Plan GanttStep)
  deriving (Eq, Show)
data GanttStep = ScriptStep Script | LLMStep AgencyCall deriving (Eq, Show)

-- Pipeline is scripts only, including indirect calls: no hidden LLM calls.
-- Gantt combines LLM and script work. It is an execution kind here, not merely
-- a chart. Whether every Gantt must contain both kinds is not settled.
-- Step IDs are unique, dependency endpoints exist, and cycles need an explicit
-- policy. Scheduling details and graph execution mechanics are not specified.

data WorkflowDefinition = MkWorkflowDefinition
  { workflowRef :: WorkflowRef
  , body :: WorkflowBody
  , forkedFrom :: Maybe WorkflowRef
  } deriving (Eq, Show)

-- WBS redesigns workload. Its output can contain tasks, workflows, and
-- further planned WBS work. It does not automatically execute its output.
data WBS = MkWBS
  { objective :: Text
  , sourceWork :: [WorkRef]
  , constraints :: [Text]
  } deriving (Eq, Show)
data WorkRef = TaskRef TaskId | WorkflowSource WorkflowRef | WBSRef Text
  deriving (Eq, Show)
data WBSOutput
  = PlannedTask Task
  | PlannedWorkflow WorkflowDefinition
  | PlannedWBS WBS
  deriving (Eq, Show)

-- Fix/rebuild: create a NEW workflow fork; archive the original reversibly.
-- Never rewrite the original definition or reinterpret its execution history.
-- Restoring an archive does not replay work. In-flight runs and schedule /
-- callback retargeting during replacement require an explicit policy.
-- Further WBS work is planned, not an automatic unbounded recursive invocation.

-- Open decisions --------------------------------------------------------
-- 1. Pulse scope (System / each Sapi), Vacant/Busy meaning, priority/capacity,
--    and concrete deterministic GTD rules, including unclassifiable input.
-- 2. Reaction routing: when Cron creates Adhoc instead of starting a workflow,
--    policy ordering, event deduplication, retry/backoff and dismissal rules.
-- 3. Workflow replacement: archive timing, in-flight runs, retargeting and
--    restoration; Agile stages and whether Scrum remains a distinct concept.
-- The prior organizational ontology, Agency taxonomy, Job/Cue/WorkGraph types,
-- mandatory task dates and run-type hierarchy are outside this replacement.
-- Their omission is not a runtime migration or deletion of persisted data.
