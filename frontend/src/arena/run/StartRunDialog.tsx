import { PlayIcon } from "lucide-react";
import {
	type FormEvent,
	type ReactNode,
	useEffect,
	useId,
	useMemo,
	useState,
} from "react";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import { createRun, listDatasets } from "../api";
import { difficultyLabel } from "../components";
import { arenaHref, navigate } from "../router";
import type {
	DatasetSource,
	DatasetSummary,
	LevelDetail,
	Team,
} from "../types";
import { useArenaQuery } from "../useArenaQuery";

type Source = DatasetSource["source"];

const dateFormat = new Intl.DateTimeFormat("en-GB", {
	day: "numeric",
	month: "short",
	hour: "2-digit",
	minute: "2-digit",
	timeZone: "UTC",
});
const datasetLabel = (d: DatasetSummary) =>
	`${d.name} · ${d.size} case${d.size === 1 ? "" : "s"} · ${dateFormat.format(new Date(d.created_at))} UTC`;

function SourceChoice({
	team,
	source,
	onSource,
	datasetId,
	onDataset,
	datasets,
	loading,
	generateHint,
}: {
	team: Team;
	source: Source;
	onSource: (source: Source) => void;
	datasetId: string;
	onDataset: (id: string) => void;
	datasets: DatasetSummary[];
	loading: boolean;
	generateHint: string;
}) {
	const name = useId();
	const selectId = useId();
	const none = !loading && datasets.length === 0;
	const option = (value: Source, label: string, hint: string) => (
		<label
			className={cn(
				"flex cursor-pointer items-start gap-2.5 rounded-lg border bg-white px-3 py-2.5 text-sm transition-colors",
				source === value
					? "border-blue-500 bg-blue-50/50"
					: "hover:border-slate-300",
				value === "dataset" && none && "cursor-not-allowed opacity-60",
			)}
		>
			<input
				type="radio"
				name={name}
				value={value}
				checked={source === value}
				disabled={value === "dataset" && none}
				onChange={() => onSource(value)}
				className="mt-0.5 accent-blue-600"
			/>
			<span className="flex flex-col gap-0.5">
				<span className="font-medium">{label}</span>
				<span className="text-xs text-slate-500">{hint}</span>
			</span>
		</label>
	);
	return (
		<div className="flex flex-col gap-2">
			<div className="grid gap-2 sm:grid-cols-2">
				{option("generate", "Generate new", generateHint)}
				{option(
					"dataset",
					"Use stored dataset",
					loading
						? "Loading stored datasets…"
						: none
							? `No stored ${team} datasets for this level yet.`
							: "Runs exactly the stored cases, unchanged.",
				)}
			</div>
			{source === "dataset" && datasets.length > 0 && (
				<div className="flex flex-col gap-1">
					<label htmlFor={selectId} className="text-xs text-slate-500">
						{team === "red" ? "Red" : "Green"} dataset
					</label>
					<select
						id={selectId}
						value={datasetId}
						onChange={(event) => onDataset(event.target.value)}
						className="h-9 w-full rounded-md border bg-white px-2.5 text-sm"
					>
						{datasets.map((d) => (
							<option key={d.id} value={d.id}>
								{datasetLabel(d)}
							</option>
						))}
					</select>
				</div>
			)}
		</div>
	);
}

function Section({
	title,
	tone,
	summary,
	children,
}: {
	title: string;
	tone: "red" | "green";
	summary: ReactNode;
	children: ReactNode;
}) {
	return (
		<section
			aria-label={title}
			className="flex flex-col gap-3 rounded-xl border bg-slate-50/70 p-4"
		>
			<div className="flex flex-wrap items-baseline justify-between gap-2">
				<h3
					className={cn(
						"text-xs font-semibold tracking-[0.08em] uppercase",
						tone === "red" ? "text-red-600" : "text-emerald-700",
					)}
				>
					{title}
				</h3>
				<div className="text-xs text-slate-500">{summary}</div>
			</div>
			{children}
		</section>
	);
}

/**
 * Starts an evaluation run: red and green cases are generated fresh or taken from a stored
 * dataset (R-DAT-2). Red cases of direct levels are always generated during the run (R-DAT-3).
 */
export function StartRunDialog({
	detail,
	open,
	onOpenChange,
}: {
	detail: LevelDetail;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}) {
	const direct = detail.surface.channel === "direct";
	const datasets = useArenaQuery(
		(signal) =>
			open ? listDatasets(detail.level, { signal }) : Promise.resolve([]),
		[open, detail.level],
	);
	const [red, green] = useMemo(() => {
		const all = datasets.data ?? [];
		return [
			all.filter((d) => d.team === "red"),
			all.filter((d) => d.team === "green"),
		];
	}, [datasets.data]);

	const [redSource, setRedSource] = useState<Source>("generate");
	const [greenSource, setGreenSource] = useState<Source>("generate");
	const [redDataset, setRedDataset] = useState("");
	const [greenDataset, setGreenDataset] = useState("");
	const [submitting, setSubmitting] = useState(false);
	const [error, setError] = useState<string>();

	// Preselect the newest dataset of each team once the list is there.
	useEffect(() => {
		setRedDataset((id) => id || red[0]?.id || "");
		setGreenDataset((id) => id || green[0]?.id || "");
	}, [red, green]);

	useEffect(() => {
		if (!open) {
			setError(undefined);
			setSubmitting(false);
		}
	}, [open]);

	const attacks = detail.red_team?.attacks ?? [];
	const redCases = detail.red_team?.cases;
	// Indirect levels report `max_turns: 1`; only a multi-turn surface has a turn budget worth showing.
	const maxTurns =
		detail.surface.turns === "multi" ? detail.red_team?.max_turns : undefined;
	const greenCases = detail.green_team?.cases;
	const chosenRed = red.find((d) => d.id === redDataset);
	const chosenGreen = green.find((d) => d.id === greenDataset);

	const submit = async (event: FormEvent) => {
		event.preventDefault();
		setSubmitting(true);
		setError(undefined);
		const source = (kind: Source, id: string): DatasetSource =>
			kind === "dataset" && id
				? { source: "dataset", dataset_id: id }
				: { source: "generate" };
		try {
			const run = await createRun({
				level: detail.level,
				difficulty: detail.difficulty,
				red: direct ? { source: "generate" } : source(redSource, redDataset),
				green: source(greenSource, greenDataset),
			});
			onOpenChange(false);
			navigate(arenaHref({ name: "run", runId: run.id }));
		} catch (reason) {
			setError(reason instanceof Error ? reason.message : String(reason));
			setSubmitting(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl">
				<form onSubmit={submit} className="flex flex-col gap-4">
					<DialogHeader>
						<DialogTitle>Run evaluation</DialogTitle>
						<DialogDescription>
							{detail.title} · {difficultyLabel(detail.difficulty)}. All cases
							run to completion; you can stop the run at any time.
						</DialogDescription>
					</DialogHeader>

					<Section
						title="Red team · attacks"
						tone="red"
						summary={
							redSource === "dataset" && chosenRed && !direct
								? `${chosenRed.size} stored cases`
								: redCases !== undefined
									? `${redCases} case${redCases === 1 ? "" : "s"}${maxTurns ? ` · ≤ ${maxTurns} turns` : ""}`
									: null
						}
					>
						{attacks.length > 0 && (
							<ul
								aria-label="Configured attacks"
								className="flex flex-wrap gap-1.5"
							>
								{attacks.map((attack) => (
									<li
										key={attack.type}
										className="rounded-md border bg-white px-2 py-1 font-mono text-xs text-slate-700"
									>
										{attack.type} × {attack.attempts}
										{attack.turns && (
											<span className="text-slate-400">
												{" "}
												· {attack.turns}-turn
											</span>
										)}
									</li>
								))}
							</ul>
						)}
						{direct ? (
							<p className="rounded-lg border border-dashed bg-white px-3 py-2.5 text-sm text-slate-600">
								Red cases are generated during each run: the attacker talks to
								the agent directly and adapts every turn, so these cases cannot
								be stored and reused.
							</p>
						) : (
							<SourceChoice
								team="red"
								source={redSource}
								onSource={setRedSource}
								datasetId={redDataset}
								onDataset={setRedDataset}
								datasets={red}
								loading={datasets.loading}
								generateHint="The attacker writes fresh content for the injection point."
							/>
						)}
					</Section>

					<Section
						title="Green team · benign cases"
						tone="green"
						summary={
							greenSource === "dataset" && chosenGreen
								? `${chosenGreen.size} stored cases`
								: greenCases !== undefined
									? `${greenCases} case${greenCases === 1 ? "" : "s"}`
									: null
						}
					>
						<SourceChoice
							team="green"
							source={greenSource}
							onSource={setGreenSource}
							datasetId={greenDataset}
							onDataset={setGreenDataset}
							datasets={green}
							loading={datasets.loading}
							generateHint="The generator writes fresh benign cases for normal use."
						/>
					</Section>

					{datasets.error && (
						<p role="alert" className="text-sm text-amber-700">
							Stored datasets could not be loaded: {datasets.error.message}
						</p>
					)}
					{error && (
						<p role="alert" className="text-sm text-red-600">
							The run could not be started: {error}
						</p>
					)}

					<DialogFooter>
						<Button
							type="button"
							variant="outline"
							onClick={() => onOpenChange(false)}
						>
							Cancel
						</Button>
						<Button type="submit" disabled={submitting}>
							{submitting ? (
								<Spinner className="size-3.5" aria-hidden="true" />
							) : (
								<PlayIcon className="size-3.5" aria-hidden="true" />
							)}
							Start run
						</Button>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
