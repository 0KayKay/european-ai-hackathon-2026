import {
	act,
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
	within,
} from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import App from "../App";
import {
	cloneLevel,
	emailReviewHard,
	hiddenMailboxHard,
	levelsFixture,
} from "./fixtures";
import {
	hiddenMailboxCase03,
	hiddenMailboxResults,
	hiddenMailboxRunCompleted,
} from "./fixtures/runs";
import { mockArenaFetch, requestedUrls } from "./testing/fetch";
import { mockReactFlow } from "./testing/reactflow";
import type { LevelDetail } from "./types";

vi.mock("../lib/auth-client", () => ({
	authClient: {
		useSession: () => ({
			data: { user: { name: "Ada" } },
			isPending: false,
		}),
		signOut: vi.fn(),
	},
}));
vi.mock("../AGUIPlayground", () => ({
	default: () => (
		<main aria-label="Playground">
			<a href="#/arena">Arena</a>
		</main>
	),
}));

beforeAll(mockReactFlow);
afterEach(() => {
	cleanup();
	vi.restoreAllMocks();
	window.location.hash = "";
});

/** Medium variant: same level, no guardrails. */
function withoutGuardrails(level: LevelDetail): LevelDetail {
	const medium = cloneLevel(level);
	medium.difficulty = "medium";
	const graph = medium.graph;
	graph.nodes = graph.nodes.filter(
		(n) => !n.id.startsWith("guardrail:") && !n.id.startsWith("guardrails:"),
	);
	graph.edges = [
		{
			id: "attacker->agent",
			source: "attacker",
			target: "agent",
			kind: "attacker",
		},
		{ id: "agent->tools", source: "agent", target: "tools", kind: "tool_call" },
		{
			id: "tools->agent",
			source: "tools",
			target: "agent",
			kind: "tool_result",
		},
		{ id: "agent->output", source: "agent", target: "output", kind: "flow" },
		{
			id: "output->success",
			source: "output",
			target: "success",
			kind: "success",
		},
		{
			id: "agent->attacker",
			source: "agent",
			target: "attacker",
			kind: "reply_loop",
		},
	];
	return medium;
}

const routes = {
	"/api/v1/arena/levels": levelsFixture,
	"/api/v1/arena/levels/hidden-mailbox/hard": hiddenMailboxHard,
	"/api/v1/arena/levels/hidden-mailbox/medium":
		withoutGuardrails(hiddenMailboxHard),
	"/api/v1/arena/levels/email-review/hard": emailReviewHard,
	"/api/v1/arena/runs/run-hm-1": hiddenMailboxRunCompleted,
	"/api/v1/arena/runs/run-hm-1/results": hiddenMailboxResults,
	"/api/v1/arena/runs/run-hm-1/cases/hm-03": hiddenMailboxCase03,
};

const go = (hash: string) =>
	act(() => {
		window.location.hash = hash;
		window.dispatchEvent(new HashChangeEvent("hashchange"));
	});

function renderAt(hash: string) {
	window.location.hash = hash;
	const fetch = mockArenaFetch(routes);
	render(<App />);
	return fetch;
}

const inspector = () =>
	screen.getByRole("complementary", { name: "Inspector" });
const graph = () => screen.getByTestId("arena-graph");

describe("arena routing", () => {
	it("opens the arena from the playground and goes back", async () => {
		renderAt("");
		expect(screen.getByRole("main", { name: "Playground" })).toBeVisible();

		await go("#/arena");
		expect(
			await screen.findByRole("heading", { name: "Levels" }),
		).toBeInTheDocument();
		expect(screen.getByText("Level 1 · Hidden Mailbox")).toBeInTheDocument();
		// The playground stays mounted (keeps its chat) but is hidden.
		expect(
			screen.getByRole("main", { name: "Playground", hidden: true }),
		).not.toBeVisible();

		expect(
			screen.getByRole("link", { name: /Back to playground/ }),
		).toHaveAttribute("href", "#/");
		await go("#/");
		expect(screen.queryByRole("heading", { name: "Levels" })).toBeNull();
		expect(screen.getByRole("main", { name: "Playground" })).toBeVisible();
	});

	it("restores the level view from the hash on reload", async () => {
		const fetch = renderAt("#/arena/levels/hidden-mailbox/hard");
		expect(await screen.findByText("Direct · multi-turn")).toBeInTheDocument();
		expect(requestedUrls(fetch)).toContain(
			"/api/v1/arena/levels/hidden-mailbox/hard",
		);
		expect(screen.getByRole("link", { name: "Hard" })).toHaveAttribute(
			"aria-current",
			"page",
		);
		expect(
			screen.getAllByText("Level 1 · Hidden Mailbox").length,
		).toBeGreaterThan(0);
	});

	it("restores run and case routes on reload", async () => {
		const fetch = renderAt("#/arena/runs/run-hm-1");
		expect(await screen.findByTestId("rate-attack-success")).toHaveTextContent(
			"22.2%",
		);
		expect(requestedUrls(fetch)).toContain("/api/v1/arena/runs/run-hm-1");
		cleanup();

		renderAt("#/arena/runs/run-hm-1/cases/hm-03");
		expect(
			await screen.findByRole("list", { name: "Event log" }),
		).toBeInTheDocument();
		expect(screen.getByText("Case 03")).toBeInTheDocument();

		await go("#/arena/unknown");
		expect(
			await screen.findByRole("heading", { name: "Page not found" }),
		).toBeInTheDocument();
	});

	it("links level cards to their difficulties", async () => {
		renderAt("#/arena");
		const card = (await screen.findByText("Level 2 · Email Review")).closest(
			"li",
		)!;
		expect(
			within(card).getByText("Indirect · single-turn"),
		).toBeInTheDocument();
		expect(within(card).getByRole("link", { name: "Hard" })).toHaveAttribute(
			"href",
			"#/arena/levels/email-review/hard",
		);
	});
});

describe("level view", () => {
	it("shows config source, graph and inspector for the default selection", async () => {
		renderAt("#/arena/levels/hidden-mailbox/hard");
		await screen.findByText("Direct · multi-turn");

		// Config source: level.yaml first, then referenced files.
		const tabs = within(
			screen.getByRole("tablist", { name: "Config files" }),
		).getAllByRole("tab");
		expect(tabs.map((t) => t.textContent)).toEqual(
			hiddenMailboxHard.source.files.map((f) => f.path),
		);
		expect(tabs[0]).toHaveAttribute("aria-selected", "true");
		const source = screen.getByRole("tabpanel");
		expect(source).toHaveTextContent("id: hidden-mailbox");

		// The LLM judge is selected by default and its config lines are marked.
		expect(
			within(inspector()).getByRole("heading", { name: "Input judge" }),
		).toBeInTheDocument();
		expect(source.querySelector("[data-marked]")?.textContent).toContain(
			"id: input-judge",
		);
		expect(await screen.findByText("next turn · ≤ 15")).toBeInTheDocument();
	});

	it("shows a clicked node's prompts and model in the inspector", async () => {
		renderAt("#/arena/levels/hidden-mailbox/hard");
		await screen.findByText("Direct · multi-turn");

		fireEvent.click(await within(graph()).findByText("hidden-mailbox"));
		const panel = inspector();
		expect(
			within(panel).getByRole("heading", { name: "hidden-mailbox" }),
		).toBeInTheDocument();
		expect(within(panel).getByText("System prompt")).toBeInTheDocument();
		expect(within(panel).getByText("gemini-3.8-flash")).toBeInTheDocument();
		expect(within(panel).getByText("reasoning_effort")).toBeInTheDocument();
		expect(within(panel).getByText("65536")).toBeInTheDocument();

		const toggle = within(panel).getAllByRole("button", {
			name: /Show full prompt/,
		})[0];
		expect(toggle).toHaveAttribute("aria-expanded", "false");
		fireEvent.click(toggle);
		expect(toggle).toHaveAttribute("aria-expanded", "true");
		expect(toggle).toHaveTextContent("Hide full prompt");

		// Prompt file links open the file in the config source.
		fireEvent.click(
			within(panel).getByRole("button", {
				name: "hidden-mailbox/prompts/system.md",
			}),
		);
		expect(
			screen.getByRole("tab", { name: "hidden-mailbox/prompts/system.md" }),
		).toHaveAttribute("aria-selected", "true");

		// Selecting a guardrail shows its model, policy and same-position guardrails.
		fireEvent.click(within(graph()).getByText("Input judge"));
		expect(within(panel).getByText("Policy")).toBeInTheDocument();
		expect(
			within(panel).getByText("Same position · user → agent"),
		).toBeInTheDocument();
		expect(within(panel).getByText("Input sanitizer")).toBeInTheDocument();
		expect(
			within(panel).getByText(/You are a security filter/),
		).toBeInTheDocument();
	});

	it("loads another difficulty when switching", async () => {
		const fetch = renderAt("#/arena/levels/hidden-mailbox/hard");
		await screen.findByText("Direct · multi-turn");
		expect(await within(graph()).findByText("Input judge")).toBeInTheDocument();

		expect(screen.getByRole("link", { name: "Medium" })).toHaveAttribute(
			"href",
			"#/arena/levels/hidden-mailbox/medium",
		);
		await go("#/arena/levels/hidden-mailbox/medium");
		await waitFor(() =>
			expect(requestedUrls(fetch)).toContain(
				"/api/v1/arena/levels/hidden-mailbox/medium",
			),
		);
		await waitFor(() =>
			expect(within(graph()).queryByText("Input judge")).toBeNull(),
		);
		expect(screen.getByRole("link", { name: "Medium" })).toHaveAttribute(
			"aria-current",
			"page",
		);
		// Without guardrails the agent is selected.
		expect(
			within(inspector()).getByRole("heading", { name: "hidden-mailbox" }),
		).toBeInTheDocument();
	});

	it("renders an indirect level without a reply path", async () => {
		renderAt("#/arena/levels/email-review/hard");
		expect(
			await screen.findByText("Indirect · single-turn"),
		).toBeInTheDocument();
		expect(screen.getByText("attacker never sees a reply")).toBeInTheDocument();
		const [injection] = await within(graph()).findAllByText("Injection point");
		expect(screen.queryByText(/next turn/)).toBeNull();

		fireEvent.click(injection);
		const panel = inspector();
		expect(within(panel).getByText("Delivered by")).toBeInTheDocument();
		expect(
			within(panel).getByText(
				"≤ 300 characters · Subject of the planted email.",
			),
		).toBeInTheDocument();
	});

	it("does not start a run for the previous difficulty while switching", async () => {
		window.location.hash = "#/arena/levels/hidden-mailbox/hard";
		let answerEasy: (() => void) | undefined;
		const fetch = mockArenaFetch(routes);
		const answer = fetch.getMockImplementation();
		fetch.mockImplementation((input, init) =>
			String(input) === "/api/v1/arena/levels/hidden-mailbox/easy"
				? new Promise<Response>((resolve) => {
						answerEasy = () =>
							resolve(
								new Response(JSON.stringify({ detail: "Boom" }), {
									status: 500,
								}),
							);
					})
				: (answer as typeof globalThis.fetch)(input, init),
		);
		render(<App />);
		const runButton = () =>
			screen.getByRole("button", { name: "Run evaluation" });
		await waitFor(() => expect(runButton()).toBeEnabled());

		await go("#/arena/levels/hidden-mailbox/easy");
		// The hard level is still shown while easy loads, but a run must not start for it.
		expect(runButton()).toBeDisabled();
		await waitFor(() => expect(answerEasy).toBeDefined());
		await act(async () => answerEasy?.());
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"This level could not be loaded.",
		);
		expect(runButton()).toBeDisabled();
	});

	it("shows an error with retry when the level cannot be loaded", async () => {
		renderAt("#/arena/levels/missing/easy");
		const alert = await screen.findByRole("alert");
		expect(alert).toHaveTextContent("This level could not be loaded.");
		expect(
			within(alert).getByRole("button", { name: "Try again" }),
		).toBeInTheDocument();
	});
});
