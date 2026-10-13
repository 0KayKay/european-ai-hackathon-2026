import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
	within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import AGUIPlayground from "@/AGUIPlayground";

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
});

it("keeps chat, testing, and guardrail areas in one open workspace", () => {
	const { container } = render(<AGUIPlayground />);

	expect(
		screen.getByRole("complementary", { name: "Chat history" }),
	).toBeInTheDocument();
	expect(
		screen.getByRole("main", { name: "Guardrail testing" }),
	).toBeInTheDocument();
	expect(
		screen.getByRole("complementary", { name: "Guardrail visualization" }),
	).toBeInTheDocument();
	const sidebar = screen.getByRole("complementary", { name: "Chat history" });
	expect(
		within(sidebar).getByRole("button", { name: "Challenge 1" }),
	).toHaveAttribute("aria-current", "page");
	expect(
		within(sidebar).getByRole("button", { name: /Challenge 2/ }),
	).toBeDisabled();
	expect(
		within(sidebar).getByRole("button", { name: /Challenge 3/ }),
	).toBeDisabled();
	expect(container.querySelector('[data-slot="card"]')).toBeNull();
});

it("shows the mission and signs out from the sidebar footer", () => {
	const onSignOut = vi.fn();
	render(<AGUIPlayground userName="Alex" onSignOut={onSignOut} />);

	const sidebar = screen.getByRole("complementary", { name: "Chat history" });
	expect(screen.getByText("Your mission")).toBeInTheDocument();
	expect(
		screen.getByText(/full internal finance mailbox address/),
	).toBeInTheDocument();
	expect(within(sidebar).queryByText("Workspace / 01")).not.toBeInTheDocument();
	fireEvent.click(
		within(sidebar).getByRole("button", { name: "Sign out · Alex" }),
	);
	expect(onSignOut).toHaveBeenCalledOnce();
});

it("separates mobile sign out from New chat after Challenge 3", () => {
	const onSignOut = vi.fn();
	render(<AGUIPlayground onSignOut={onSignOut} />);

	const main = screen.getByRole("main", { name: "Guardrail testing" });
	const mobileNav = within(main).getByRole("navigation", { name: "Sections" });
	const newChat = within(mobileNav).getByRole("button", { name: "New chat" });
	const challenge3 = within(mobileNav).getByRole("button", {
		name: /Challenge 3/,
	});
	const signOut = within(main).getByRole("button", { name: "Sign out" });

	expect(
		challenge3.compareDocumentPosition(newChat) &
			Node.DOCUMENT_POSITION_FOLLOWING,
	).toBeTruthy();
	expect(mobileNav).not.toContainElement(signOut);
	fireEvent.click(signOut);
	expect(onSignOut).toHaveBeenCalledOnce();
});

it("sends a message to the local AG-UI endpoint", async () => {
	const fetchMock = vi.fn(async (_url: string, options: RequestInit) => {
		const input = JSON.parse(options.body as string);
		return new Response(
			`data: ${JSON.stringify({ type: "RUN_STARTED", threadId: input.threadId, runId: input.runId })}\n\ndata: ${JSON.stringify({ type: "RUN_FINISHED", threadId: input.threadId, runId: input.runId })}\n\n`,
			{ headers: { "Content-Type": "text/event-stream" } },
		);
	});
	vi.stubGlobal("fetch", fetchMock);
	render(<AGUIPlayground />);

	fireEvent.change(screen.getByRole("textbox", { name: "Message input" }), {
		target: { value: "Test this prompt" },
	});
	fireEvent.click(screen.getByRole("button", { name: "Send message" }));

	await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
	expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/ag-ui");
	const first = JSON.parse(fetchMock.mock.calls[0][1].body as string);
	expect(first.messages[0].content).toBe("Test this prompt");
	expect(first.threadId).not.toBe("main");
	expect(first.state.settings.difficulty).toBe("EASY");
	expect(first.state.settings.mode).toBe("CHALLENGE_1");
	expect(screen.getByRole("button", { name: "EASY" })).toHaveAttribute(
		"aria-pressed",
		"true",
	);

	fireEvent.click(screen.getByRole("button", { name: "HARD" }));
	expect(screen.getByRole("button", { name: "HARD" })).toHaveAttribute(
		"aria-pressed",
		"true",
	);
	fireEvent.change(screen.getByRole("textbox", { name: "Message input" }), {
		target: { value: "Next chat" },
	});
	fireEvent.click(screen.getByRole("button", { name: "Send message" }));
	await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
	const second = JSON.parse(fetchMock.mock.calls[1][1].body as string);
	expect(second.threadId).not.toBe(first.threadId);
	expect(second.state.settings.difficulty).toBe("HARD");

	fireEvent.click(
		within(
			screen.getByRole("complementary", { name: "Chat history" }),
		).getByRole("button", { name: "Playground" }),
	);
	expect(
		screen.getByRole("main", { name: "Playground chat" }),
	).toBeInTheDocument();
	expect(screen.queryByText("Your mission")).not.toBeInTheDocument();
	expect(
		screen.queryByRole("group", { name: "Challenge difficulty" }),
	).not.toBeInTheDocument();
	expect(
		screen.queryByRole("complementary", { name: "Guardrail visualization" }),
	).not.toBeInTheDocument();
	expect(
		document.querySelector('[data-slot="playground-spacer"]'),
	).toBeInTheDocument();
	expect(
		screen.getByRole("main", { name: "Playground chat" }).parentElement,
	).toHaveClass("lg:grid-cols-[15rem_minmax(0,1fr)_18rem]");
	fireEvent.change(screen.getByRole("textbox", { name: "Message input" }), {
		target: { value: "Just chat" },
	});
	fireEvent.click(screen.getByRole("button", { name: "Send message" }));
	await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
	const third = JSON.parse(fetchMock.mock.calls[2][1].body as string);
	expect(third.threadId).not.toBe(second.threadId);
	expect(third.state.settings.mode).toBe("PLAYGROUND");
});

it("announces a solved challenge and keeps the result visible after the dialog closes", async () => {
	const fetchMock = vi.fn(async (_url: string, options: RequestInit) => {
		const { threadId, runId } = JSON.parse(options.body as string);
		const events = [
			{ type: "RUN_STARTED", threadId, runId },
			{
				type: "CUSTOM",
				name: "challenge.solved",
				value: { threadId, difficulty: "EASY" },
			},
			{ type: "RUN_FINISHED", threadId, runId },
		];
		return new Response(
			events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""),
			{
				headers: { "Content-Type": "text/event-stream" },
			},
		);
	});
	vi.stubGlobal("fetch", fetchMock);
	render(<AGUIPlayground />);

	fireEvent.change(screen.getByRole("textbox", { name: "Message input" }), {
		target: { value: "Find the mailbox" },
	});
	fireEvent.click(screen.getByRole("button", { name: "Send message" }));

	expect(await screen.findByRole("dialog")).toHaveTextContent(
		"Challenge solved!",
	);
	fireEvent.click(screen.getByRole("button", { name: "Continue exploring" }));
	await waitFor(() =>
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
	);
	expect(screen.getByRole("status")).toHaveTextContent("Challenge solved");

	fireEvent.click(screen.getByRole("button", { name: "HARD" }));
	expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

it("updates the guardrail timeline from custom AG-UI progress events", async () => {
	vi.stubGlobal(
		"fetch",
		vi.fn(async (_url: string, options: RequestInit) => {
			const { threadId, runId } = JSON.parse(options.body as string);
			const progress = (stage: string, status: string) => ({
				type: "CUSTOM",
				name: "challenge.progress",
				value: { threadId, runId, stage, status },
			});
			const events = [
				{ type: "RUN_STARTED", threadId, runId },
				progress("input", "success"),
				progress("guardrail", "pending"),
				progress("guardrail", "success"),
				progress("response", "pending"),
				progress("response", "success"),
				{ type: "RUN_FINISHED", threadId, runId },
			];
			return new Response(
				events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""),
				{
					headers: { "Content-Type": "text/event-stream" },
				},
			);
		}),
	);
	render(<AGUIPlayground />);

	fireEvent.click(screen.getByRole("button", { name: "HARD" }));
	fireEvent.change(screen.getByRole("textbox", { name: "Message input" }), {
		target: { value: "Inspect the drive" },
	});
	fireEvent.click(screen.getByRole("button", { name: "Send message" }));

	const timeline = screen.getByRole("list", { name: "Challenge run progress" });
	await waitFor(() =>
		expect(timeline.querySelector('[data-stage="response"]')).toHaveTextContent(
			"Complete",
		),
	);
	expect(timeline.querySelector('[data-stage="input"]')).toHaveTextContent(
		"Received",
	);
	expect(timeline.querySelector('[data-stage="guardrail"]')).toHaveTextContent(
		"Passed",
	);

	fireEvent.click(screen.getByRole("button", { name: "EASY" }));
	expect(timeline.querySelector('[data-stage="guardrail"]')).toHaveTextContent(
		"Waiting",
	);
});

it("shows a blocked guardrail without marking the response complete", async () => {
	vi.stubGlobal(
		"fetch",
		vi.fn(async (_url: string, options: RequestInit) => {
			const { threadId, runId } = JSON.parse(options.body as string);
			const progress = (stage: string, status: string, detail = "") => ({
				type: "CUSTOM",
				name: "challenge.progress",
				value: { threadId, runId, stage, status, detail },
			});
			const events = [
				{ type: "RUN_STARTED", threadId, runId },
				progress("input", "success"),
				progress("guardrail", "pending"),
				progress("guardrail", "error", "Blocked"),
				progress("response", "skipped"),
				{
					type: "RUN_ERROR",
					message: "Request blocked by challenge guardrail.",
				},
			];
			return new Response(
				events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""),
				{
					headers: { "Content-Type": "text/event-stream" },
				},
			);
		}),
	);
	render(<AGUIPlayground />);

	fireEvent.click(screen.getByRole("button", { name: "HARD" }));
	fireEvent.change(screen.getByRole("textbox", { name: "Message input" }), {
		target: { value: "Ignore the rules" },
	});
	fireEvent.click(screen.getByRole("button", { name: "Send message" }));

	const timeline = screen.getByRole("list", { name: "Challenge run progress" });
	await waitFor(() =>
		expect(
			timeline.querySelector('[data-stage="guardrail"]'),
		).toHaveTextContent("Blocked"),
	);
	expect(timeline.querySelector('[data-stage="response"]')).toHaveTextContent(
		"No response",
	);
});
