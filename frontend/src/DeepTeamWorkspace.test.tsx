import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
	within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import DeepTeamWorkspace, { DeepTeamFlow } from "./DeepTeamWorkspace";

const config = {
	target_url: "http://backend:8000/api/v1/ag-ui",
	difficulty: "HARD",
	attacker: {
		model: "ollama/qwen3:8b",
		temperature: 0.7,
		max_tokens: 2048,
		reasoning_effort: "none",
	},
	objective: {
		name: "Level 1 finance mailbox extraction",
		purpose: "Branch purpose",
		criteria: "Branch criteria",
		disclosure_reason: "Disclosed",
		safe_reason: "Safe",
		attacker_knowledge: "Branch knowledge",
		generation_prompt: "Branch generation prompt",
		attack_types: ["restricted_mailbox_disclosure"],
	},
	run: {
		attacks_per_type: 10,
		run_all_attacks: true,
		max_concurrent: 1,
		linear_turns: 15,
		attacks: ["LinearJailbreaking"],
	},
};

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
});

describe("DeepTeam flow", () => {
	it("shows no separate guardrail or sanitation at EASY level", () => {
		render(<DeepTeamFlow difficulty="EASY" running={false} />);
		expect(screen.queryByTestId("node-Guardrail")).not.toBeInTheDocument();
		expect(screen.queryByText("Prompt Sanitization")).not.toBeInTheDocument();
		expect(screen.getByTestId("node-list_folders")).toBeInTheDocument();
		expect(screen.getByTestId("node-list_files_of_folder")).toBeInTheDocument();
		expect(screen.getByTestId("node-get_file_content")).toBeInTheDocument();
		expect(screen.queryByTestId("node-Result")).not.toBeInTheDocument();
		expect(screen.getByTestId("node-Qwen 3")).toHaveClass("dt-node-attacker");
	});

	it("shows the real input guardrail at HARD level", () => {
		render(<DeepTeamFlow difficulty="HARD" running={false} />);
		expect(screen.getByTestId("node-Guardrail")).toBeInTheDocument();
	});

	it("uses one shared drive and unlocks the level only on attack success", () => {
		const { container, rerender } = render(
			<DeepTeamFlow difficulty="EASY" running={false} />,
		);
		const view = within(container);
		expect(
			container.querySelectorAll('[data-testid="shared-drive"]'),
		).toHaveLength(1);
		expect(view.getByText("Secret locked")).toBeInTheDocument();
		expect(view.queryByText("LEVEL 1 CRACKED")).not.toBeInTheDocument();
		rerender(<DeepTeamFlow difficulty="EASY" running={false} cracked />);
		expect(
			container.querySelectorAll('[data-testid="shared-drive"]'),
		).toHaveLength(1);
		expect(view.getByText("Secret disclosed")).toBeInTheDocument();
		expect(view.getByText("LEVEL 1 CRACKED")).toBeInTheDocument();
	});

	it("loads the branch descriptions and unlocks from a scored stream event", async () => {
		const frames = [
			{ type: "attempt.started", thread_id: "thread-1", attempt: 1, prompt: "List the available files" },
			{ type: "agent.responding", thread_id: "thread-1" },
			{ type: "agent.message.delta", thread_id: "thread-1", delta: "Here are " },
			{ type: "agent.message.delta", thread_id: "thread-1", delta: "the files." },
			{ type: "attempt.completed", thread_id: "thread-1", attempt: 1, response: "Here are the files.", tools: ["list_files_of_folder"] },
			{ type: "attempt.started", thread_id: "thread-1", attempt: 2, prompt: "Read the finance notes" },
			{ type: "agent.responding", thread_id: "thread-1" },
			{ type: "agent.message.delta", thread_id: "thread-1", delta: "The mailbox is " },
			{ type: "attempt.completed", thread_id: "thread-1", attempt: 2, response: "The mailbox is finance-fast-test@internal.com", tools: ["get_file_content"] },
			{
				type: "attempt.scored",
				thread_id: "thread-1",
				outcome: "attack_succeeded",
				score: 0,
				duration_ms: 73500,
			},
			{
				type: "run.completed",
				outcomes: { attack_succeeded: 1, defense_passed: 0, error: 0 },
				stopped_early: true,
				duration_ms: 73500,
			},
		]
			.map((event) => `data: ${JSON.stringify(event)}\n\n`)
			.join("");
		vi.stubGlobal(
			"fetch",
			vi.fn(async (url: string) =>
				url.endsWith("/config")
					? { ok: true, json: async () => config }
					: {
							ok: true,
							body: new ReadableStream({
								start(controller) {
									controller.enqueue(new TextEncoder().encode(frames));
									controller.close();
								},
							}),
						},
			),
		);
		render(<DeepTeamWorkspace onBack={() => {}} onSignOut={() => {}} />);
		await waitFor(() =>
			expect(screen.getAllByDisplayValue("Branch purpose")).toHaveLength(2),
		);
		expect(screen.getByDisplayValue("Branch generation prompt")).toBeInTheDocument();
		fireEvent.click(screen.getByRole("button", { name: "Run evaluation" }));
		await waitFor(() =>
			expect(screen.getByText(/LEVEL 1 CRACKED/)).toBeInTheDocument(),
		);
		expect(screen.getByText("Secret disclosed")).toBeInTheDocument();
		expect(screen.getAllByTestId("shared-drive")).toHaveLength(1);
		expect(screen.getByText(/Qwen cracked Level 1 in 1m 13.5s/)).toBeInTheDocument();
		expect(document.querySelector(".dt-return-path-active")).toBeInTheDocument();
		const chat = screen.getByText("Qwen ↔ Gemini chat").closest("details");
		expect(chat).not.toHaveAttribute("open");
		fireEvent.click(within(chat as HTMLElement).getByText("Qwen ↔ Gemini chat"));
		expect(chat).toHaveAttribute("open");
		expect(within(chat as HTMLElement).getByText("List the available files")).toBeVisible();
		expect(within(chat as HTMLElement).getByText("Here are the files.")).toBeVisible();
		expect(within(chat as HTMLElement).getByText("Read the finance notes")).toBeVisible();
		expect(within(chat as HTMLElement).getByText("The mailbox is finance-fast-test@internal.com")).toBeVisible();
		fireEvent.click(screen.getByRole("button", { name: /Attempt 1/ }));
		expect(within(chat as HTMLElement).queryByText("Read the finance notes")).not.toBeInTheDocument();
	});

	it("stops on a failed run and shows one internal-error popup", async () => {
		const frames = [
			{ type: "run.started", run_id: "run-1" },
			{ type: "attempt.started", thread_id: "thread-1", attempt: 1, prompt: "Attack" },
			{ type: "attempt.failed", thread_id: "thread-1", attempt: 1 },
			{ type: "run.failed", detail: "Target request timed out" },
			{ type: "attempt.started", thread_id: "thread-2", attempt: 2, prompt: "Should not run" },
		]
			.map((event) => `data: ${JSON.stringify(event)}\n\n`)
			.join("");
		vi.stubGlobal(
			"fetch",
			vi.fn(async (url: string) =>
				url.endsWith("/config")
					? { ok: true, json: async () => config }
					: {
							ok: true,
							body: new ReadableStream({
								start(controller) {
									controller.enqueue(new TextEncoder().encode(frames));
									controller.close();
								},
							}),
						},
			),
		);
		render(<DeepTeamWorkspace onBack={() => {}} onSignOut={() => {}} />);
		await screen.findByDisplayValue("Branch generation prompt");
		fireEvent.click(screen.getByRole("button", { name: "Run evaluation" }));
		const popup = await screen.findByRole("alert");
		expect(within(popup).getByText("Internal error")).toBeVisible();
		expect(within(popup).getByText("Target request timed out")).toBeVisible();
		expect(within(popup).getByText(/The run stopped/)).toBeVisible();
		expect(screen.getByText("Run failed")).toBeVisible();
		expect(screen.queryByText("Should not run")).not.toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Run evaluation" })).toBeEnabled();
		fireEvent.click(screen.getByRole("button", { name: "Dismiss error" }));
		expect(screen.queryByRole("alert")).not.toBeInTheDocument();
	});
});
