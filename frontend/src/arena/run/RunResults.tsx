import { type ReactNode, useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { getRunResults } from "../api";
import { ErrorState, LoadingState, OutcomeBadge } from "../components";
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
	Rate,
	RunDetail,
	RunResults,
} from "../types";
import { useArenaQuery } from "../useArenaQuery";

function Card({
	label,
	children,
	caption,
	testId,
}: {
	label: string;
	children: ReactNode;
	caption: string;
	testId?: string;
}) {
	return (
		<section
			aria-label={label}
			data-testid={testId}
			className="flex flex-col gap-1.5 rounded-xl border bg-white p-4 shadow-sm"
		>
			<h2 className="text-[13px] text-slate-500">{label}</h2>
			<div className="flex items-baseline gap-2">{children}</div>
			<p className="text-xs text-slate-500">{caption}</p>
		</section>
	);
}

function RateValue({ rate, tone }: { rate: Rate; tone: string }) {
	return (
		<>
			<span className={cn("text-3xl font-semibold tracking-tight", tone)}>
				{formatPercent(rate)}
			</span>
			<span className="font-mono text-sm text-slate-500">
				{rate.numerator} / {rate.denominator}
			</span>
		</>
	);
}

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

const DECISION_DOTS: Record<string, string> = {
	guardrail: "bg-emerald-500",
	agent: "bg-blue-500",
	turn_limit: "bg-slate-400",
	success_check: "bg-red-500",
	error: "bg-slate-300",
};

function ByDecision({ rows }: { rows: RunResults["by_decision"] }) {
	return (
		<Panel title="Where it was decided" aside="red · green">
			<ul className="flex flex-col divide-y rounded-lg border">
				{rows.map((row) => (
					<li
						key={`${row.decided_by.kind}:${"guardrail_id" in row.decided_by ? row.decided_by.guardrail_id : ""}`}
						className="flex items-center gap-2.5 px-3 py-2 text-[13px]"
					>
						<span
							aria-hidden="true"
							className={cn(
								"size-2 shrink-0 rounded-full",
								DECISION_DOTS[row.decided_by.kind] ?? "bg-slate-400",
							)}
						/>
						<span className="min-w-0 flex-1">{row.label}</span>
						<span className="shrink-0 font-mono text-xs text-slate-600">
							{row.red} attack{row.red === 1 ? "" : "s"} · {row.green} benign
						</span>
					</li>
				))}
			</ul>
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
										<span className="text-xs text-slate-400">not run</span>
									)}
								</td>
								<td className="px-2 py-2 text-slate-700">
									{decidedLabel(item)}
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
	const errorsTotal = useMemo(
		() => (results ? results.errors.red + results.errors.green : 0),
		[results],
	);

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

	const { rates, matrix } = results;
	return (
		<div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4 lg:p-6">
			{run.status !== "completed" && (
				<p className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-900">
					This run {STATUS_LABELS[run.status].toLowerCase()} before all cases
					were run. The results cover the cases that were completed.
				</p>
			)}
			<div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
				<Card
					label="Attack success rate"
					caption="red cases that met the success check (errors excluded)"
					testId="rate-attack-success"
				>
					<RateValue rate={rates.attack_success} tone="text-red-600" />
				</Card>
				<Card
					label="False positive rate"
					caption="benign cases blocked by a guardrail (errors excluded)"
					testId="rate-false-positive"
				>
					<RateValue rate={rates.false_positive} tone="text-amber-600" />
				</Card>
				<Card
					label="Defended"
					caption="attacks that did not reach the objective"
					testId="defended"
				>
					<span className="text-3xl font-semibold tracking-tight text-emerald-700">
						{matrix.red.stopped}
					</span>
					<span className="font-mono text-sm text-slate-500">
						/ {rates.attack_success.denominator}
					</span>
				</Card>
				<Card
					label="Errors"
					caption={`red ${results.errors.red} · green ${results.errors.green} · excluded from both rates`}
					testId="errors"
				>
					<span className="text-3xl font-semibold tracking-tight">
						{errorsTotal}
					</span>
				</Card>
			</div>
			<div className="grid gap-4 lg:grid-cols-3">
				<Matrix matrix={matrix} />
				<ByAttackType rows={results.by_attack_type} />
				<ByDecision rows={results.by_decision} />
			</div>
			<CaseTable runId={run.id} cases={results.cases} />
		</div>
	);
}
