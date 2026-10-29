import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { listRuns } from "../api";
import { difficultyLabel, RunStatusBadge } from "../components";
import {
	formatClock,
	formatDateTime,
	formatPercent,
	summaryRates,
} from "../format";
import { arenaHref } from "../router";
import type { Progress, Rate, RunSummary } from "../types";
import { isActiveRun } from "./useRunPolling";

/** Poll interval of the run list while any listed run is generating or running. */
export const RUN_LIST_POLL_INTERVAL_MS = 2000;

/** Active runs first, then finished runs; both newest first. */
export function sortRuns(runs: RunSummary[]): RunSummary[] {
	const newest = (a: RunSummary, b: RunSummary) =>
		Date.parse(b.started_at) - Date.parse(a.started_at);
	return [
		...runs.filter(isActiveRun).sort(newest),
		...runs.filter((run) => !isActiveRun(run)).sort(newest),
	];
}

export interface RunListState {
	runs: RunSummary[] | undefined;
	error: Error | undefined;
	reload: () => void;
}

/**
 * Loads `GET /runs[?level=]` and polls it about every 2 s while any run is active; polling stops
 * when nothing runs any more and on unmount.
 */
export function useRunList(level?: string): RunListState {
	const [runs, setRuns] = useState<RunSummary[]>();
	const [error, setError] = useState<Error>();
	const [attempt, setAttempt] = useState(0);
	const loaded = useRef<RunSummary[] | undefined>(undefined);

	useEffect(() => {
		void attempt;
		const controller = new AbortController();
		let timer: ReturnType<typeof setTimeout> | undefined;
		const schedule = () => {
			timer = setTimeout(tick, RUN_LIST_POLL_INTERVAL_MS);
		};
		async function tick() {
			try {
				const next = await listRuns(level, { signal: controller.signal });
				if (controller.signal.aborted) return;
				loaded.current = next;
				setRuns(next);
				setError(undefined);
				if (next.some(isActiveRun)) schedule();
			} catch (reason) {
				if (controller.signal.aborted) return;
				setError(reason instanceof Error ? reason : new Error(String(reason)));
				// A transient failure while runs are active: keep trying.
				if (loaded.current?.some(isActiveRun)) schedule();
			}
		}
		void tick();
		return () => {
			controller.abort();
			clearTimeout(timer);
		};
	}, [level, attempt]);

	const reload = useCallback(() => setAttempt((n) => n + 1), []);
	return { runs, error, reload };
}

function TeamProgress({
	team,
	progress,
}: {
	team: "Red" | "Green";
	progress: Progress;
}) {
	const pct = progress.total > 0 ? (progress.done / progress.total) * 100 : 0;
	return (
		<span className="flex min-w-0 items-center gap-1.5">
			<span
				className={cn(
					"text-xs font-medium",
					team === "Red" ? "text-red-600" : "text-emerald-700",
				)}
			>
				{team}
			</span>
			<span
				aria-hidden="true"
				className="h-1.5 w-12 overflow-hidden rounded-full bg-slate-200"
			>
				<span
					className={cn(
						"block h-full rounded-full transition-[width]",
						team === "Red" ? "bg-red-500" : "bg-emerald-500",
					)}
					style={{ width: `${pct}%` }}
				/>
			</span>
			<span
				data-testid={`progress-${team.toLowerCase()}`}
				className="font-mono text-xs text-slate-700 tabular-nums"
			>
				{progress.done}/{progress.total}
			</span>
		</span>
	);
}

function RateText({ label, rate }: { label: string; rate: Rate }) {
	return (
		<span className="flex items-baseline gap-1 whitespace-nowrap">
			<span className="text-xs text-slate-500">{label}</span>
			<span className="text-[13px] font-semibold tabular-nums">
				{formatPercent(rate)}
			</span>
			<span className="font-mono text-[11px] text-slate-500">
				{rate.numerator}/{rate.denominator}
			</span>
		</span>
	);
}

function RunOutcome({ run }: { run: RunSummary }) {
	if (isActiveRun(run))
		return (
			<span className="flex flex-wrap items-center gap-x-3 gap-y-1">
				<TeamProgress team="Red" progress={run.progress.red} />
				<TeamProgress team="Green" progress={run.progress.green} />
			</span>
		);
	const rates = summaryRates(run);
	return (
		<span className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
			<span title="Attack success rate (errors excluded)">
				<RateText label="ASR" rate={rates.attack_success} />
			</span>
			<span title="False positive rate (errors excluded)">
				<RateText label="FPR" rate={rates.false_positive} />
			</span>
		</span>
	);
}

/** One run: level, difficulty, status, start, duration and progress or rates; links to the run. */
export function RunRow({
	run,
	levelTitle,
	showTitle = true,
	compact = false,
}: {
	run: RunSummary;
	/** Level title from the level list, for runs without a recorded title. */
	levelTitle?: string;
	showTitle?: boolean;
	compact?: boolean;
}) {
	const active = isActiveRun(run);
	const title = run.title ?? levelTitle ?? run.level;
	return (
		<li data-run={run.id} data-status={run.status}>
			<a
				href={arenaHref({ name: "run", runId: run.id })}
				className={cn(
					"grid gap-x-4 gap-y-1 px-4 py-2.5 text-[13px] transition-colors focus-visible:outline-2 focus-visible:outline-blue-500",
					compact
						? "grid-cols-[minmax(0,1fr)_auto]"
						: "grid-cols-[minmax(0,1fr)_auto] md:grid-cols-[minmax(0,1.4fr)_6.5rem_7.5rem_4.5rem_minmax(0,1.5fr)] md:items-center",
					active
						? "bg-blue-50/60 shadow-[inset_3px_0_0_#3b82f6] hover:bg-blue-50"
						: "hover:bg-slate-50",
				)}
			>
				<span className="flex min-w-0 items-baseline gap-2">
					{showTitle && (
						<span className="truncate font-medium text-slate-900">{title}</span>
					)}
					<span
						className={cn(
							"shrink-0",
							showTitle ? "text-slate-500" : "font-medium text-slate-900",
						)}
					>
						{difficultyLabel(run.difficulty)}
					</span>
				</span>
				<span
					className={cn(
						"justify-self-end",
						!compact && "md:justify-self-start",
					)}
				>
					<RunStatusBadge status={run.status} />
				</span>
				<span
					className={cn(
						"flex items-baseline gap-3 text-slate-600",
						!compact && "md:contents",
					)}
				>
					<time
						dateTime={run.started_at}
						title={`Started ${formatDateTime(run.started_at, { seconds: true })}`}
						className="whitespace-nowrap tabular-nums"
					>
						{formatDateTime(run.started_at)}
					</time>
					<span
						title="Duration"
						className="font-mono text-xs whitespace-nowrap tabular-nums"
					>
						{formatClock(run.elapsed)}
					</span>
				</span>
				<span className={cn("col-span-2", !compact && "md:col-span-1")}>
					<RunOutcome run={run} />
				</span>
			</a>
		</li>
	);
}

/**
 * Runs of all levels (or of one `level`): active runs first with live progress, then finished
 * runs with their rates. `limit` keeps only the latest few.
 */
export function RunList({
	level,
	limit,
	compact = false,
	levelTitles,
	label = "Runs",
}: {
	level?: string;
	limit?: number;
	compact?: boolean;
	levelTitles?: Record<string, string>;
	label?: string;
}) {
	const { runs, error, reload } = useRunList(level);
	const sorted = runs ? sortRuns(runs) : [];
	const shown = limit === undefined ? sorted : sorted.slice(0, limit);

	if (!runs)
		return error ? (
			<p className="px-4 py-3 text-sm text-slate-500">
				Runs could not be loaded ({error.message}).{" "}
				<button
					type="button"
					onClick={reload}
					className="font-medium text-blue-600 hover:underline"
				>
					Reload runs
				</button>
			</p>
		) : (
			<p className="px-4 py-3 text-sm text-slate-500">Loading runs…</p>
		);
	if (!shown.length)
		return (
			<p className="px-4 py-3 text-sm text-slate-500">
				{level
					? "No runs of this level yet."
					: "No runs yet. Open a level and start an evaluation."}
			</p>
		);
	return (
		<>
			{!compact && (
				<div
					aria-hidden="true"
					className="hidden grid-cols-[minmax(0,1.4fr)_6.5rem_7.5rem_4.5rem_minmax(0,1.5fr)] gap-x-4 border-b px-4 py-2 text-xs text-slate-500 md:grid"
				>
					<span>Level</span>
					<span>Status</span>
					<span>Started</span>
					<span>Duration</span>
					<span>Progress / rates</span>
				</div>
			)}
			<ul aria-label={label} className="divide-y divide-slate-100">
				{shown.map((run) => (
					<RunRow
						key={run.id}
						run={run}
						levelTitle={levelTitles?.[run.level]}
						showTitle={!level}
						compact={compact}
					/>
				))}
			</ul>
		</>
	);
}
