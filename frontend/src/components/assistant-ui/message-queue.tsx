import {
	type AssistantRuntime,
	type AttachmentAdapter,
	type CompleteAttachment,
	createMessageQueue,
	type PendingAttachment,
} from "@assistant-ui/react";
import { XIcon } from "lucide-react";
import {
	useCallback,
	useEffect,
	useMemo,
	useRef,
	useSyncExternalStore,
} from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { TooltipIconButton } from "./tooltip-icon-button";

export function useMessageQueue(
	runtime: AssistantRuntime,
	attachments: AttachmentAdapter,
) {
	const queue = useMemo(
		() =>
			createMessageQueue({
				run: (message) =>
					runtime.thread.append({
						...message,
						parentId: runtime.thread.getState().messages.at(-1)?.id ?? null,
						sourceId: null,
					}),
			}),
		[runtime],
	);
	const isRunning = useSyncExternalStore(
		runtime.thread.subscribe,
		() => runtime.thread.getState().isRunning,
	);
	const canSend = useSyncExternalStore(
		runtime.thread.composer.subscribe,
		() => {
			const composer = runtime.thread.composer.getState();
			return (
				!composer.isEmpty &&
				composer.attachments.every(
					(attachment) =>
						attachment.status.type === "complete" ||
						attachment.status.type === "requires-action",
				)
			);
		},
	);
	const items = useSyncExternalStore(
		queue.subscribe,
		() => queue.adapter.items,
	);
	const sending = useRef(false);
	useEffect(() => {
		if (isRunning) queue.notifyBusy();
		else queue.notifyIdle();
	}, [queue, isRunning]);

	const send = useCallback(async () => {
		const draft = runtime.thread.composer.getState();
		if (
			sending.current ||
			draft.isEmpty ||
			draft.attachments.some(
				(attachment) =>
					attachment.status.type !== "complete" &&
					attachment.status.type !== "requires-action",
			)
		)
			return;
		sending.current = true;
		try {
			const completed = await Promise.all(
				draft.attachments.map((attachment) =>
					attachment.status.type === "complete"
						? (attachment as CompleteAttachment)
						: attachments.send(attachment as PendingAttachment),
				),
			);
			await runtime.thread.composer.reset();
			if (runtime.thread.getState().isRunning) queue.notifyBusy();
			queue.adapter.enqueue({
				createdAt: new Date(),
				metadata: { custom: {} },
				role: "user",
				parentId: null,
				sourceId: null,
				content: draft.text ? [{ type: "text", text: draft.text }] : [],
				attachments: completed,
				runConfig: draft.runConfig,
			});
		} catch (error) {
			toast.error(error instanceof Error ? error.message : String(error));
		} finally {
			sending.current = false;
		}
	}, [attachments, queue, runtime]);
	return {
		items,
		send,
		canSend,
		clear: queue.clear,
		cancel: queue.notifyCancelled,
		remove: queue.adapter.remove,
	};
}

export function MessageQueue({
	items,
	remove,
}: Pick<ReturnType<typeof useMessageQueue>, "items" | "remove">) {
	const { t } = useTranslation();
	const count = items.length;
	if (count === 0) return null;

	return (
		<div data-slot="message-queue" className="mb-2 space-y-2 px-2">
			<p role="status" className="text-xs text-muted-foreground">
				{t("assistantUi.queuedMessages", { count })}
			</p>
			<ul className="max-h-40 space-y-1 overflow-y-auto">
				{items.map((queueItem) => (
					<li
						key={queueItem.id}
						className="flex items-center gap-2 rounded-xl border bg-muted/40 px-3 py-1"
					>
						<span className="min-w-0 flex-1 truncate text-sm">
							{queueItem.prompt}
						</span>

						<TooltipIconButton
							onClick={() => remove(queueItem.id)}
							tooltip={t("assistantUi.removeFromQueue")}
							aria-label={t("assistantUi.removeFromQueue")}
						>
							<XIcon className="size-4" />
						</TooltipIconButton>
					</li>
				))}
			</ul>
		</div>
	);
}
