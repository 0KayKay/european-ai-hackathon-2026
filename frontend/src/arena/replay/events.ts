import { formatEventTime } from "../format";
import type {
	EdgeHighlight,
	GraphHighlight,
	NodeHighlight,
} from "../graph/context";
import type { ArenaGraph, CaseEvent, GuardrailEventData, Team } from "../types";

const str = (value: unknown): string | undefined =>
	typeof value === "string" && value ? value : undefined;

export const guardrailData = (event: CaseEvent) =>
	event.data as Partial<GuardrailEventData>;

/** Whether a guardrail event comes from an LLM guardrail (judge verdicts are SAFE/UNSAFE). */
const isJudge = (data: Partial<GuardrailEventData>) =>
	Boolean(data.model) ||
	data.guardrail_kind === "llm_judge" ||
	data.guardrail_kind === "tool_authorization";

/** Short graph annotation for a guardrail decision, e.g. "SAFE · passed" or "no changes". */
export function guardrailAnnotation(event: CaseEvent): string {
	const data = guardrailData(event);
	const status = data.status ?? "pending";
	if (isJudge(data)) {
		if (status === "passed") return "SAFE · passed";
		if (status === "blocked") return "UNSAFE · blocked";
		return status;
	}
	if (status === "sanitized")
		return data.changed === false
			? "no changes"
			: data.changed
				? "sanitized"
				: status;
	return status;
}

/**
 * Graph highlight for a case after the event at `index`. Every node and edge seen so far in the
 * current turn is visited (each turn of a multi-turn case is a new pass through the graph), a
 * blocking guardrail stays `blocked`, a met success check is an `alert`, and the current event's
 * node and edge are `current`.
 */
export function highlightAt(
	events: CaseEvent[],
	index: number,
): GraphHighlight {
	const nodes: Record<string, NodeHighlight> = {};
	const edges: Record<string, EdgeHighlight> = {};
	const annotations: Record<string, string> = {};
	const last = Math.min(index, events.length - 1);
	const turn = events[last]?.turn;
	let first = last;
	while (first > 0 && events[first - 1].turn === turn) first--;
	for (let i = Math.max(first, 0); i <= last; i++) {
		const event = events[i];
		if (event.edge) edges[event.edge] = "visited";
		if (!event.node) continue;
		const node = event.node;
		if (nodes[node] !== "blocked" && nodes[node] !== "alert")
			nodes[node] = "visited";
		switch (event.kind) {
			case "guardrail":
				annotations[node] = guardrailAnnotation(event);
				if (guardrailData(event).status === "blocked") nodes[node] = "blocked";
				break;
			case "tool_call":
				annotations[node] = formatEventTime(event.t);
				break;
			case "attacker_message":
			case "injection":
				annotations[node] = formatEventTime(event.t);
				break;
			case "success_check":
				annotations[node] = event.data.met ? "met" : "not met";
				if (event.data.met) nodes[node] = "alert";
				break;
			case "error":
				nodes[node] = "alert";
				break;
		}
	}
	const current = events[last];
	if (current?.node && nodes[current.node] === "visited")
		nodes[current.node] = "current";
	if (current?.edge) edges[current.edge] = "current";
	return { nodes, edges, annotations };
}

/** Label of a graph node by id, e.g. `guardrail:input-judge` → "Input judge". */
export function nodeLabel(graph: ArenaGraph | undefined, id: string) {
	return graph?.nodes.find((n) => n.id === id)?.label;
}

export type EventTone =
	| "attacker"
	| "user"
	| "guardrail-ok"
	| "guardrail-block"
	| "tool"
	| "agent"
	| "success"
	| "fail"
	| "muted";

/** Heading and tone of an event in the log. */
export function eventHeading(
	event: CaseEvent,
	team: Team,
	graph?: ArenaGraph,
): { title: string; tone: EventTone } {
	const data = event.data;
	switch (event.kind) {
		case "attacker_message":
			return team === "red"
				? { title: "Attacker · message", tone: "attacker" }
				: { title: "User · message (benign)", tone: "user" };
		case "injection":
			return team === "red"
				? { title: "Attacker · planted content", tone: "attacker" }
				: { title: "Planted content (benign)", tone: "user" };
		case "user_task":
			return { title: "User task", tone: "user" };
		case "guardrail": {
			const g = guardrailData(event);
			const label =
				(event.node ? nodeLabel(graph, event.node) : undefined) ??
				(g.guardrail_id
					? nodeLabel(graph, `guardrail:${g.guardrail_id}`)
					: undefined) ??
				g.guardrail_id ??
				"Guardrail";
			return {
				title: `${label} · ${g.status ?? "pending"}`,
				tone: g.status === "blocked" ? "guardrail-block" : "guardrail-ok",
			};
		}
		case "model_call":
			return {
				title: `Model call${str(data.model) ? ` · ${data.model}` : ""}`,
				tone: "muted",
			};
		case "tool_call":
			return { title: "Tool call", tone: "tool" };
		case "tool_result":
			return { title: "Tool result", tone: "tool" };
		case "agent_reply":
			return { title: "Agent · reply", tone: "agent" };
		case "success_check":
			return data.met
				? { title: "Success check · met", tone: "fail" }
				: { title: "Success check · not met", tone: "success" };
		case "limit":
			return { title: "Limit reached", tone: "fail" };
		case "error":
			return { title: "Error", tone: "fail" };
		default:
			return { title: String(event.kind), tone: "muted" };
	}
}
