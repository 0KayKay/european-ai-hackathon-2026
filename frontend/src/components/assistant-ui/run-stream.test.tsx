import { HttpAgent } from "@ag-ui/client";
import {
	type AssistantRuntime,
	AssistantRuntimeProvider,
} from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import {
	act,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Thread } from "./thread";
import { useAgUiRunMessages } from "./use-agui-run-messages";

afterEach(() => vi.unstubAllGlobals());

it("extends the same thinking and tool blocks across distinct wire message IDs for a whole run", async () => {
	let runtime: AssistantRuntime;
	let controller: ReadableStreamDefaultController<Uint8Array>;
	const fetchMock = vi.fn(
		async () =>
			new Response(
				new ReadableStream<Uint8Array>({
					start(c) {
						controller = c;
					},
				}),
				{ headers: { "Content-Type": "text/event-stream" } },
			),
	);
	vi.stubGlobal("fetch", fetchMock);
	const agent = new HttpAgent({ url: "https://example.test/ag-ui" });
	function Harness() {
		const { messageRuns, activeReasoningRun, awaitingText } =
			useAgUiRunMessages(agent);
		runtime = useAgUiRuntime({ agent, showThinking: true });
		return (
			<AssistantRuntimeProvider runtime={runtime}>
				<Thread
					messageRuns={messageRuns}
					activeReasoningRun={activeReasoningRun}
					awaitingText={awaitingText}
				/>
			</AssistantRuntimeProvider>
		);
	}
	const { container } = render(<Harness />);
	const emit = async (...events: Record<string, unknown>[]) => {
		await act(async () => {
			for (const event of events)
				controller.enqueue(
					new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`),
				);
		});
	};
	const emitTool = async (index: number) => {
		await emit(
			{
				type: "TEXT_MESSAGE_START",
				messageId: `tool-message-${index}`,
				role: "assistant",
			},
			{ type: "TEXT_MESSAGE_END", messageId: `tool-message-${index}` },
			{
				type: "TOOL_CALL_START",
				toolCallId: `call-${index}`,
				toolCallName: "get_applications",
				parentMessageId: `tool-message-${index}`,
			},
			{
				type: "TOOL_CALL_ARGS",
				toolCallId: `call-${index}`,
				delta: '{"page_size":1}',
			},
		);
		const blocks = Array.from(
			container.querySelectorAll('[data-slot="tool-group-root"]'),
		);
		await emit(
			{ type: "TOOL_CALL_END", toolCallId: `call-${index}` },
			{
				type: "TOOL_CALL_RESULT",
				messageId: `result-${index}`,
				toolCallId: `call-${index}`,
				content: `{"total_count":${index}}`,
				role: "tool",
			},
		);
		expect(
			Array.from(container.querySelectorAll('[data-slot="tool-group-root"]')),
		).toEqual(blocks);
	};
	const emitReasoning = async (index: number) =>
		emit(
			{ type: "REASONING_START", messageId: `thinking-${index}` },
			{
				type: "REASONING_MESSAGE_START",
				messageId: `thinking-${index}`,
				role: "reasoning",
			},
			{
				type: "REASONING_MESSAGE_CONTENT",
				messageId: `thinking-${index}`,
				delta: `**Headline ${index}**\n\nReasoning body ${index}\n\n`,
			},
		);
	const endReasoning = async (index: number) =>
		emit(
			{ type: "REASONING_MESSAGE_END", messageId: `thinking-${index}` },
			{ type: "REASONING_END", messageId: `thinking-${index}` },
		);
	await act(async () => {
		runtime!.thread.append({
			role: "user",
			content: [{ type: "text", text: "Find EOL applications" }],
		});
	});
	await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
	await emit({ type: "RUN_STARTED", threadId: "thread", runId: "first-run" });
	expect(
		container.querySelector('[data-slot="aui_pending-dot"]'),
	).toBeInTheDocument();
	await emit({
		type: "CUSTOM",
		name: "challenge.progress",
		value: { stage: "guardrail", status: "pending" },
	});
	expect(
		container.querySelector('[data-slot="aui_pending-dot"]'),
	).toBeInTheDocument();
	expect(
		container.querySelectorAll('.aui-md[data-status="running"]'),
	).toHaveLength(1);
	await emitTool(1);
	const toolBlock = container.querySelector('[data-slot="tool-group-root"]');
	expect(toolBlock).not.toBeNull();
	fireEvent.click(toolBlock!.querySelector("button")!);
	await emitReasoning(1);
	const thinkingBlock = container.querySelector('[data-slot="reasoning-root"]');
	expect(thinkingBlock).not.toBeNull();
	expect(thinkingBlock).toHaveAttribute("data-state", "open");
	expect(
		thinkingBlock!.querySelector('[data-slot="reasoning-content"]'),
	).toHaveAttribute("aria-busy", "true");
	await endReasoning(1);
	expect(thinkingBlock).toHaveAttribute("data-state", "closed");
	expect(
		thinkingBlock!.querySelector('[data-slot="reasoning-content"]'),
	).toHaveAttribute("aria-busy", "false");
	for (let index = 2; index <= 7; index++) {
		await emitTool(index);
		if (index <= 4) {
			await emitReasoning(index);
			expect(
				container.querySelector('[data-slot="reasoning-trigger-label"]'),
			).toHaveTextContent(`Headline ${index}`);
			await endReasoning(index);
		}
		expect(
			container.querySelectorAll('[data-slot="tool-group-root"]'),
		).toHaveLength(1);
		expect(
			container.querySelectorAll('[data-slot="reasoning-root"]'),
		).toHaveLength(1);
		expect(container.querySelector('[data-slot="tool-group-root"]')).toBe(
			toolBlock,
		);
		expect(container.querySelector('[data-slot="reasoning-root"]')).toBe(
			thinkingBlock,
		);
	}
	expect(
		toolBlock!.querySelectorAll('[data-slot="tool-fallback-root"]'),
	).toHaveLength(7);
	await emit({
		type: "TEXT_MESSAGE_START",
		messageId: "answer",
		role: "assistant",
	});
	expect(
		container.querySelector('[data-slot="aui_pending-dot"]'),
	).toBeInTheDocument();
	await emit({
		type: "TEXT_MESSAGE_CONTENT",
		messageId: "answer",
		delta: " \n",
	});
	expect(
		container.querySelector('[data-slot="aui_pending-dot"]'),
	).toBeInTheDocument();
	await emit({
		type: "TEXT_MESSAGE_CONTENT",
		messageId: "answer",
		delta: "Final answer",
	});
	expect(
		container.querySelector('[data-slot="aui_pending-dot"]'),
	).not.toBeInTheDocument();
	await emit(
		{ type: "TEXT_MESSAGE_END", messageId: "answer" },
		{
			type: "RUN_FINISHED",
			threadId: "thread",
			runId: "first-run",
			outcome: { type: "success" },
		},
	);
	await act(async () => controller.close());
	await waitFor(() => expect(runtime!.thread.getState().isRunning).toBe(false));
	expect(screen.getByText("Final answer")).toBeInTheDocument();
	expect(container.querySelector('[data-slot="tool-group-root"]')).toBe(
		toolBlock,
	);
	expect(container.querySelector('[data-slot="reasoning-root"]')).toBe(
		thinkingBlock,
	);
	// The collapsed block still contains every previous reasoning segment.
	fireEvent.click(thinkingBlock!.querySelector("button")!);
	for (let index = 1; index <= 4; index++)
		expect(screen.getByText(`Reasoning body ${index}`)).toBeInTheDocument();
	// A subsequent run without a user-message separator gets its own blocks.
	await act(async () => {
		runtime!.thread.startRun({ parentId: "answer" });
	});
	await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
	await emit({ type: "RUN_STARTED", threadId: "thread", runId: "second-run" });
	await emitTool(8);
	await emitReasoning(8);
	expect(
		container.querySelectorAll('[data-slot="tool-group-root"]'),
	).toHaveLength(2);
	expect(
		container.querySelectorAll('[data-slot="reasoning-root"]'),
	).toHaveLength(2);
	await act(async () => runtime!.thread.cancelRun());
	await act(async () => controller.close());
	expect(
		container.querySelectorAll('[data-slot="tool-group-root"]'),
	).toHaveLength(2);
	expect(
		container.querySelectorAll('[data-slot="reasoning-root"]'),
	).toHaveLength(2);
});
