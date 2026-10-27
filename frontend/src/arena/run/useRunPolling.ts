import { useCallback, useEffect, useRef, useState } from "react";
import { getRun } from "../api";
import { ACTIVE_RUN_STATUSES, type RunDetail, type RunSummary } from "../types";

/** Poll interval while a run is generating or running (IMPLEMENTATION §5: ≈ 1 s). */
export const RUN_POLL_INTERVAL_MS = 1000;

export const isActiveRun = (run: Pick<RunSummary, "status">) =>
	ACTIVE_RUN_STATUSES.includes(run.status);

export interface RunPolling {
	run: RunDetail | undefined;
	/** Last request error; while a run is loaded, polling keeps retrying. */
	error: Error | undefined;
	reload: () => void;
	/** Merges a run summary (e.g. the answer of POST /runs/{id}/stop) into the current run. */
	merge: (summary: RunSummary) => void;
}

/**
 * Loads `GET /runs/{id}` and polls it about every second while the run is active. Polling stops
 * once the run has finished and on unmount.
 */
export function useRunPolling(runId: string): RunPolling {
	const [run, setRun] = useState<RunDetail>();
	const [error, setError] = useState<Error>();
	const [attempt, setAttempt] = useState(0);
	const loaded = useRef<RunDetail | undefined>(undefined);

	useEffect(() => {
		void attempt;
		const controller = new AbortController();
		let timer: ReturnType<typeof setTimeout> | undefined;
		const schedule = () => {
			timer = setTimeout(tick, RUN_POLL_INTERVAL_MS);
		};
		async function tick() {
			try {
				const next = await getRun(runId, { signal: controller.signal });
				if (controller.signal.aborted) return;
				loaded.current = next;
				setRun(next);
				setError(undefined);
				if (isActiveRun(next)) schedule();
			} catch (reason) {
				if (controller.signal.aborted) return;
				setError(reason instanceof Error ? reason : new Error(String(reason)));
				// A transient failure while watching an active run: keep trying.
				const last = loaded.current;
				if (last?.id === runId && isActiveRun(last)) schedule();
			}
		}
		void tick();
		return () => {
			controller.abort();
			clearTimeout(timer);
		};
	}, [runId, attempt]);

	const reload = useCallback(() => setAttempt((n) => n + 1), []);
	const merge = useCallback(
		(summary: RunSummary) =>
			setRun((current) =>
				current && current.id === summary.id
					? { ...current, ...summary }
					: current,
			),
		[],
	);
	return {
		run: run?.id === runId ? run : undefined,
		error,
		reload,
		merge,
	};
}
