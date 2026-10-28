import { AlertTriangleIcon, CheckIcon, Loader2Icon } from "lucide-react";
import { useMemo } from "react";
import { cn } from "@/lib/utils";
import { getLevel } from "../api";
import { OutcomeBadge } from "../components";
import { formatParams } from "../format";
import { ArenaGraph } from "../graph/ArenaGraph";
import { EventLog } from "../replay/EventLog";
import { highlightAt } from "../replay/events";
import { arenaHref } from "../router";
import type {
	CaseSummary,
	GenerationStatus,
	Progress,
	RunDetail,
	Team,
} from "../types";
import { useArenaQuery } from "../useArenaQuery";

const LIVE_EVENTS_SHOWN = 8;

function ProgressCard({
	title,
	unit,
	progress,
	tone,
	stats,
}: {
	title: string;
	unit: string;
	progress: Progress;
	tone: "red" | "green";
	stats: { label: string; value: number; dot: string }[];
}) {
	const pct = progress.total > 0 ? (progress.done / progress.total) * 100 : 0;
	return (
		<section
			aria-label={`${title} progress`}
			className="flex flex-col gap-3 rounded-xl border bg-white p-4 shadow-sm"
		>
			<div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
				<h2
					className={cn(
						"text-xs font-semibold tracking-[0.08em] uppercase",
						tone === "red" ? "text-red-600" : "text-emerald-700",
					)}
				>
					{title}
				</h2>
				<span className="text-2xl font-semibold tracking-tight tabular-nums">
					{progress.done}
				</span>
				<span className="text-sm text-slate-500">
					/ {progress.total} {unit}
				</span>
			</div>
			<div
				role="progressbar"
				aria-label={`${title} cases done`}
				aria-valuemin={0}
				aria-valuemax={progress.total}
				aria-valuenow={progress.done}
				className="h-2 overflow-hidden rounded-full bg-slate-100"
			>
				<div
					className={cn(
						"h-full rounded-full transition-[width]",
						tone === "red" ? "bg-red-500" : "bg-emerald-500",
					)}
					style={{ width: `${pct}%` }}
				/>
			</div>
			<dl className="flex flex-wrap gap-x-5 gap-y-1 text-[13px] text-slate-600">
				{stats.map((stat) => (
					<div key={stat.label} className="flex items-center gap-1.5">
						<span
							aria-hidden="true"
							className={cn("size-2 rounded-sm", stat.dot)}
						/>
						<dt>{stat.label}</dt>
						<dd className="font-semibold text-slate-900 tabular-nums">
							{stat.value}
						</dd>
					</div>
				))}
			</dl>
		</section>
	);
}

function caseStatus(item: CaseSummary, liveId: string | undefined) {
	if (item.outcome) return <OutcomeBadge outcome={item.outcome} />;
	if (item.status === "stopped")
		return (
			<span className="inline-flex h-5 items-center rounded-md border border-amber-200 bg-amber-50 px-1.5 text-[11px] text-amber-800">
				Stopped
			</span>
		);
	if (item.id === liveId || item.status === "running")
		return (
			<span className="inline-flex h-5 items-center gap-1.5 rounded-md border border-blue-200 bg-blue-50 px-1.5 text-[11px] font-semibold text-blue-700">
				<span className="size-1.5 animate-pulse rounded-full bg-blue-500" />
				Running{item.turns > 0 ? ` · turn ${item.turns}` : ""}
			</span>
		);
	return (
		<span className="inline-flex h-5 items-center rounded-md border px-1.5 text-[11px] text-slate-500">
			Queued
		</span>
	);
}

/** Generated cases with their input, visible as soon as they exist (R-RED-4). */
function CaseQueue({ run }: { run: RunDetail }) {
	const liveId = run.live?.case_id;
	return (
		<section
			aria-label="Generated cases"
			className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-white shadow-sm"
		>
			<div className="flex h-12 shrink-0 items-center justify-between gap-3 border-b px-4">
				<h2 className="text-sm font-semibold">Cases</h2>
				<span className="truncate text-xs text-slate-500">
					{run.cases.length} generated · inputs for a sanity check
				</span>
			</div>
			{run.cases.length === 0 ? (
				<p className="p-4 text-sm text-slate-500">
					{run.status === "generating"
						? "Generating cases… they appear here as soon as they exist."
						: "No cases yet."}
				</p>
			) : (
				<ul aria-label="Cases" className="min-h-0 flex-1 overflow-y-auto">
					{run.cases.map((item) => {
						const decided = item.outcome !== null;
						const body = (
							<>
								<span className="flex items-center gap-2 text-xs">
									<span className="font-mono text-slate-500">
										{item.team === "green" ? item.label : `#${item.label}`}
									</span>
									<span
										className={cn(
											"truncate",
											item.team === "red"
												? "text-slate-700"
												: "text-emerald-700",
										)}
									>
										{item.attack_type ?? "benign"}
									</span>
									<span className="ml-auto shrink-0">
										{caseStatus(item, liveId)}
									</span>
								</span>
								<span
									className={cn(
										"truncate text-[13px]",
										item.input_preview
											? "text-slate-600"
											: "text-slate-400 italic",
									)}
								>
									{item.input_preview ??
										(item.team === "red"
											? "Generated when the case starts"
											: "Not generated yet")}
								</span>
							</>
						);
						return (
							<li
								key={item.id}
								data-case={item.label}
								className={cn(
									// New cases slide in as they are generated.
									"border-b border-slate-100 animate-in fade-in-0 slide-in-from-top-1 duration-300",
									item.id === liveId && "bg-blue-50/50",
								)}
							>
								{decided ? (
									<a
										href={arenaHref({
											name: "case",
											runId: run.id,
											caseId: item.id,
										})}
										className="flex flex-col gap-1 px-4 py-2.5 hover:bg-slate-50"
									>
										{body}
									</a>
								) : (
									<div className="flex flex-col gap-1 px-4 py-2.5">{body}</div>
								)}
							</li>
						);
					})}
				</ul>
			)}
		</section>
	);
}

const GENERATION_TEXT: Record<Team, Record<GenerationStatus, string>> = {
	red: {
		pending: "Waiting to generate attack cases…",
		generating: "Generating attack cases…",
		done: "Attack cases generated",
		failed: "Attack case generation failed",
		dataset: "Attack cases loaded from a dataset",
	},
	green: {
		pending: "Waiting to generate benign cases…",
		generating: "Generating benign cases…",
		done: "Benign cases generated",
		failed: "Benign case generation failed",
		dataset: "Benign cases loaded from a dataset",
	},
};

function TeamGeneration({ run, team }: { run: RunDetail; team: Team }) {
	const status = run.generation?.[team]?.status ?? "generating";
	const text = GENERATION_TEXT[team];
	const generated = run.cases.filter((c) => c.team === team).length;
	const total = Math.max(run.progress[team].total, generated);
	const pct = total > 0 ? (generated / total) * 100 : 0;
	const busy = status === "generating" || status === "pending";
	const title = text[status];
	return (
		<div
			data-team={team}
			data-generation={status}
			className="flex flex-col gap-2 rounded-lg border bg-slate-50/60 p-4"
		>
			<div className="flex items-center gap-2.5">
				{busy ? (
					<Loader2Icon
						aria-hidden="true"
						className={cn(
							"size-4 shrink-0 animate-spin",
							team === "red" ? "text-red-500" : "text-emerald-600",
						)}
					/>
				) : status === "failed" ? (
					<AlertTriangleIcon
						aria-hidden="true"
						className="size-4 shrink-0 text-red-600"
					/>
				) : (
					<CheckIcon
						aria-hidden="true"
						className="size-4 shrink-0 text-emerald-600"
					/>
				)}
				<h3 className="text-sm font-semibold">{title}</h3>
			</div>
			<div
				role="progressbar"
				aria-label={`${team === "red" ? "Attack" : "Benign"} cases generated`}
				aria-valuemin={0}
				aria-valuemax={total}
				aria-valuenow={generated}
				className="relative h-2 overflow-hidden rounded-full bg-slate-200"
			>
				<div
					className={cn(
						"h-full rounded-full transition-[width] duration-500",
						team === "red" ? "bg-red-500" : "bg-emerald-500",
					)}
					style={{ width: `${pct}%` }}
				/>
				{busy && (
					<div
						aria-hidden="true"
						className="absolute inset-0 animate-pulse bg-white/30"
					/>
				)}
			</div>
			<p className="text-[13px] text-slate-600 tabular-nums">
				{generated} of {total} cases generated
			</p>
		</div>
	);
}

/** While the run is generating: progress of case generation per team, instead of an empty sample. */
function GenerationPanel({ run }: { run: RunDetail }) {
	return (
		<section
			aria-label="Case generation"
			className="flex min-h-[28rem] flex-col overflow-hidden rounded-xl border bg-white shadow-sm"
		>
			<div className="flex h-12 shrink-0 items-center gap-3 border-b px-4">
				<h2 className="text-sm font-semibold">Generating cases</h2>
				<span className="truncate text-xs text-slate-500">
					the live sample starts with the first case
				</span>
			</div>
			<div className="flex flex-1 flex-col justify-center gap-4 p-6">
				<TeamGeneration run={run} team="red" />
				<TeamGeneration run={run} team="green" />
			</div>
		</section>
	);
}

/** One running case: graph with the current step and the latest events (R-RUN-5). */
function LiveSample({ run }: { run: RunDetail }) {
	const live = run.live;
	const needLevel = Boolean(live) && !run.graph;
	const level = useArenaQuery(
		(signal) =>
			needLevel
				? getLevel(run.level, run.difficulty, { signal })
				: Promise.resolve(undefined),
		[needLevel, run.level, run.difficulty],
	);
	const graph = run.graph ?? level.data?.graph;
	const item = live ? run.cases.find((c) => c.id === live.case_id) : undefined;
	const events = live?.events ?? [];
	const highlight = useMemo(
		() => (events.length ? highlightAt(events, events.length - 1) : undefined),
		[events],
	);
	const shown = events.slice(-LIVE_EVENTS_SHOWN);
	const lastTurn = events[events.length - 1]?.turn;

	return (
		<section
			aria-label="Live sample"
			className="flex min-h-[28rem] flex-col overflow-hidden rounded-xl border bg-white shadow-sm"
		>
			<div className="flex h-12 shrink-0 items-center gap-3 border-b px-4">
				<h2 className="text-sm font-semibold">Live sample</h2>
				{item && (
					<span className="truncate text-xs text-slate-500">
						Case {item.label}
						{item.attack_type ? ` · ${item.attack_type}` : ""}
						{lastTurn
							? ` · turn ${lastTurn}${run.graph?.surface.turns === "multi" ? ` of ≤ ${run.graph.surface.max_turns}` : ""}`
							: ""}
					</span>
				)}
			</div>
			{!live ? (
				<p className="flex flex-1 items-center justify-center p-6 text-sm text-slate-500">
					No case is running right now.
				</p>
			) : (
				<div className="flex min-h-0 flex-1 flex-col xl:flex-row">
					<div className="relative h-80 min-h-0 xl:h-auto xl:flex-1">
						{graph ? (
							<ArenaGraph graph={graph} highlight={highlight} />
						) : (
							<p className="p-4 text-sm text-slate-500">Loading graph…</p>
						)}
					</div>
					<div className="min-h-0 overflow-y-auto border-t xl:w-[17rem] xl:shrink-0 xl:border-t-0 xl:border-l">
						{events.length > shown.length && (
							<p className="border-b border-slate-100 px-4 py-1.5 text-center text-xs text-slate-500">
								{events.length - shown.length} earlier events
							</p>
						)}
						<EventLog
							events={shown}
							team={item?.team ?? "red"}
							graph={graph}
							current={shown.length - 1}
							label="Latest events"
							compact
						/>
					</div>
				</div>
			)}
		</section>
	);
}

/** The model and parameters of every LLM role, as recorded with the run (R-EXE-7, AC-9). */
export function ModelsUsed({ run }: { run: RunDetail }) {
	if (!run.models.length) return null;
	return (
		<details className="group rounded-xl border bg-white shadow-sm">
			<summary className="flex cursor-pointer items-center justify-between gap-3 px-4 py-3 text-sm font-semibold">
				Models used
				<span className="text-xs font-normal text-slate-500">
					{run.models.length} roles · recorded with the run
				</span>
			</summary>
			<table className="w-full border-t text-[13px]">
				<thead className="sr-only">
					<tr>
						<th>Role</th>
						<th>Model</th>
						<th>Parameters</th>
					</tr>
				</thead>
				<tbody>
					{run.models.map((m) => (
						<tr
							key={m.role}
							className="border-b border-slate-100 last:border-0"
						>
							<td className="px-4 py-2 font-mono text-xs text-slate-600">
								{m.role}
							</td>
							<td className="px-2 py-2 font-mono text-xs">{m.model}</td>
							<td className="px-4 py-2 font-mono text-xs text-slate-500">
								{formatParams(m.params)}
							</td>
						</tr>
					))}
				</tbody>
			</table>
		</details>
	);
}

/** Run in progress: progress per team, live counts, live sample, generated cases, models. */
export function RunProgress({ run }: { run: RunDetail }) {
	const { counts, progress } = run;
	return (
		<div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4 lg:p-6">
			<div className="grid shrink-0 gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
				<ProgressCard
					title="Red team"
					unit="attacks"
					tone="red"
					progress={progress.red}
					stats={[
						{
							label: "Attack succeeded",
							value: counts.succeeded,
							dot: "bg-red-500",
						},
						{
							label: "Defended",
							value: counts.defended,
							dot: "bg-emerald-500",
						},
					]}
				/>
				<ProgressCard
					title="Green team"
					unit="benign cases"
					tone="green"
					progress={progress.green}
					stats={[
						{ label: "Passed", value: counts.passed, dot: "bg-emerald-500" },
						{
							label: "False positive",
							value: counts.false_positive,
							dot: "bg-amber-500",
						},
					]}
				/>
				<section
					aria-label="Errors"
					className="flex flex-col justify-between gap-1 rounded-xl border bg-white p-4 shadow-sm md:min-w-40"
				>
					<h2 className="text-xs font-semibold tracking-[0.08em] text-slate-500 uppercase">
						Errors
					</h2>
					<span
						className="text-2xl font-semibold tabular-nums"
						data-testid="count-error"
					>
						{counts.error}
					</span>
					<span className="text-xs text-slate-500">excluded from rates</span>
				</section>
			</div>
			<div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
				{run.status === "generating" && !run.live ? (
					<GenerationPanel run={run} />
				) : (
					<LiveSample run={run} />
				)}
				<div className="flex min-h-0 flex-col gap-4">
					<CaseQueue run={run} />
					<ModelsUsed run={run} />
				</div>
			</div>
		</div>
	);
}
