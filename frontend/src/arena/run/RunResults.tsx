import { type ReactNode, useState } from "react";
import { cn } from "@/lib/utils";
import { getRunResults } from "../api";
import {
	ErrorState,
	ExpandableText,
	LoadingState,
	OutcomeBadge,
} from "../components";
import {
	decidedLabel,
	formatDuration,
	formatPercent,
	STATUS_LABELS,
	TEAM_LABELS,
} from "../format";
import { arenaHref, navigate } from "../router";
import type {
	CaseOutcome,
	CaseSummary,
	OutcomeCounts,
	Rate,
	RunDetail,
	RunResults,
} from "../types";
import { useArenaQuery } from "../useArenaQuery";
import { ModelsUsed } from "./RunProgress";

function Panel({
	title,
	aside,
	children,
}: {
	title: string;
	aside?: string;
	children: ReactNode;
}) {
	return (
		<section
			aria-label={title}
			className="flex flex-col gap-3 rounded-xl border bg-white p-4 shadow-sm"
		>
			<div className="flex items-baseline justify-between gap-3">
				<h2 className="text-sm font-semibold">{title}</h2>
				{aside && <span className="text-xs text-slate-500">{aside}</span>}
			</div>
			{children}
		</section>
	);
}

function Cell({
	value,
	caption,
	good,
}: {
	value: number;
	caption: string;
	good: boolean;
}) {
	return (
		<div
			className={cn(
				"flex flex-col items-center justify-center rounded-lg border py-2.5",
				good
					? "border-emerald-200 bg-emerald-50 text-emerald-800"
					: "border-red-200 bg-red-50 text-red-700",
			)}
		>
			<span className="text-xl font-semibold tabular-nums">{value}</span>
			<span className="text-[11px]">{caption}</span>
		</div>
	);
}

function Matrix({ matrix }: { matrix: RunResults["matrix"] }) {
	return (
		<Panel title="Expected vs. outcome">
			<div className="grid grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)] items-center gap-2 text-[13px]">
				<span />
				<span className="text-center text-xs text-slate-500">
					Stopped / blocked
				</span>
				<span className="text-center text-xs text-slate-500">
					Got through / passed
				</span>
				<span className="pr-2 text-red-600">Red team</span>
				<Cell value={matrix.red.stopped} caption="correct" good />
				<Cell
					value={matrix.red.got_through}
					caption="false negative"
					good={false}
				/>
				<span className="pr-2 text-emerald-700">Green team</span>
				<Cell
					value={matrix.green.blocked}
					caption="false positive"
					good={false}
				/>
				<Cell value={matrix.green.passed} caption="correct" good />
			</div>
		</Panel>
	);
}

const OUTCOME_SLICES: {
	outcome: keyof OutcomeCounts;
	team: string;
	label: string;
	color: string;
}[] = [
	{
		outcome: "succeeded",
		team: "Red",
		label: "attack succeeded",
		color: "#dc2626",
	},
	{ outcome: "defended", team: "Red", label: "defended", color: "#059669" },
	{
		outcome: "false_positive",
		team: "Green",
		label: "false positive",
		color: "#d97706",
	},
	{ outcome: "passed", team: "Green", label: "passed", color: "#0284c7" },
	{ outcome: "error", team: "Both", label: "errors", color: "#475569" },
];

const RADIUS = 52;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
/** Surface gap between two slices, in px along the ring. */
const SLICE_GAP = 2;

function RateLabel({
	label,
	rate,
	color,
	testId,
}: {
	label: string;
	rate: Rate;
	color: string;
	testId: string;
}) {
	return (
		<div
			data-testid={testId}
			className="flex items-baseline gap-2 rounded-lg bg-slate-50 px-3 py-2"
		>
			<span
				aria-hidden="true"
				className="size-2 shrink-0 self-center rounded-sm"
				style={{ background: color }}
			/>
			<span className="text-[13px] text-slate-600">{label}</span>
			<span className="ml-auto text-lg font-semibold tracking-tight tabular-nums">
				{formatPercent(rate)}
			</span>
			<span className="font-mono text-xs text-slate-500">
				{rate.numerator} / {rate.denominator}
			</span>
		</div>
	);
}

/** Donut of the decided cases by outcome, with both rates as returned by the API (R-RES-1). */
function OutcomeChart({ results }: { results: RunResults }) {
	const { counts, rates } = results;
	const total = OUTCOME_SLICES.reduce((sum, s) => sum + counts[s.outcome], 0);
	const shown = OUTCOME_SLICES.filter((s) => counts[s.outcome] > 0);
	const gap = shown.length > 1 ? SLICE_GAP : 0;
	let offset = 0;
	const arcs = shown.map((slice) => {
		const length = (counts[slice.outcome] / total) * CIRCUMFERENCE;
		const arc = {
			...slice,
			dash: Math.max(length - gap, 0.5),
			offset,
		};
		offset += length;
		return arc;
	});
	const pct = (n: number) =>
		total ? `${Math.round((n / total) * 100)}%` : "—";

	return (
		<Panel
			title="Outcomes"
			aside={`${total} decided case${total === 1 ? "" : "s"}`}
		>
			<div className="flex flex-wrap items-center gap-5">
				<svg
					viewBox="0 0 140 140"
					role="img"
					aria-label={`Outcomes: ${OUTCOME_SLICES.map((s) => `${s.team} ${s.label} ${counts[s.outcome]}`).join(", ")}`}
					className="size-36 shrink-0 -rotate-90"
				>
					<circle
						cx="70"
						cy="70"
						r={RADIUS}
						fill="none"
						stroke="#f1f5f9"
						strokeWidth="20"
					/>
					{arcs.map((arc) => (
						<circle
							key={arc.outcome}
							data-outcome={arc.outcome}
							cx="70"
							cy="70"
							r={RADIUS}
							fill="none"
							stroke={arc.color}
							strokeWidth="20"
							strokeDasharray={`${arc.dash} ${CIRCUMFERENCE - arc.dash}`}
							strokeDashoffset={-arc.offset}
							className="transition-opacity hover:opacity-80"
						>
							<title>{`${arc.team} · ${arc.label}: ${counts[arc.outcome]} (${pct(counts[arc.outcome])})`}</title>
						</circle>
					))}
					<text
						x="70"
						y="70"
						transform="rotate(90 70 70)"
						textAnchor="middle"
						dominantBaseline="central"
						className="fill-slate-900 text-[22px] font-semibold"
					>
						{total}
					</text>
				</svg>
				<ul
					aria-label="Outcome counts"
					className="flex min-w-36 flex-1 flex-col gap-1.5 text-[13px]"
				>
					{OUTCOME_SLICES.map((slice) => (
						<li
							key={slice.outcome}
							data-outcome={slice.outcome}
							className="flex items-center gap-2"
						>
							<span
								aria-hidden="true"
								className="size-2.5 shrink-0 rounded-sm"
								style={{ background: slice.color }}
							/>
							<span className="min-w-0 flex-1 text-slate-700">
								<span className="text-slate-500">{slice.team}</span> ·{" "}
								{slice.label}
							</span>
							<span className="font-semibold tabular-nums">
								{counts[slice.outcome]}
							</span>
							<span className="w-9 text-right font-mono text-xs text-slate-500">
								{pct(counts[slice.outcome])}
							</span>
						</li>
					))}
				</ul>
			</div>
			<div className="flex flex-col gap-2">
				<RateLabel
					label="Attack success rate"
					rate={rates.attack_success}
					color="#dc2626"
					testId="rate-attack-success"
				/>
				<RateLabel
					label="False positive rate"
					rate={rates.false_positive}
					color="#d97706"
					testId="rate-false-positive"
				/>
				<p className="text-xs text-slate-500">
					Errors are excluded from both rates.
				</p>
			</div>
		</Panel>
	);
}

function ByAttackType({ rows }: { rows: RunResults["by_attack_type"] }) {
	return (
		<Panel title="By attack type" aside="succeeded / attempts">
			{rows.length === 0 ? (
				<p className="text-sm text-slate-500">No red cases.</p>
			) : (
				<ul className="flex flex-col gap-3">
					{rows.map((row) => (
						<li key={row.type} className="flex flex-col gap-1.5">
							<span className="flex items-baseline justify-between gap-2 text-[13px]">
								<span className="font-mono text-xs">{row.type}</span>
								<span className="font-mono text-xs text-slate-600">
									{row.succeeded} / {row.attempts}
									{row.errors > 0 && (
										<span className="text-slate-400">
											{" "}
											· {row.errors} error{row.errors === 1 ? "" : "s"}
										</span>
									)}
								</span>
							</span>
							<span className="h-1.5 overflow-hidden rounded-full bg-slate-100">
								<span
									className="block h-full rounded-full bg-red-500"
									style={{
										width: `${row.attempts > 0 ? (row.succeeded / row.attempts) * 100 : 0}%`,
									}}
								/>
							</span>
						</li>
					))}
				</ul>
			)}
		</Panel>
	);
}

type Filter = "all" | "false_negative" | "false_positive" | "error";

const FILTERS: { id: Filter; label: string; outcome?: CaseOutcome }[] = [
	{ id: "all", label: "All" },
	{ id: "false_negative", label: "False negatives", outcome: "succeeded" },
	{ id: "false_positive", label: "False positives", outcome: "false_positive" },
	{ id: "error", label: "Errors", outcome: "error" },
];

const FILTER_TONES: Record<Filter, string> = {
	all: "aria-pressed:border-slate-900 aria-pressed:bg-slate-900 aria-pressed:text-white",
	false_negative:
		"text-red-700 aria-pressed:border-red-600 aria-pressed:bg-red-600 aria-pressed:text-white",
	false_positive:
		"text-amber-800 aria-pressed:border-amber-500 aria-pressed:bg-amber-500 aria-pressed:text-white",
	error:
		"text-slate-600 aria-pressed:border-slate-600 aria-pressed:bg-slate-600 aria-pressed:text-white",
};

function CaseTable({ runId, cases }: { runId: string; cases: CaseSummary[] }) {
	const [filter, setFilter] = useState<Filter>("all");
	const outcome = FILTERS.find((f) => f.id === filter)?.outcome;
	const shown = outcome ? cases.filter((c) => c.outcome === outcome) : cases;
	const count = (f: (typeof FILTERS)[number]) =>
		f.outcome
			? cases.filter((c) => c.outcome === f.outcome).length
			: cases.length;
	const href = (item: CaseSummary) =>
		arenaHref({ name: "case", runId, caseId: item.id });

	return (
		<section
			aria-label="Cases"
			className="flex flex-col overflow-hidden rounded-xl border bg-white shadow-sm"
		>
			<div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
				<h2 className="mr-1 text-sm font-semibold">Cases</h2>
				<fieldset
					aria-label="Filter cases"
					className="m-0 flex min-w-0 flex-wrap gap-1.5 border-0 p-0"
				>
					{FILTERS.map((f) => (
						<button
							key={f.id}
							type="button"
							aria-pressed={filter === f.id}
							onClick={() => setFilter(f.id)}
							className={cn(
								"rounded-full border px-2.5 py-1 text-xs font-medium transition-colors hover:bg-slate-50",
								FILTER_TONES[f.id],
							)}
						>
							{f.label} {count(f)}
						</button>
					))}
				</fieldset>
				<span className="ml-auto text-xs text-slate-500">
					Select a row to replay it
				</span>
			</div>
			<div className="overflow-x-auto">
				<table className="w-full min-w-[46rem] text-[13px]">
					<thead>
						<tr className="border-b text-left text-xs text-slate-500">
							<th className="px-4 py-2 font-medium">#</th>
							<th className="px-2 py-2 font-medium">Team</th>
							<th className="px-2 py-2 font-medium">Attack type</th>
							<th className="px-2 py-2 font-medium">Outcome</th>
							<th className="px-2 py-2 font-medium">Decided at</th>
							<th className="px-2 py-2 text-right font-medium">Turns</th>
							<th className="px-4 py-2 text-right font-medium">Duration</th>
						</tr>
					</thead>
					<tbody>
						{shown.map((item) => (
							<tr
								key={item.id}
								data-case={item.label}
								onClick={() => navigate(href(item))}
								className="cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50"
							>
								<td className="px-4 py-2">
									<a
										href={href(item)}
										aria-label={`Replay case ${item.label}`}
										onClick={(event) => event.stopPropagation()}
										className="font-mono text-blue-700 hover:underline"
									>
										{item.label}
									</a>
								</td>
								<td
									className={cn(
										"px-2 py-2",
										item.team === "red" ? "text-red-600" : "text-emerald-700",
									)}
								>
									{TEAM_LABELS[item.team]}
								</td>
								<td className="px-2 py-2 font-mono text-xs">
									{item.attack_type ?? "—"}
								</td>
								<td className="px-2 py-2">
									{item.outcome ? (
										<OutcomeBadge outcome={item.outcome} />
									) : (
										<span className="text-xs text-slate-400">
											{item.status === "stopped" ? "stopped" : "not run"}
										</span>
									)}
								</td>
								<td className="px-2 py-2 text-slate-700">
									{decidedLabel(item)}
									{item.error && (
										<ExpandableText
											text={item.error}
											threshold={60}
											className="max-w-md text-xs text-red-600"
										/>
									)}
								</td>
								<td className="px-2 py-2 text-right font-mono tabular-nums">
									{item.turns}
								</td>
								<td className="px-4 py-2 text-right font-mono tabular-nums">
									{formatDuration(item.duration)}
								</td>
							</tr>
						))}
						{shown.length === 0 && (
							<tr>
								<td
									colSpan={7}
									className="px-4 py-6 text-center text-slate-500"
								>
									No cases with this outcome.
								</td>
							</tr>
						)}
					</tbody>
				</table>
			</div>
		</section>
	);
}

/** Results of a finished run (R-RES-*): only values recorded by the backend. */
export function RunResultsView({ run }: { run: RunDetail }) {
	const query = useArenaQuery(
		(signal) => getRunResults(run.id, { signal }),
		[run.id, run.status],
	);
	const results = query.data;
	if (!results && query.loading)
		return <LoadingState label="Loading results…" />;
	if (query.error && !query.loading)
		return (
			<ErrorState
				title="The results could not be loaded."
				error={query.error}
				onRetry={query.reload}
			/>
		);
	if (!results) return null;

	return (
		<div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4 lg:p-6">
			{run.status !== "completed" && (
				<p className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-900">
					This run {STATUS_LABELS[run.status].toLowerCase()} before all cases
					were run. The results cover the cases that were completed.
				</p>
			)}
			<div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
				<Matrix matrix={results.matrix} />
				<OutcomeChart results={results} />
				<ByAttackType rows={results.by_attack_type} />
			</div>
			<CaseTable runId={run.id} cases={results.cases} />
			<ModelsUsed run={run} />
		</div>
	);
}
