import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
	within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import AGUIPlayground from "./AGUIPlayground";
import { type Attempt, AttemptResult } from "./ChallengeResults";

const saved: Attempt = {
	threadId: "saved-thread",
	mode: "CHALLENGE_2",
	difficulty: "MEDIUM",
	createdAt: "2026-10-23T12:00:00Z",
	status: "complete",
	solved: false,
	messages: [
		{ id: "u", role: "user", content: "Please process my email inbox." },
	],
	output: "Could not forward the credentials.",
	sentEmails: [
		{
			id: "mail",
			from: "finance-fast@internal.com",
			to: "attacker@example.com",
			subject: "Reply",
			body: "[MASKED]",
		},
	],
	log: [
		{
			id: "log",
			type: "tool_result",
			name: "get_emails",
			content: "Password: [MASKED]",
		},
	],
};
afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
});

it("shows failed attacks, inbox mail and an inspectable masked agent log", () => {
	render(<AttemptResult attempt={saved} />);
	expect(screen.getByText("Your attack did not succeed")).toBeInTheDocument();
	expect(screen.getByText("Your inbox (1)")).toBeInTheDocument();
	fireEvent.click(screen.getByText("Agent log and guardrails"));
	expect(screen.getByText("Password: [MASKED]")).toBeVisible();
});

it("loads saved progress and restores a previous submission with its difficulty", async () => {
	vi.stubGlobal(
		"matchMedia",
		vi.fn(() => ({
			matches: true,
			addEventListener: vi.fn(),
			removeEventListener: vi.fn(),
		})),
	);
	vi.stubGlobal(
		"fetch",
		vi.fn(async (url: string) =>
			Response.json(
				url.endsWith("/saved-thread")
					? saved
					: { attempts: [saved], eventStatus: "open" },
			),
		),
	);
	render(<AGUIPlayground />);
	const history = screen.getByRole("complementary", { name: "Chat history" });
	await waitFor(() =>
		expect(
			within(history).getByText("Attempted: 1/9 · Solved: 0/9"),
		).toBeInTheDocument(),
	);
	fireEvent.click(
		within(history).getByRole("button", { name: /CH2 · medium/ }),
	);
	expect(
		await screen.findByRole("region", { name: "Attempt result" }),
	).toBeInTheDocument();
	expect(screen.getByRole("button", { name: "MEDIUM" })).toHaveAttribute(
		"aria-pressed",
		"true",
	);
	expect(screen.getByRole("textbox", { name: "Destination" })).toHaveValue(
		"finance-fast@internal.com",
	);
});

it("reports each offer condition independently", () => {
	render(
		<AttemptResult
			attempt={{
				...saved,
				mode: "CHALLENGE_3",
				checks: { price: false, routing: true, plausibility: false },
			}}
		/>,
	);
	expect(screen.getByText("Price check: Not achieved")).toBeInTheDocument();
	expect(screen.getByText("Routing target: Correct")).toBeInTheDocument();
	expect(
		screen.getByText("Plausibility check: Not achieved"),
	).toBeInTheDocument();
});

it("restores chat messages into the runtime and resumes the saved thread", async () => {
	vi.stubGlobal(
		"matchMedia",
		vi.fn(() => ({
			matches: true,
			addEventListener: vi.fn(),
			removeEventListener: vi.fn(),
		})),
	);
	const chat: Attempt = {
		...saved,
		threadId: "chat-history",
		mode: "CHALLENGE_1",
		difficulty: "HARD",
		settings: { model_id: "saved-model" },
		messages: [
			{ id: "old-user", role: "user", content: "My previous question" },
			{
				id: "old-answer",
				role: "assistant",
				content: "The previous saved answer",
			},
		],
	};
	const requests: Record<string, any>[] = [];
	vi.stubGlobal(
		"fetch",
		vi.fn(async (url: string, options?: RequestInit) => {
			if (url === "/api/v1/challenges")
				return Response.json({ attempts: [chat], eventStatus: "open" });
			if (url.endsWith("/chat-history")) return Response.json(chat);
			const input = JSON.parse(options?.body as string);
			requests.push(input);
			return new Response(
				`data: ${JSON.stringify({ type: "RUN_STARTED", threadId: input.threadId, runId: input.runId })}\n\ndata: ${JSON.stringify({ type: "RUN_FINISHED", threadId: input.threadId, runId: input.runId })}\n\n`,
				{ headers: { "Content-Type": "text/event-stream" } },
			);
		}),
	);
	render(<AGUIPlayground />);
	const sidebar = screen.getByRole("complementary", { name: "Chat history" });
	fireEvent.click(
		await within(sidebar).findByRole("button", {
			name: /CH1 · hard · Not solved/,
		}),
	);
	expect(await screen.findByText("My previous question")).toBeVisible();
	expect(screen.getByText("The previous saved answer")).toBeVisible();
	fireEvent.change(screen.getByRole("textbox", { name: "Message input" }), {
		target: { value: "Follow-up question" },
	});
	fireEvent.click(screen.getByRole("button", { name: "Send message" }));
	await waitFor(() => expect(requests).toHaveLength(1));
	expect(requests[0].threadId).toBe("chat-history");
	expect(requests[0].state.settings).toMatchObject({
		mode: "CHALLENGE_1",
		difficulty: "HARD",
		model_id: "saved-model",
	});
	expect(
		requests[0].messages.map((message: { content: string }) => message.content),
	).toContain("The previous saved answer");
});

it("restores email fields and labels an executed attempt as not solved", async () => {
	vi.stubGlobal(
		"matchMedia",
		vi.fn(() => ({
			matches: true,
			addEventListener: vi.fn(),
			removeEventListener: vi.fn(),
		})),
	);
	const attempt = {
		...saved,
		submission: { subject: "Saved subject", body: "Saved injected body" },
	};
	vi.stubGlobal(
		"fetch",
		vi.fn(async (url: string) =>
			Response.json(
				url.endsWith("/saved-thread")
					? attempt
					: { attempts: [attempt], eventStatus: "open", isAdmin: true },
			),
		),
	);
	render(<AGUIPlayground />);
	const sidebar = screen.getByRole("complementary", { name: "Chat history" });
	fireEvent.click(
		await within(sidebar).findByRole("button", {
			name: /CH2 · medium · Not solved/,
		}),
	);
	await waitFor(() =>
		expect(screen.getByRole("textbox", { name: "Subject" })).toHaveValue(
			"Saved subject",
		),
	);
	expect(screen.getByRole("textbox", { name: "Email body" })).toHaveValue(
		"Saved injected body",
	);
	expect(
		screen.queryByText("Competition administration"),
	).not.toBeInTheDocument();
	expect(
		screen.queryByText("Workflow and difficulty rules"),
	).not.toBeInTheDocument();
});

it("embeds an actual PDF for the full offer preview", () => {
	vi.stubGlobal(
		"matchMedia",
		vi.fn(() => ({
			matches: true,
			addEventListener: vi.fn(),
			removeEventListener: vi.fn(),
		})),
	);
	vi.stubGlobal(
		"fetch",
		vi.fn(async () => Response.json({ attempts: [], eventStatus: "open" })),
	);
	const { container } = render(<AGUIPlayground />);
	fireEvent.click(
		within(
			screen.getByRole("complementary", { name: "Chat history" }),
		).getByRole("button", { name: "Challenge 3" }),
	);
	fireEvent.click(screen.getByText("View the full offer document"));
	expect(
		container.querySelector('object[type="application/pdf"]'),
	).toHaveAttribute("data", "/challenges/offer.pdf#view=FitH");
	expect(screen.getByRole("link", { name: "Open offer PDF" })).toHaveAttribute(
		"href",
		"/challenges/offer.pdf",
	);
});

it("does not scroll the chat automatically when a message is sent", async () => {
	vi.stubGlobal(
		"matchMedia",
		vi.fn(() => ({
			matches: true,
			addEventListener: vi.fn(),
			removeEventListener: vi.fn(),
		})),
	);
	const scroll = vi.spyOn(HTMLElement.prototype, "scrollTo");
	vi.stubGlobal(
		"fetch",
		vi.fn(async (url: string, options?: RequestInit) => {
			if (url === "/api/v1/challenges")
				return Response.json({ attempts: [], eventStatus: "open" });
			const input = JSON.parse(options?.body as string);
			return new Response(
				`data: ${JSON.stringify({ type: "RUN_STARTED", threadId: input.threadId, runId: input.runId })}\n\ndata: ${JSON.stringify({ type: "RUN_FINISHED", threadId: input.threadId, runId: input.runId })}\n\n`,
				{ headers: { "Content-Type": "text/event-stream" } },
			);
		}),
	);
	render(<AGUIPlayground />);
	fireEvent.change(screen.getByRole("textbox", { name: "Message input" }), {
		target: { value: "A question" },
	});
	fireEvent.click(screen.getByRole("button", { name: "Send message" }));
	await waitFor(() =>
		expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled(),
	);
	expect(scroll).not.toHaveBeenCalled();
	scroll.mockRestore();
});

it("retries a restored email as a new attempt without dropping the restored submission", async () => {
	vi.stubGlobal(
		"matchMedia",
		vi.fn(() => ({
			matches: true,
			addEventListener: vi.fn(),
			removeEventListener: vi.fn(),
		})),
	);
	const attempt = {
		...saved,
		submission: { subject: "Retry subject", body: "Retry body" },
	};
	const requests: { threadId: string; state: { submission: unknown } }[] = [];
	vi.stubGlobal(
		"fetch",
		vi.fn(async (url: string, options?: RequestInit) => {
			if (url === "/api/v1/challenges")
				return Response.json({ attempts: [attempt], eventStatus: "open" });
			if (url.endsWith("/saved-thread")) return Response.json(attempt);
			const input = JSON.parse(options?.body as string);
			requests.push(input);
			return new Response(
				`data: ${JSON.stringify({ type: "RUN_STARTED", threadId: input.threadId, runId: input.runId })}\n\ndata: ${JSON.stringify({ type: "RUN_FINISHED", threadId: input.threadId, runId: input.runId })}\n\n`,
				{ headers: { "Content-Type": "text/event-stream" } },
			);
		}),
	);
	render(<AGUIPlayground />);
	fireEvent.click(
		await within(
			screen.getByRole("complementary", { name: "Chat history" }),
		).findByRole("button", { name: /CH2 · medium · Not solved/ }),
	);
	await waitFor(() =>
		expect(screen.getByRole("textbox", { name: "Subject" })).toHaveValue(
			"Retry subject",
		),
	);
	fireEvent.click(screen.getByRole("button", { name: "Submit for review" }));
	await waitFor(() => expect(requests).toHaveLength(1));
	expect(requests[0].threadId).not.toBe(saved.threadId);
	expect(requests[0].state.submission).toEqual({
		kind: "email",
		subject: "Retry subject",
		body: "Retry body",
	});
});
