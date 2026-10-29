// Types for the arena HTTP API (docs/spec/config-driven-arena/IMPLEMENTATION.md §3–§5).
// Field names follow the backend JSON exactly (snake_case).

export const DIFFICULTIES = ["easy", "medium", "hard"] as const;
export type Difficulty = (typeof DIFFICULTIES)[number];

export type Channel = "direct" | "indirect";
export type Turns = "single" | "multi";
export type Team = "red" | "green";

/** `user->agent`, `agent->output`, `tool->agent` or `around:<tool_name>`. */
export type GuardrailPosition =
	| "user->agent"
	| "agent->output"
	| "tool->agent"
	| `around:${string}`;

// ---------------------------------------------------------------------------
// Models (§2 ModelSpec)

export interface ModelParams {
	temperature?: number;
	top_p?: number;
	frequency_penalty?: number;
	presence_penalty?: number;
	max_tokens?: number;
	reasoning_effort?: string;
	seed?: number;
	thinking?: unknown;
	extra?: Record<string, unknown>;
	[param: string]: unknown;
}

export interface ModelSpec {
	model: string;
	endpoint?: string;
	params: ModelParams;
}

/** A model bound to one LLM role, e.g. `agent`, `guardrail:input-judge`, `red_team.attacker`. */
export interface RoleModel extends ModelSpec {
	role: string;
	/** Graph node id the role belongs to, if it has one. */
	node?: string | null;
}

// ---------------------------------------------------------------------------
// Graph (§4)

export interface GraphSurface {
	channel: Channel;
	turns: Turns;
	max_turns?: number | null;
}

/**
 * Node kinds as the backend sends them (`hackathon/arena/graph.py`). Group nodes are
 * `guardrail_group` (`guardrails:<position>`) and `tool_group` (`tools`).
 */
export type WireNodeKind =
	| "attacker"
	| "user_task"
	| "injection"
	| "guardrail_group"
	| "guardrail"
	| "agent"
	| "tool_group"
	| "tool"
	| "data"
	| "success"
	| "output";

/**
 * Node kinds inside the UI. They mirror the stable id prefixes (`guardrails:<position>` →
 * `guardrails`, `guardrail:<id>` → `guardrail`, `tool:<name>` → `tool`, `data:<id>` → `data`);
 * `nodeKind()` maps the wire kinds onto them and falls back to the id prefix.
 */
export type NodeKind =
	| "attacker"
	| "user_task"
	| "injection"
	| "guardrails"
	| "guardrail"
	| "agent"
	| "tools"
	| "tool"
	| "data"
	| "success"
	| "output";

export interface NodeSetting {
	label: string;
	value: string;
}

export interface NodePrompt {
	name: string;
	/** Path relative to the level directory, e.g. `prompts/input_judge.md`. */
	file?: string | null;
	text: string;
}

export interface NodeDetails {
	settings?: NodeSetting[];
	prompts?: NodePrompt[];
	model?: ModelSpec | null;
}

export interface GraphNode {
	id: string;
	kind: WireNodeKind | NodeKind | (string & {});
	label: string;
	subtitle?: string | null;
	/** Id of the group node this node is a member of (`guardrails:<position>` or `tools`). */
	group?: string | null;
	details?: NodeDetails;
}

export type EdgeKind =
	| "attacker"
	| "flow"
	| "tool_call"
	| "tool_result"
	| "success"
	| "reply_loop";

export interface GraphEdge {
	id: string;
	source: string;
	target: string;
	kind: EdgeKind | (string & {});
	label?: string | null;
}

export interface ArenaGraph {
	surface: GraphSurface;
	nodes: GraphNode[];
	edges: GraphEdge[];
}

// ---------------------------------------------------------------------------
// Levels (§5)

export interface LevelSummary {
	id: string;
	title: string;
	description: string;
	channel: Channel;
	turns: Turns;
	difficulties: Difficulty[];
	/** Overrides version; grows with every save or reset in the UI (0: never edited). */
	version?: number;
	/** The level is served with edits saved in the UI (overrides of the repository files). */
	modified?: boolean;
	/** Saved edits that no longer load; the repository version is served meanwhile. */
	override_error?: string | null;
}

export interface SourceFile {
	/** Path relative to `backend/levels/`, e.g. `hidden-mailbox/level.yaml`, `shared/prompts/refusal.md`. */
	path: string;
	content: string;
	/** The content is an edit saved in the UI, not the repository file. */
	modified?: boolean;
	/** The file lies in the level's own directory and can be edited (`shared/` and other levels cannot). */
	editable?: boolean;
}

/** One problem of a rejected save (`PUT /levels/{level}/source` → 422). */
export interface SourceProblem {
	/** File relative to `backend/levels/`. */
	path: string;
	message: string;
	/** 1-based line, when the YAML parser or validator can tell. */
	line?: number;
}

/** Saved edits that no longer load (e.g. after a schema change). */
export interface OverrideError {
	message: string;
	errors: SourceProblem[];
	/** The stored (broken) files, to fix them in the editor. */
	files: SourceFile[];
}

/** `PUT /levels/{level}/source` body. */
export interface SaveSourceRequest {
	files: { path: string; content: string }[];
	base_version: number;
	difficulty?: Difficulty;
}

export interface AttackSummary {
	type: string;
	/** Display name of the attack type, e.g. "Prompt injection". */
	title?: string;
	attempts: number;
	turns?: Turns;
}

export interface AttackerObjective {
	purpose: string;
	criteria: string;
	attacker_knowledge: string;
}

export interface RedTeamSummary {
	attacks?: AttackSummary[];
	/** Total number of red cases a run will execute. */
	cases?: number;
	max_turns?: number | null;
	attacker?: ModelSpec;
	simulator?: ModelSpec;
	evaluator?: ModelSpec;
	objective?: AttackerObjective;
}

export interface GreenTeamSummary {
	cases?: number;
	generator?: ModelSpec;
	guidance?: { file: string | null; text: string };
}

export interface InjectionField {
	name: string;
	max_length: number;
	description?: string;
}

/** The level's attack surface (`attack_surface` of the config, resolved). */
export interface LevelSurface extends GraphSurface {
	injection?: {
		point: "user_message" | "tool_output" | (string & {});
		tool?: string | null;
		description?: string;
		fields?: InjectionField[];
	};
	/** Fixed user task of an indirect level. */
	user_task?: string | null;
}

export interface LevelDetail {
	level: string;
	difficulty: Difficulty;
	difficulties?: Difficulty[];
	title: string;
	description?: string;
	surface: LevelSurface;
	graph: ArenaGraph;
	models: RoleModel[];
	source: { files: SourceFile[] };
	red_team: RedTeamSummary;
	green_team: GreenTeamSummary;
	/** Overrides version the editor saves against (`base_version`). */
	version?: number;
	modified?: boolean;
	override_error?: OverrideError | null;
}

// ---------------------------------------------------------------------------
// Datasets (§5, R-DAT-*)

export interface DatasetSummary {
	id: string;
	name: string;
	level: string;
	team: Team;
	created_at: string;
	size: number;
	run_id: string | null;
	/** Difficulty of the run that generated it; reusable on every difficulty of the level. */
	difficulty: Difficulty;
	/** Generator roles used (`red_team.*` or `green_team.generator`). */
	models: RoleModel[];
}

export interface DatasetCase {
	id: string;
	team: Team;
	attack_type?: string | null;
	/** Direct levels: the chat message. */
	input?: string | null;
	/** Indirect levels: one value per injection field, e.g. `{ subject, body }`. */
	content?: Record<string, string> | null;
}

export interface DatasetDetail extends DatasetSummary {
	cases: DatasetCase[];
}

// ---------------------------------------------------------------------------
// Runs (§5)

export type RunStatus =
	| "generating"
	| "running"
	| "completed"
	| "stopped"
	| "failed"
	| "interrupted";

export const ACTIVE_RUN_STATUSES: readonly RunStatus[] = [
	"generating",
	"running",
];

export interface DatasetSource {
	source: "generate" | "dataset";
	dataset_id?: string | null;
}

export interface CreateRunRequest {
	level: string;
	difficulty: Difficulty;
	red: DatasetSource;
	green: DatasetSource;
}

export interface Progress {
	done: number;
	total: number;
}

export interface OutcomeCounts {
	succeeded: number;
	defended: number;
	false_positive: number;
	passed: number;
	error: number;
}

/** Red: succeeded | defended | error. Green: passed | false_positive | error. */
export type CaseOutcome =
	| "succeeded"
	| "defended"
	| "passed"
	| "false_positive"
	| "error";

export type DecidedBy =
	| { kind: "guardrail"; guardrail_id: string }
	| { kind: "success_check" }
	| { kind: "agent" }
	| { kind: "turn_limit" }
	/** `run.model_requests`, `run.tool_calls`, `conversation.model_requests`, `conversation.tool_calls`. */
	| { kind: "limit"; limit: string }
	| { kind: "error" };

/** `stopped`: interrupted by a stop request before it was decided. */
export type CaseStatus = "pending" | "running" | "done" | "stopped";

export interface CaseSummary {
	id: string;
	index: number;
	/** "07" for red cases, "G04" for green cases. */
	label: string;
	team: Team;
	attack_type?: string | null;
	/** `null` while the case is generated but not yet decided. */
	outcome: CaseOutcome | null;
	decided_by: DecidedBy | null;
	/** Display text of the deciding step, e.g. "Input judge · UNSAFE". */
	decided_label?: string | null;
	turns: number;
	/** Seconds. */
	duration?: number | null;
	input_preview?: string | null;
	started_at: string | null;
	status: CaseStatus;
	/** Why the case is an error (generation failure, invalid generated input, model failure …). */
	error: string | null;
}

export type GenerationStatus =
	| "pending"
	| "generating"
	| "done"
	| "failed"
	| "dataset";

export interface Generation {
	status: GenerationStatus;
	error: string | null;
}

export interface RunRates {
	attack_success: Rate;
	false_positive: Rate;
}

export interface RunSummary {
	id: string;
	level: string;
	difficulty: Difficulty;
	/** Level title at run time; the UI falls back to the level id. */
	title?: string | null;
	status: RunStatus;
	started_at: string;
	finished_at?: string | null;
	/** Seconds since start (or total duration once finished). */
	elapsed: number;
	progress: { red: Progress; green: Progress };
	counts: OutcomeCounts;
	/** Both rates from `counts` (R-RES-1, errors excluded), as `GET /runs/{id}/results` returns them. */
	rates?: RunRates;
	/** Run-level failure text (status `failed`). */
	error: string | null;
	/** Case generation per team; `failed` e.g. when DeepTeam is unreachable. */
	generation: { red: Generation; green: Generation };
}

export interface CaseEvent {
	seq: number;
	/** Seconds since case start. */
	t: number;
	/** ISO timestamp. */
	at: string;
	turn: number;
	kind: CaseEventKind;
	node: string | null;
	edge: string | null;
	data: Record<string, unknown>;
}

export type CaseEventKind =
	| "attacker_message"
	| "injection"
	| "user_task"
	| "guardrail"
	| "model_call"
	| "tool_call"
	| "tool_result"
	| "agent_reply"
	| "success_check"
	| "limit"
	| "error";

export type GuardrailStatus = "pending" | "passed" | "blocked" | "sanitized";

/** `data` of an `attacker_message` event (red attack or green benign message). */
export interface AttackerMessageData {
	text: string;
	team: Team;
}

/** `data` of a `guardrail` event. */
export interface GuardrailEventData {
	guardrail_id: string;
	guardrail_kind: string;
	guardrail_name?: string;
	position: GuardrailPosition;
	status: GuardrailStatus;
	rule?: string | null;
	reason?: string | null;
	changed?: boolean | null;
	model?: string | null;
	/** Shared by the pending and the final event of one check. */
	check_id?: string;
	/** The judge timed out or failed (its `on_error` decided). */
	failed?: boolean;
}

/** `data` of a `success_check` event; same shape as `CaseDetail.success`. */
export interface SuccessCheckData {
	met: boolean;
	/** Success-check kind, e.g. `secret_in_output`. */
	kind: string;
	detail: Record<string, unknown>;
}

export interface RunDetail extends RunSummary {
	/** Level graph at run time, for the live sample; the UI falls back to the level graph. */
	graph?: ArenaGraph | null;
	models: RoleModel[];
	datasets: { red?: string | null; green?: string | null };
	/** Where the cases come from, as requested. */
	sources: { red: DatasetSource; green: DatasetSource };
	cases: CaseSummary[];
	live?: { case_id: string; events: CaseEvent[] } | null;
}

export interface Rate {
	/** Fraction 0…1 (`numerator / denominator`); `null` when the denominator is 0. */
	value: number | null;
	numerator: number;
	denominator: number;
}

export interface AttackTypeResult {
	type: string;
	title: string;
	turns: Turns | null;
	attempts: number;
	succeeded: number;
	defended: number;
	errors: number;
}

export interface DecisionResult {
	decided_by: DecidedBy;
	label: string;
	red: number;
	green: number;
}

export interface RunResults {
	rates: RunRates;
	matrix: {
		red: { stopped: number; got_through: number };
		green: { blocked: number; passed: number };
	};
	errors: { red: number; green: number };
	/** Outcome counts of the decided cases. */
	counts: OutcomeCounts;
	by_attack_type: AttackTypeResult[];
	by_decision: DecisionResult[];
	cases: CaseSummary[];
}

export interface CaseUsage {
	model_requests: number;
	tool_calls: number;
	input_tokens: number;
	output_tokens: number;
}

export interface CaseDetail extends CaseSummary {
	/** Single-turn direct input; `null` for multi-turn cases (see `messages`) and indirect cases. */
	input?: string | null;
	content?: Record<string, string> | null;
	/** Multi-turn: the attacker messages so far. */
	messages: string[] | null;
	success: SuccessCheckData | null;
	usage: CaseUsage | null;
	events: CaseEvent[];
	/** Level graph at run time. */
	graph: ArenaGraph;
}

export interface RunExport {
	run: RunDetail;
	models: RoleModel[];
	datasets: { red: DatasetDetail | null; green: DatasetDetail | null };
	snapshot: { level: Record<string, unknown>; graph: ArenaGraph };
	results: RunResults;
	cases: Omit<CaseDetail, "graph">[];
}
