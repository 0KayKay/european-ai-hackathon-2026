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
 * Node kinds. The backend sends them as `kind`; they mirror the stable id prefixes
 * (`guardrails:<position>` → `guardrails`, `guardrail:<id>` → `guardrail`, `tool:<name>` → `tool`,
 * `data:<id>` → `data`).
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
	kind: NodeKind | (string & {});
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
}

export interface SourceFile {
	/** Path relative to the levels directory root of this level, e.g. `level.yaml`, `prompts/system.md`. */
	path: string;
	content: string;
}

export interface AttackSummary {
	type: string;
	attempts: number;
	turns?: Turns;
}

export interface RedTeamSummary {
	attacks?: AttackSummary[];
	/** Total number of red cases a run will execute. */
	cases?: number;
	max_turns?: number | null;
	attacker?: ModelSpec;
	simulator?: ModelSpec;
	evaluator?: ModelSpec;
	[key: string]: unknown;
}

export interface GreenTeamSummary {
	cases?: number;
	generator?: ModelSpec;
	[key: string]: unknown;
}

export interface LevelDetail {
	level: string;
	difficulty: Difficulty;
	title: string;
	surface: GraphSurface;
	graph: ArenaGraph;
	models: RoleModel[];
	source: { files: SourceFile[] };
	red_team: RedTeamSummary;
	green_team: GreenTeamSummary;
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
	dataset_id?: string;
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
	| { kind: "error" };

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
	decided_label?: string | null;
	turns: number;
	/** Seconds. */
	duration?: number | null;
	input_preview?: string | null;
	started_at?: string | null;
}

export interface RunSummary {
	id: string;
	level: string;
	difficulty: Difficulty;
	status: RunStatus;
	started_at: string;
	finished_at?: string | null;
	/** Seconds since start (or total duration once finished). */
	elapsed: number;
	progress: { red: Progress; green: Progress };
	counts: OutcomeCounts;
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

/** `data` of a `guardrail` event. */
export interface GuardrailEventData {
	guardrail_id: string;
	guardrail_kind: string;
	position: GuardrailPosition;
	status: GuardrailStatus;
	rule?: string | null;
	reason?: string | null;
	changed?: boolean | null;
	model?: string | null;
}

export interface RunDetail extends RunSummary {
	models: RoleModel[];
	datasets: { red?: string | null; green?: string | null };
	cases: CaseSummary[];
	live?: { case_id: string; events: CaseEvent[] } | null;
}

export interface Rate {
	/** `null` when the denominator is 0. */
	value: number | null;
	numerator: number;
	denominator: number;
}

export interface RunResults {
	rates: { attack_success: Rate; false_positive: Rate };
	matrix: {
		red: { stopped: number; got_through: number };
		green: { blocked: number; passed: number };
	};
	errors: { red: number; green: number };
	by_attack_type: {
		type: string;
		attempts: number;
		succeeded: number;
		errors: number;
	}[];
	by_decision: {
		decided_by: DecidedBy;
		label: string;
		red: number;
		green: number;
	}[];
	cases: CaseSummary[];
}

export interface CaseDetail extends CaseSummary {
	input?: string | null;
	content?: Record<string, string> | null;
	events: CaseEvent[];
	/** Level graph at run time. */
	graph: ArenaGraph;
}

export interface RunExport {
	run: RunDetail;
	models: RoleModel[];
	datasets: Record<string, unknown>;
	cases: CaseDetail[];
	[key: string]: unknown;
}
