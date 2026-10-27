import type { Edge, Node } from "@xyflow/react";
import type { ArenaGraph, GraphEdge, GraphNode, NodeKind } from "../types";

/**
 * Deterministic layout of the level graph (IMPLEMENTATION §4: "layout is the frontend's job").
 *
 * Group members (`guardrail:<id>`, `tool:<name>`) are rendered as rows inside their group node
 * (`guardrails:<position>`, `tools`); edges that reference a member are drawn to its group.
 *
 * Direct levels (attacker = chat user):
 *
 *     main column             tool guardrails      tools
 *     attacker                [around:<tool>]  ↗
 *     guardrails user->agent
 *     agent        ──────── tool_call ───────→      tools
 *     guardrails agent->output [tool->agent]   ↖    data…
 *     output                                         success
 *
 * Indirect levels (attacker plants data, a fixed user task triggers the agent):
 *
 *     source      tools     tool guardrails     main column
 *     attacker              [around:<tool>]     user_task
 *     injection                                 guardrails user->agent
 *     data    →   tools  ←────── tool_call ──── agent
 *                 success   [tool->agent]       guardrails agent->output
 *                                               output
 */

export const KNOWN_KINDS: readonly NodeKind[] = [
	"attacker",
	"user_task",
	"injection",
	"guardrails",
	"guardrail",
	"agent",
	"tools",
	"tool",
	"data",
	"success",
	"output",
];

/** Node kind, falling back to the stable id prefix when the backend sends an unknown kind. */
export function nodeKind(
	node: Pick<GraphNode, "id" | "kind">,
): NodeKind | null {
	if ((KNOWN_KINDS as readonly string[]).includes(node.kind))
		return node.kind as NodeKind;
	const { id } = node;
	if (id.startsWith("guardrails:")) return "guardrails";
	if (id.startsWith("guardrail:")) return "guardrail";
	if (id.startsWith("tool:")) return "tool";
	if (id.startsWith("data:")) return "data";
	if ((KNOWN_KINDS as readonly string[]).includes(id)) return id as NodeKind;
	return null;
}

/** `guardrails:user->agent` → `user->agent`. */
export const groupPosition = (groupId: string) =>
	groupId.startsWith("guardrails:")
		? groupId.slice("guardrails:".length)
		: null;

/** `user->agent` → `user → agent`, `around:send_email` → `around send_email`. */
export const formatPosition = (position: string) =>
	position.replace("->", " → ").replace(/^around:/, "around ");

export type FlowNodeType =
	| "attacker"
	| "user_task"
	| "injection"
	| "guardrails"
	| "agent"
	| "tools"
	| "data"
	| "success"
	| "output"
	| "generic";

export type FlowNodeData = {
	node: GraphNode;
	kind: NodeKind | null;
	/** Member nodes rendered as rows (guardrail groups and the tools group). */
	members: GraphNode[];
};

/**
 * React Flow node type names are prefixed: React Flow ships default styles for `input`, `output`,
 * `default` and `group` that would otherwise apply to our `output` node.
 */
export type FlowNodeTypeName = `arena_${FlowNodeType}`;
export const flowTypeName = (type: FlowNodeType): FlowNodeTypeName =>
	`arena_${type}`;

export type FlowNode = Node<FlowNodeData, FlowNodeTypeName>;

export type FlowEdgeData = {
	kind: string;
	/** Ids of the graph edges this flow edge stands for (several when members collapse to a group). */
	graphEdgeIds: string[];
};

export type FlowEdge = Edge<FlowEdgeData>;

export interface Rect {
	x: number;
	y: number;
	w: number;
	h: number;
}

// Sizes ----------------------------------------------------------------------

export const NODE_WIDTH = 240;
const GAP_X = 100;
const GAP_Y = 64;
const SIDE_GAP_Y = 28;

const GROUP_HEADER = 40;
const GROUP_PADDING = 10;
const GUARDRAIL_ROW = 42;
const TOOL_ROW = 34;

/** Handle offsets (px from the top) for horizontal edges, see `nodes.tsx`. */
export const SIDE_HANDLE_OFFSETS = { a: 30, b: 54 } as const;

function nodeHeight(type: FlowNodeType, members: number): number {
	switch (type) {
		case "guardrails":
			return (
				GROUP_HEADER + Math.max(members, 1) * GUARDRAIL_ROW + GROUP_PADDING
			);
		case "tools":
			return GROUP_HEADER + Math.max(members, 1) * TOOL_ROW + GROUP_PADDING;
		case "agent":
			return 124;
		case "user_task":
			return 116;
		case "attacker":
		case "injection":
		case "success":
			return 108;
		case "data":
		case "output":
		case "generic":
			return 100;
	}
}

function flowType(kind: NodeKind | null): FlowNodeType {
	switch (kind) {
		case "attacker":
		case "user_task":
		case "injection":
		case "guardrails":
		case "agent":
		case "tools":
		case "data":
		case "success":
		case "output":
			return kind;
		default:
			return "generic";
	}
}

// Grouping -------------------------------------------------------------------

interface TopNode {
	node: GraphNode;
	kind: NodeKind | null;
	type: FlowNodeType;
	members: GraphNode[];
	w: number;
	h: number;
}

function syntheticGroup(id: string): GraphNode | null {
	const position = groupPosition(id);
	if (position !== null)
		return { id, kind: "guardrails", label: "Guardrails", subtitle: position };
	if (id === "tools") return { id, kind: "tools", label: "Tools" };
	return null;
}

function collectTopNodes(graph: ArenaGraph) {
	const byId = new Map(graph.nodes.map((n) => [n.id, n]));
	const membersOf = new Map<string, GraphNode[]>();
	const memberToGroup = new Map<string, string>();
	const top: GraphNode[] = [];

	for (const node of graph.nodes) {
		if (node.group) {
			if (!byId.has(node.group)) {
				// Tolerate a member whose group node is missing: derive the group from its id.
				const group = syntheticGroup(node.group);
				if (group) {
					byId.set(group.id, group);
					top.push(group);
				}
			}
			if (byId.has(node.group)) {
				memberToGroup.set(node.id, node.group);
				membersOf.set(node.group, [...(membersOf.get(node.group) ?? []), node]);
				continue;
			}
		}
		top.push(node);
	}

	const tops: TopNode[] = top.map((node) => {
		const kind = nodeKind(node);
		const type = flowType(kind);
		const members = membersOf.get(node.id) ?? [];
		return {
			node,
			kind,
			type,
			members,
			w: NODE_WIDTH,
			h: nodeHeight(type, members.length),
		};
	});
	return { tops, memberToGroup };
}

// Layout ---------------------------------------------------------------------

const POSITION_ORDER = (position: string) =>
	position === "user->agent"
		? 0
		: position === "tool->agent"
			? 1
			: position === "agent->output"
				? 3
				: 2;

function layoutPositions(
	tops: TopNode[],
	channel: "direct" | "indirect",
): Map<string, Rect> {
	const rects = new Map<string, Rect>();
	const pick = (type: FlowNodeType) => tops.filter((t) => t.type === type);
	const one = (type: FlowNodeType) => pick(type)[0];
	const guardrailGroups = pick("guardrails")
		.map((t) => ({ t, position: groupPosition(t.node.id) ?? "" }))
		.sort(
			(a, b) =>
				POSITION_ORDER(a.position) - POSITION_ORDER(b.position) ||
				a.position.localeCompare(b.position),
		);
	const inGroup = (position: string) =>
		guardrailGroups.filter((g) => g.position === position).map((g) => g.t);
	const inputGroups = inGroup("user->agent");
	const outputGroups = inGroup("agent->output");
	const toolResultGroups = inGroup("tool->agent");
	const aroundGroups = guardrailGroups
		.filter((g) => g.position.startsWith("around:"))
		.map((g) => g.t);
	const otherGroups = guardrailGroups
		.filter(
			(g) =>
				![
					...inputGroups,
					...outputGroups,
					...toolResultGroups,
					...aroundGroups,
				].includes(g.t),
		)
		.map((g) => g.t);

	const attacker = one("attacker");
	const injection = one("injection");
	const userTask = one("user_task");
	const agent = one("agent");
	const output = one("output");
	const tools = one("tools");
	const success = one("success");
	const data = pick("data");
	const placedTypes = new Set<TopNode>(
		[
			attacker,
			injection,
			userTask,
			agent,
			output,
			tools,
			success,
			...data,
			...guardrailGroups.map((g) => g.t),
		].filter(Boolean),
	);
	const extra = [...tops.filter((t) => !placedTypes.has(t)), ...otherGroups];

	const direct = channel === "direct";
	const present = (items: (TopNode | undefined)[]) =>
		items.filter((t): t is TopNode => Boolean(t));

	// Main column, top to bottom. Its y values are relative; x is set once columns are known.
	const main = present(
		direct
			? [attacker, injection, ...inputGroups, agent, ...outputGroups, output]
			: [userTask, ...inputGroups, agent, ...outputGroups, output],
	);
	const mainYs = new Map<TopNode, number>();
	let y = 0;
	for (const t of main) {
		mainYs.set(t, y);
		y += t.h + GAP_Y;
	}
	const agentY = agent ? (mainYs.get(agent) ?? 0) : 0;
	const agentH = agent?.h ?? 0;

	// Tool guardrail column: around:<tool> above the agent row, tool->agent below it.
	const toolGuardYs = new Map<TopNode, number>();
	let up = agentY - SIDE_GAP_Y;
	for (const t of [...aroundGroups].reverse()) {
		up -= t.h;
		toolGuardYs.set(t, up);
		up -= SIDE_GAP_Y;
	}
	let down = agentY + agentH + SIDE_GAP_Y;
	for (const t of toolResultGroups) {
		toolGuardYs.set(t, down);
		down += t.h + SIDE_GAP_Y;
	}

	// Tools column: tools on the agent row; direct levels stack data and success below it.
	const stack = (items: TopNode[], startY: number) => {
		const ys = new Map<TopNode, number>();
		let cursor = startY;
		for (const t of items) {
			ys.set(t, cursor);
			cursor += t.h + GAP_Y;
		}
		return ys;
	};
	const toolsColumn = direct
		? present([tools, ...data, success])
		: present([tools, success]);
	const toolsYs = stack(toolsColumn, agentY);

	type Column = { items: Map<TopNode, number> };
	const columns: Column[] = [];
	const mainColumn: Column = { items: mainYs };
	const toolGuardColumn: Column = { items: toolGuardYs };
	const toolsCol: Column = { items: toolsYs };

	if (direct) {
		columns.push(mainColumn);
		if (toolGuardYs.size) columns.push(toolGuardColumn);
		if (toolsYs.size) columns.push(toolsCol);
	} else {
		// Source column: attacker → injection point → data, ending on the agent row so the
		// planted content flows straight right into the tools.
		const sourceYs = stack(data, agentY);
		let top = agentY;
		for (const t of present([injection, attacker])) {
			if (data.length || t !== injection) top -= t.h + GAP_Y;
			sourceYs.set(t, top);
		}
		for (const column of [{ items: sourceYs }, toolsCol, toolGuardColumn])
			if (column.items.size) columns.push(column);
		columns.push(mainColumn);
	}
	if (extra.length) columns.push({ items: stack(extra, 0) });

	let x = 0;
	for (const column of columns) {
		let width = 0;
		for (const [t, ty] of column.items) {
			rects.set(t.node.id, { x, y: ty, w: t.w, h: t.h });
			width = Math.max(width, t.w);
		}
		x += width + GAP_X;
	}
	return rects;
}

// Edges ----------------------------------------------------------------------

type Side = "left" | "right" | "top" | "bottom";
const OPPOSITE: Record<Side, Side> = {
	left: "right",
	right: "left",
	top: "bottom",
	bottom: "top",
};

/**
 * Picks handles so that forward edges (left→right, top→bottom) use slot `a` and backward edges
 * use slot `b`; an edge pair in opposite directions (call/result) never shares a line.
 */
export function pickHandles(source: Rect, target: Rect, kind: string) {
	if (kind === "reply_loop")
		return { sourceHandle: "s-left-a", targetHandle: "t-left-a" };
	const xOverlap =
		target.x < source.x + source.w && source.x < target.x + target.w;
	let side: Side;
	if (!xOverlap) side = target.x > source.x ? "right" : "left";
	else side = target.y >= source.y ? "bottom" : "top";
	const slot = side === "right" || side === "bottom" ? "a" : "b";
	return {
		sourceHandle: `s-${side}-${slot}`,
		targetHandle: `t-${OPPOSITE[side]}-${slot}`,
	};
}

export function replyLoopLabel(graph: ArenaGraph, edge: GraphEdge) {
	const max = graph.surface.max_turns;
	return max ? `next turn · ≤ ${max}` : (edge.label ?? "next turn");
}

// Entry point ----------------------------------------------------------------

export function buildFlow(graph: ArenaGraph): {
	nodes: FlowNode[];
	edges: FlowEdge[];
	rects: Map<string, Rect>;
} {
	const { tops, memberToGroup } = collectTopNodes(graph);
	const rects = layoutPositions(tops, graph.surface.channel);

	const nodes: FlowNode[] = tops.map((t) => {
		const rect = rects.get(t.node.id) ?? { x: 0, y: 0, w: t.w, h: t.h };
		return {
			id: t.node.id,
			type: flowTypeName(t.type),
			position: { x: rect.x, y: rect.y },
			width: rect.w,
			height: rect.h,
			data: { node: t.node, kind: t.kind, members: t.members },
			draggable: false,
			connectable: false,
			selectable: false,
			focusable: false,
		};
	});

	const resolve = (id: string) => memberToGroup.get(id) ?? id;
	const merged = new Map<string, FlowEdge>();
	for (const edge of graph.edges) {
		const source = resolve(edge.source);
		const target = resolve(edge.target);
		if (source === target) continue;
		const sourceRect = rects.get(source);
		const targetRect = rects.get(target);
		if (!sourceRect || !targetRect) continue;
		const key = `${source}|${target}|${edge.kind}`;
		const existing = merged.get(key);
		if (existing) {
			existing.data?.graphEdgeIds.push(edge.id);
			continue;
		}
		const label =
			edge.kind === "reply_loop"
				? replyLoopLabel(graph, edge)
				: (edge.label ?? undefined);
		merged.set(key, {
			id: source === edge.source && target === edge.target ? edge.id : key,
			source,
			target,
			...pickHandles(sourceRect, targetRect, edge.kind),
			label,
			type: "smoothstep",
			data: { kind: edge.kind, graphEdgeIds: [edge.id] },
		});
	}
	return { nodes, edges: [...merged.values()], rects };
}
