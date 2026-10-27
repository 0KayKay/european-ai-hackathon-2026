import {
	cleanup,
	fireEvent,
	render,
	screen,
	within,
} from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import {
	cloneLevel,
	emailReviewHard,
	hiddenMailboxHard,
	moveGuardrail,
} from "../fixtures";
import { mockReactFlow } from "../testing/reactflow";
import { ArenaGraph, GraphLegend } from "./ArenaGraph";

beforeAll(mockReactFlow);
afterEach(cleanup);

const flowNode = (id: string) =>
	document.querySelector<HTMLElement>(
		`.react-flow__node[data-id="${CSS.escape(id)}"]`,
	);
const flowEdge = (id: string) =>
	document.querySelector(`[data-testid="rf__edge-${CSS.escape(id)}"]`);

describe("ArenaGraph", () => {
	it("renders every top-level node and the members inside their groups", () => {
		render(<ArenaGraph graph={hiddenMailboxHard.graph} />);

		for (const id of [
			"attacker",
			"guardrails:user->agent",
			"agent",
			"tools",
			"guardrails:tool->agent",
			"guardrails:agent->output",
			"data:drive",
			"success",
			"output",
		])
			expect(flowNode(id), id).not.toBeNull();
		// Members are rows, not nodes of their own.
		expect(flowNode("guardrail:input-judge")).toBeNull();
		expect(screen.getByText("Attacker agent")).toBeInTheDocument();
		expect(screen.getByText("hidden-mailbox")).toBeInTheDocument();
		const tools = flowNode("tools")!;
		for (const tool of [
			"list_folders",
			"list_files_of_folder",
			"get_file_content",
		])
			expect(within(tools).getByText(tool)).toBeInTheDocument();
	});

	it("places a guardrail in the group of its position and moves it with the config (AC-2)", () => {
		const level = cloneLevel(hiddenMailboxHard);
		const { rerender } = render(<ArenaGraph graph={level.graph} />);

		expect(
			within(flowNode("guardrails:user->agent")!).getByText("Input judge"),
		).toBeInTheDocument();
		expect(
			within(flowNode("guardrails:agent->output")!).queryByText("Input judge"),
		).toBeNull();

		const moved = moveGuardrail(level.graph, "input-judge", "agent->output");
		rerender(<ArenaGraph graph={moved} />);

		expect(
			within(flowNode("guardrails:agent->output")!).getByText("Input judge"),
		).toBeInTheDocument();
		expect(
			within(flowNode("guardrails:user->agent")!).queryByText("Input judge"),
		).toBeNull();
		// The input sanitizer stays where it was.
		expect(
			within(flowNode("guardrails:user->agent")!).getByText("Input sanitizer"),
		).toBeInTheDocument();
	});

	it("creates a new group when a guardrail moves to an unoccupied position", () => {
		const moved = moveGuardrail(
			cloneLevel(hiddenMailboxHard).graph,
			"input-judge",
			"around:get_file_content",
		);
		render(<ArenaGraph graph={moved} />);
		const group = flowNode("guardrails:around:get_file_content");
		expect(group).not.toBeNull();
		expect(within(group!).getByText("Input judge")).toBeInTheDocument();
		expect(
			within(group!).getByText("around get_file_content"),
		).toBeInTheDocument();
	});

	it("shows the reply loop back to the attacker on a direct multi-turn level", () => {
		render(<ArenaGraph graph={hiddenMailboxHard.graph} />);
		expect(flowEdge("output->attacker")).not.toBeNull();
		expect(screen.getByText("next turn · ≤ 15")).toBeInTheDocument();
	});

	it("has no edge back to the attacker on an indirect level", () => {
		render(<ArenaGraph graph={emailReviewHard.graph} />);
		expect(flowNode("injection")).not.toBeNull();
		expect(flowNode("user_task")).not.toBeNull();
		const edges = [...document.querySelectorAll("[data-testid^='rf__edge-']")];
		expect(edges.length).toBeGreaterThan(5);
		expect(
			edges.some((e) => e.getAttribute("data-testid")?.endsWith("->attacker")),
		).toBe(false);
		expect(screen.queryByText(/next turn/)).toBeNull();
		// Guardrails around a tool get their own group.
		expect(
			within(flowNode("guardrails:around:send_email")!).getByText(
				"Send email judge",
			),
		).toBeInTheDocument();
	});

	it("selects nodes and member rows", () => {
		const onSelect = vi.fn();
		render(<ArenaGraph graph={hiddenMailboxHard.graph} onSelect={onSelect} />);

		fireEvent.click(screen.getByText("Input judge"));
		expect(onSelect).toHaveBeenLastCalledWith("guardrail:input-judge");
		fireEvent.click(screen.getByText("hidden-mailbox"));
		expect(onSelect).toHaveBeenLastCalledWith("agent");
		fireEvent.click(screen.getByText("get_file_content"));
		expect(onSelect).toHaveBeenLastCalledWith("tool:get_file_content");
	});

	it("lets nodes receive mouse clicks although they are neither selectable nor draggable", () => {
		render(<ArenaGraph graph={hiddenMailboxHard.graph} />);

		// React Flow would set `pointer-events: none`, so a real mouse click would hit the pane instead.
		for (const id of ["attacker", "agent", "guardrails:user->agent", "tools"])
			expect(flowNode(id)?.style.pointerEvents, id).toBe("all");
	});

	it("marks highlighted nodes for replay", () => {
		render(
			<ArenaGraph
				graph={hiddenMailboxHard.graph}
				highlight={{
					nodes: { attacker: "visited", "guardrail:input-judge": "current" },
					annotations: { "guardrail:input-judge": "SAFE · passed" },
				}}
			/>,
		);
		const judge = document.querySelector(
			'[data-node-id="guardrail:input-judge"]',
		);
		expect(judge).toHaveAttribute("data-highlight", "current");
		expect(screen.getByText("SAFE · passed")).toBeInTheDocument();
		expect(document.querySelector('[data-node-id="agent"]')).toHaveClass(
			"opacity-45",
		);
	});
});

describe("GraphLegend", () => {
	it("lists the edge kinds present in the graph", () => {
		const { unmount } = render(<GraphLegend graph={hiddenMailboxHard.graph} />);
		const legend = screen.getByRole("list", { name: "Legend" });
		expect(
			within(legend).getByText("attacker-controlled text"),
		).toBeInTheDocument();
		expect(
			within(legend).getByText("next turn (reply loop)"),
		).toBeInTheDocument();
		unmount();

		render(<GraphLegend graph={emailReviewHard.graph} />);
		expect(screen.queryByText("next turn (reply loop)")).toBeNull();
		expect(screen.getByText("success check")).toBeInTheDocument();
	});
});
