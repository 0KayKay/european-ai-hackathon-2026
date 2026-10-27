import { DownloadIcon, PlayIcon, SquareIcon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import { runExportUrl, stopRun } from "../api";
import { ArenaShell, ErrorState, LoadingState, runCrumbs } from "../components";
import { formatClock, STATUS_LABELS } from "../format";
import { arenaHref } from "../router";
import type { RunDetail } from "../types";
import { RunProgress } from "./RunProgress";
import { RunResultsView } from "./RunResults";
import { isActiveRun, useRunPolling } from "./useRunPolling";

const STATUS_TONES: Record<RunDetail["status"], string> = {
	generating: "border-blue-200 bg-blue-50 text-blue-700",
	running: "border-emerald-200 bg-emerald-50 text-emerald-700",
	completed: "border-slate-200 bg-slate-50 text-slate-700",
	stopped: "border-amber-200 bg-amber-50 text-amber-800",
	failed: "border-red-200 bg-red-50 text-red-700",
	interrupted: "border-amber-200 bg-amber-50 text-amber-800",
};

function StatusPill({ run }: { run: RunDetail }) {
	const active = isActiveRun(run);
	return (
		<span
			role="status"
			data-status={run.status}
			className={cn(
				"inline-flex h-7 items-center gap-2 rounded-full border px-3 text-[13px] font-medium",
				STATUS_TONES[run.status],
			)}
		>
			{active && (
				<span
					aria-hidden="true"
					className="size-1.5 animate-pulse rounded-full bg-current"
				/>
			)}
			{STATUS_LABELS[run.status]}
			{!active && ` · ${run.cases.length} cases`}
			<span className="font-mono tabular-nums">
				{" · "}
				{formatClock(run.elapsed)}
			</span>
		</span>
	);
}

function StopButton({
	run,
	onStopped,
}: {
	run: RunDetail;
	onStopped: ReturnType<typeof useRunPolling>["merge"];
}) {
	const [stopping, setStopping] = useState(false);
	const [error, setError] = useState<string>();
	return (
		<>
			{error && (
				<span role="alert" className="max-w-56 truncate text-xs text-red-600">
					{error}
				</span>
			)}
			<Button
				type="button"
				size="sm"
				variant="outline"
				className="border-red-200 text-red-700 hover:bg-red-50 hover:text-red-800"
				disabled={stopping}
				onClick={async () => {
					setStopping(true);
					setError(undefined);
					try {
						onStopped(await stopRun(run.id));
					} catch (reason) {
						setError(
							`Could not stop the run: ${reason instanceof Error ? reason.message : String(reason)}`,
						);
					} finally {
						setStopping(false);
					}
				}}
			>
				{stopping ? (
					<Spinner className="size-3" aria-hidden="true" />
				) : (
					<SquareIcon className="size-3 fill-current" aria-hidden="true" />
				)}
				{stopping ? "Stopping…" : "Stop run"}
			</Button>
		</>
	);
}

/**
 * `#/arena/runs/:runId`: progress while the run is generating or running (polled about every
 * second), the results once it has finished.
 */
export function RunPage({ runId }: { runId: string }) {
	const { run, error, reload, merge } = useRunPolling(runId);
	const active = run ? isActiveRun(run) : false;

	return (
		<ArenaShell
			crumbs={runCrumbs(run, runId, true)}
			center={run && <StatusPill run={run} />}
			actions={
				run &&
				(active ? (
					<StopButton run={run} onStopped={merge} />
				) : (
					<>
						<Button asChild size="sm" variant="outline">
							<a href={runExportUrl(run.id)} download={`${run.id}.json`}>
								<DownloadIcon className="size-3.5" aria-hidden="true" />
								Export JSON
							</a>
						</Button>
						<Button asChild size="sm">
							<a
								href={arenaHref({
									name: "level",
									level: run.level,
									difficulty: run.difficulty,
									start: true,
								})}
							>
								<PlayIcon className="size-3.5" aria-hidden="true" />
								Run again
							</a>
						</Button>
					</>
				))
			}
		>
			{!run && !error && <LoadingState label="Loading run…" />}
			{!run && error && (
				<ErrorState
					title="This run could not be loaded."
					error={error}
					onRetry={reload}
				/>
			)}
			{run && error && active && (
				<p
					role="alert"
					className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900"
				>
					Live updates are failing ({error.message}). Retrying…
				</p>
			)}
			{run &&
				(active ? <RunProgress run={run} /> : <RunResultsView run={run} />)}
		</ArenaShell>
	);
}
