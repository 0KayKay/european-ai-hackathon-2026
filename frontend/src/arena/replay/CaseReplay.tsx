import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { useEffect, useMemo } from "react";
import { cn } from "@/lib/utils";
import { getRun, getRunCase } from "../api";
import {
	ArenaShell,
	ErrorState,
	LoadingState,
	OutcomeBadge,
	runCrumbs,
} from "../components";
import { formatDuration } from "../format";
import { ArenaGraph } from "../graph/ArenaGraph";
import { arenaHref } from "../router";
import type { CaseDetail, CaseSummary } from "../types";
import { useArenaQuery } from "../useArenaQuery";
import { EventLog } from "./EventLog";
import { highlightAt } from "./events";
import { PlaybackBar, usePlayback } from "./Playback";

function caseFacts(item: CaseSummary) {
	return [
		item.team === "red" ? "Red team" : "Green team",
		item.attack_type,
		`${item.turns} turn${item.turns === 1 ? "" : "s"}`,
		formatDuration(item.duration),
	]
		.filter(Boolean)
		.join(" · ");
}

function CaseNav({
	runId,
	cases,
	caseId,
}: {
	runId: string;
	cases: CaseSummary[] | undefined;
	caseId: string;
}) {
	const index = cases?.findIndex((c) => c.id === caseId) ?? -1;
	const prev = index > 0 ? cases?.[index - 1] : undefined;
	const next = index >= 0 ? cases?.[index + 1] : undefined;
	const link = (item: CaseSummary | undefined, dir: "prev" | "next") => {
		const label = dir === "prev" ? "Previous case" : "Next case";
		const className =
			"inline-flex h-8 items-center gap-1 rounded-md border bg-white px-2.5 text-[13px] font-medium text-slate-700 shadow-xs";
		const content = (
			<>
				{dir === "prev" && (
					<ChevronLeftIcon className="size-3.5" aria-hidden="true" />
				)}
				<span>{item ? `Case ${item.label}` : label}</span>
				{dir === "next" && (
					<ChevronRightIcon className="size-3.5" aria-hidden="true" />
				)}
			</>
		);
		return item ? (
			<a
				href={arenaHref({ name: "case", runId, caseId: item.id })}
				aria-label={`${label}: ${item.label}`}
				className={cn(className, "hover:bg-slate-50")}
			>
				{content}
			</a>
		) : (
			<span aria-disabled="true" className={cn(className, "opacity-40")}>
				{content}
			</span>
		);
	};
	return (
		<nav aria-label="Cases" className="flex items-center gap-1.5">
			{link(prev, "prev")}
			{link(next, "next")}
		</nav>
	);
}

function ReplayBody({ detail }: { detail: CaseDetail }) {
	const events = detail.events;
	const playback = usePlayback(events);
	const { index, step, toggle } = playback;
	const highlight = useMemo(
		() => (events.length ? highlightAt(events, index) : undefined),
		[events, index],
	);

	// ←/→ step, space plays or pauses (unless a form control or button has the focus).
	useEffect(() => {
		const onKey = (event: KeyboardEvent) => {
			if (event.altKey || event.ctrlKey || event.metaKey) return;
			const target = event.target as HTMLElement | null;
			const tag = target?.tagName;
			if (
				tag === "INPUT" ||
				tag === "TEXTAREA" ||
				tag === "SELECT" ||
				target?.isContentEditable
			)
				return;
			if (event.key === "ArrowLeft") {
				event.preventDefault();
				step(-1);
			} else if (event.key === "ArrowRight") {
				event.preventDefault();
				step(1);
			} else if (event.key === " " && tag !== "BUTTON" && tag !== "A") {
				event.preventDefault();
				toggle();
			}
		};
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [step, toggle]);

	return (
		<div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,30rem)] lg:overflow-hidden">
			<section aria-label="Replay graph" className="flex min-h-0 flex-col">
				<div className="relative h-[30rem] min-h-0 lg:h-auto lg:flex-1">
					<ArenaGraph graph={detail.graph} highlight={highlight} />
				</div>
				<PlaybackBar events={events} playback={playback} />
			</section>
			<aside
				aria-label="Events"
				className="flex min-h-0 flex-col border-t bg-white lg:border-t-0 lg:border-l"
			>
				<div className="flex h-12 shrink-0 items-center justify-between gap-3 border-b px-4">
					<h2 className="text-sm font-semibold">Event log</h2>
					<span className="text-xs text-slate-500">
						{events.length} event{events.length === 1 ? "" : "s"} · recorded
						during the run · ←/→ step, space plays
					</span>
				</div>
				<div className="min-h-0 flex-1 overflow-y-auto">
					{events.length ? (
						<EventLog
							events={events}
							team={detail.team}
							graph={detail.graph}
							current={index}
							onSelect={playback.seek}
						/>
					) : (
						<p className="p-4 text-sm text-slate-500">
							No events were recorded for this case.
						</p>
					)}
				</div>
			</aside>
		</div>
	);
}

/** Replay of one case: graph with the visited path, playback bar and event log (R-RPL-*). */
export function CaseReplay({
	runId,
	caseId,
}: {
	runId: string;
	caseId: string;
}) {
	const detail = useArenaQuery(
		(signal) => getRunCase(runId, caseId, { signal }),
		[runId, caseId],
	);
	const run = useArenaQuery((signal) => getRun(runId, { signal }), [runId]);
	const data = detail.data?.id === caseId ? detail.data : undefined;

	return (
		<ArenaShell
			crumbs={[
				...runCrumbs(run.data, runId),
				{ label: `Case ${data?.label ?? caseId}` },
			]}
			center={
				data && (
					<div className="flex min-w-0 items-center gap-2.5 text-[13px] text-slate-600">
						{data.outcome && <OutcomeBadge outcome={data.outcome} />}
						<span className="truncate">{caseFacts(data)}</span>
					</div>
				)
			}
			actions={
				<CaseNav runId={runId} cases={run.data?.cases} caseId={caseId} />
			}
		>
			{!data && detail.loading && <LoadingState label="Loading case…" />}
			{detail.error && !detail.loading && (
				<ErrorState
					title="This case could not be loaded."
					error={detail.error}
					onRetry={detail.reload}
				/>
			)}
			{data && !detail.error && <ReplayBody key={data.id} detail={data} />}
		</ArenaShell>
	);
}
