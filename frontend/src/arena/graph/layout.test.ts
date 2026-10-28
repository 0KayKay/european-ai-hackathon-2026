import { describe, expect, it } from "vitest";
import {
	cloneLevel,
	emailReviewHard,
	hiddenMailboxHard,
	moveGuardrail,
} from "../fixtures";
import type { ArenaGraph } from "../types";
import { buildFlow, nodeKind, pickHandles } from "./layout";

const byId = (graph: ArenaGraph) => {
	const flow = buildFlow(graph);
	return {
		...flow,
		node: (id: string) => flow.nodes.find((n) => n.id === id),
		edge: (source: string, target: string) =>
			flow.edges.filter((e) => e.source === source && e.target === target),
	};
};

describe("buildFlow", () => {
	it("collapses group members into their group node", () => {
		const flow = byId(hiddenMailboxHard.graph);
		expect(flow.node("guardrail:input-judge")).toBeUndefined();
		expect(
			flow.node("guardrails:user->agent")?.data.members.map((m) => m.id),
		).toEqual(["guardrail:input-sanitizer", "guardrail:input-judge"]);
		expect(flow.node("tools")?.data.members).toHaveLength(3);
	});

	it("is deterministic", () => {
		const a = buildFlow(hiddenMailboxHard.graph);
		const b = buildFlow(cloneLevel(hiddenMailboxHard).graph);
		expect(a.nodes.map((n) => [n.id, n.position])).toEqual(
			b.nodes.map((n) => [n.id, n.position]),
		);
	});

	it("lays a direct level out top to bottom with tools on the right", () => {
		const flow = byId(hiddenMailboxHard.graph);
		const pos = (id: string) => flow.node(id)!.position;
		expect(pos("attacker").x).toBe(pos("agent").x);
		expect(pos("attacker").y).toBeLessThan(pos("guardrails:user->agent").y);
		expect(pos("guardrails:user->agent").y).toBeLessThan(pos("agent").y);
		expect(pos("agent").y).toBeLessThan(pos("guardrails:agent->output").y);
		expect(pos("guardrails:agent->output").y).toBeLessThan(pos("output").y);
		expect(pos("tools").x).toBeGreaterThan(pos("agent").x);
		expect(pos("tools").y).toBe(pos("agent").y);
		// Tool results pass the tool->agent guardrails between tools and agent, below the call line.
		expect(pos("guardrails:tool->agent").x).toBeGreaterThan(pos("agent").x);
		expect(pos("guardrails:tool->agent").x).toBeLessThan(pos("tools").x);
		expect(pos("guardrails:tool->agent").y).toBeGreaterThan(pos("agent").y);
	});

	it("lays an indirect level out with the injection path flowing into the agent", () => {
		const flow = byId(emailReviewHard.graph);
		const pos = (id: string) => flow.node(id)!.position;
		// attacker → injection → data stacked in one column, data on the agent row.
		expect(pos("attacker").x).toBe(pos("injection").x);
		expect(pos("injection").x).toBe(pos("data:inbox").x);
		expect(pos("attacker").y).toBeLessThan(pos("injection").y);
		expect(pos("injection").y).toBeLessThan(pos("data:inbox").y);
		expect(pos("data:inbox").y).toBe(pos("agent").y);
		expect(pos("data:inbox").x).toBeLessThan(pos("tools").x);
		expect(pos("tools").x).toBeLessThan(pos("agent").x);
		expect(pos("user_task").x).toBe(pos("agent").x);
		expect(pos("user_task").y).toBeLessThan(pos("agent").y);
		// around:<tool> guardrails sit above the call line, tool->agent below it.
		expect(pos("guardrails:around:send_email").y).toBeLessThan(pos("agent").y);
		expect(pos("guardrails:tool->agent").y).toBeGreaterThan(pos("agent").y);
	});

	it("moves a guardrail to the group of its new position (AC-2)", () => {
		const moved = byId(
			moveGuardrail(
				cloneLevel(hiddenMailboxHard).graph,
				"input-judge",
				"agent->output",
			),
		);
		expect(
			moved.node("guardrails:agent->output")?.data.members.map((m) => m.id),
		).toContain("guardrail:input-judge");
		expect(
			moved.node("guardrails:user->agent")?.data.members.map((m) => m.id),
		).toEqual(["guardrail:input-sanitizer"]);
	});

	it("derives a missing group node from a member's group id", () => {
		const graph = cloneLevel(hiddenMailboxHard).graph;
		graph.nodes = graph.nodes.filter((n) => n.id !== "guardrails:user->agent");
		const flow = byId(graph);
		expect(flow.node("guardrails:user->agent")?.data.members).toHaveLength(2);
	});

	it("draws edges to members on their group and merges duplicates", () => {
		const flow = byId(emailReviewHard.graph);
		const calls = flow.edge("agent", "tools");
		expect(calls).toHaveLength(1);
		expect(calls[0].data?.graphEdgeIds).toEqual(["agent->tool:get_emails"]);
		expect(flow.edge("guardrails:around:send_email", "tools")).toHaveLength(1);
		expect(flow.edge("data:outbox", "success")[0].data?.kind).toBe("success");
	});

	it("adds the reply loop only for the direct multi-turn level", () => {
		const direct = byId(hiddenMailboxHard.graph);
		const loop = direct.edge("output", "attacker");
		expect(loop).toHaveLength(1);
		expect(loop[0].label).toBe("next turn · ≤ 15");
		expect(loop[0]).toMatchObject({
			sourceHandle: "s-left-a",
			targetHandle: "t-left-a",
		});

		const indirect = byId(emailReviewHard.graph);
		expect(indirect.edges.some((e) => e.target === "attacker")).toBe(false);
	});

	it("uses separate handles for opposite directions", () => {
		const left = { x: 0, y: 0, w: 100, h: 100 };
		const right = { x: 200, y: 0, w: 100, h: 100 };
		expect(pickHandles(left, right, "tool_call")).toEqual({
			sourceHandle: "s-right-a",
			targetHandle: "t-left-a",
		});
		expect(pickHandles(right, left, "tool_result")).toEqual({
			sourceHandle: "s-left-b",
			targetHandle: "t-right-b",
		});
		const below = { x: 0, y: 200, w: 100, h: 100 };
		expect(pickHandles(left, below, "flow")).toEqual({
			sourceHandle: "s-bottom-a",
			targetHandle: "t-top-a",
		});
	});
});

describe("nodeKind", () => {
	it("falls back to the id prefix for unknown kinds", () => {
		expect(nodeKind({ id: "guardrail:x", kind: "llm_judge" })).toBe(
			"guardrail",
		);
		expect(nodeKind({ id: "guardrails:user->agent", kind: "group" })).toBe(
			"guardrails",
		);
		expect(nodeKind({ id: "data:drive", kind: "?" })).toBe("data");
		expect(nodeKind({ id: "agent", kind: "?" })).toBe("agent");
		expect(nodeKind({ id: "custom", kind: "?" })).toBeNull();
	});
});
