import type { HttpAgent } from "@ag-ui/client";
import { useEffect, useState } from "react";

// Keep wire IDs intact; remember only which run owns their presentation.
export function useAgUiRunMessages(agent: HttpAgent) {
	const [messageRuns, setMessageRuns] = useState<ReadonlyMap<string, string>>(
		new Map(),
	);
	const [activeReasoningRun, setActiveReasoningRun] = useState<string>();
	const [awaitingText, setAwaitingText] = useState(false);
	useEffect(() => {
		setMessageRuns(new Map());
		setActiveReasoningRun(undefined);
		setAwaitingText(false);
		let run: string | undefined;
		const subscription = agent.subscribe({
			onEvent: ({ event }) => {
				if (event.type === "RUN_STARTED") {
					run = `${event.threadId}:${event.runId}`;
					setActiveReasoningRun(undefined);
					setAwaitingText(true);
					return;
				}
				if (event.type === "RUN_FINISHED" || event.type === "RUN_ERROR") {
					setActiveReasoningRun(undefined);
					setAwaitingText(false);
					run = undefined;
					return;
				}
				if (!run) return;
				if (
					event.type === "TEXT_MESSAGE_CONTENT" &&
					typeof event.delta === "string" &&
					event.delta.trim()
				) {
					setAwaitingText(false);
				}
				if (
					event.type === "REASONING_START" ||
					event.type === "REASONING_MESSAGE_START" ||
					event.type === "THINKING_START" ||
					event.type === "THINKING_TEXT_MESSAGE_START"
				) {
					setActiveReasoningRun(run);
				} else if (
					event.type === "REASONING_END" ||
					event.type === "THINKING_END"
				) {
					setActiveReasoningRun(undefined);
				}
				const id =
					"messageId" in event
						? event.messageId
						: "parentMessageId" in event
							? event.parentMessageId
							: undefined;
				if (typeof id !== "string") return;
				const owner = run;
				setMessageRuns((previous) =>
					previous.get(id) === owner
						? previous
						: new Map(previous).set(id, owner),
				);
			},
			onRunFinalized: () => {
				setActiveReasoningRun(undefined);
				setAwaitingText(false);
				run = undefined;
			},
		});
		return () => subscription.unsubscribe();
	}, [agent]);
	return { messageRuns, activeReasoningRun, awaitingText };
}
