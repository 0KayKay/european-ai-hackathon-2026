import { type DependencyList, useCallback, useEffect, useState } from "react";

export interface QueryState<T> {
	data: T | undefined;
	error: Error | undefined;
	loading: boolean;
	reload: () => void;
}

/**
 * Runs `load` whenever `deps` change and aborts the previous request. Keeps the last data while a
 * new request is in flight, so switching e.g. difficulty does not flash an empty page.
 */
export function useArenaQuery<T>(
	load: (signal: AbortSignal) => Promise<T>,
	deps: DependencyList,
): QueryState<T> {
	const [data, setData] = useState<T>();
	const [error, setError] = useState<Error>();
	const [loading, setLoading] = useState(true);
	const [attempt, setAttempt] = useState(0);
	// biome-ignore lint/correctness/useExhaustiveDependencies: `deps` are the caller's dependencies of `load`.
	const run = useCallback(load, deps);

	useEffect(() => {
		void attempt;
		const controller = new AbortController();
		setLoading(true);
		setError(undefined);
		run(controller.signal).then(
			(result) => {
				if (controller.signal.aborted) return;
				setData(result);
				setLoading(false);
			},
			(reason: unknown) => {
				if (controller.signal.aborted) return;
				setError(reason instanceof Error ? reason : new Error(String(reason)));
				setLoading(false);
			},
		);
		return () => controller.abort();
	}, [run, attempt]);

	const reload = useCallback(() => setAttempt((n) => n + 1), []);
	return { data, error, loading, reload };
}
