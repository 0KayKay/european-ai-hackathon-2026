import { PlayIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { getLevel } from "../api";
import {
	ArenaShell,
	DifficultySwitch,
	ErrorState,
	LoadingState,
	SurfaceBadge,
} from "../components";
import { ArenaGraph, GraphLegend } from "../graph/ArenaGraph";
import { nodeKind } from "../graph/layout";
import { arenaHref, replaceHash } from "../router";
import { RunList } from "../run/RunList";
import { StartRunDialog } from "../run/StartRunDialog";
import type {
	ArenaGraph as ArenaGraphData,
	Difficulty,
	LevelDetail,
} from "../types";
import { useArenaQuery } from "../useArenaQuery";
import { ConfigSource, configLinesFor } from "./ConfigSource";
import { NodeInspector } from "./NodeInspector";
import { useSourceEditor } from "./useSourceEditor";

/** Runs listed under "Recent runs" (newest first, active ones on top). */
const RECENT_RUNS = 5;

export const LEAVE_CONFIRM =
	"You have unsaved changes to this level. Leave and discard them?";

/**
 * Asks before leaving with unsaved edits: a hash change (links, difficulty switch, back button)
 * is undone unless confirmed, and closing or reloading the tab shows the browser's warning.
 * Registered by the level view, so it runs before the app's route listener.
 */
function useUnsavedChangesGuard(dirty: boolean, onLeave: () => void) {
	const latest = useRef({ dirty, onLeave });
	latest.current = { dirty, onLeave };
	useEffect(() => {
		let current = window.location.hash;
		const onHashChange = () => {
			const next = window.location.hash;
			if (next === current) return;
			if (latest.current.dirty) {
				if (!window.confirm(LEAVE_CONFIRM)) {
					window.history.replaceState(window.history.state, "", current);
					return;
				}
				latest.current.onLeave();
			}
			current = next;
		};
		const onBeforeUnload = (event: BeforeUnloadEvent) => {
			if (!latest.current.dirty) return;
			event.preventDefault();
			event.returnValue = "";
		};
		window.addEventListener("hashchange", onHashChange);
		window.addEventListener("beforeunload", onBeforeUnload);
		return () => {
			window.removeEventListener("hashchange", onHashChange);
			window.removeEventListener("beforeunload", onBeforeUnload);
		};
	}, []);
}

/** First LLM guardrail if there is one (the interesting part of a hard level), else the agent. */
export function defaultSelection(graph: ArenaGraphData): string | null {
	const judge = graph.nodes.find(
		(n) => nodeKind(n) === "guardrail" && n.details?.model,
	);
	return (
		judge?.id ??
		graph.nodes.find((n) => nodeKind(n) === "agent")?.id ??
		graph.nodes[0]?.id ??
		null
	);
}

function teamSummary(detail: LevelDetail) {
	const red = detail.red_team?.cases;
	const green = detail.green_team?.cases;
	const parts = [
		red !== undefined && `${red} attack${red === 1 ? "" : "s"}`,
		green !== undefined && `${green} benign`,
	].filter(Boolean);
	return parts.join(" · ");
}

export function LevelView({
	level,
	difficulty,
	start = false,
}: {
	level: string;
	difficulty: Difficulty;
	/** Open the start-run dialog (from "Run again"); the flag is dropped from the hash. */
	start?: boolean;
}) {
	const query = useArenaQuery(
		(signal) => getLevel(level, difficulty, { signal }),
		[level, difficulty],
	);
	const detail = query.data;
	const [selectedId, setSelectedId] = useState<string | null>(null);
	const [activePath, setActivePath] = useState("level.yaml");
	const [startOpen, setStartOpen] = useState(start);

	useEffect(() => {
		if (!start) return;
		setStartOpen(true);
		replaceHash(arenaHref({ name: "level", level, difficulty }));
	}, [start, level, difficulty]);

	const graph = detail?.graph;
	const nodesById = useMemo(
		() => new Map((graph?.nodes ?? []).map((n) => [n.id, n])),
		[graph],
	);
	// Keep the selection across difficulties when the node still exists.
	useEffect(() => {
		if (!graph) return;
		setSelectedId((current) =>
			current && graph.nodes.some((n) => n.id === current)
				? current
				: defaultSelection(graph),
		);
	}, [graph]);

	const files = detail?.source.files ?? [];
	const selected = selectedId ? (nodesById.get(selectedId) ?? null) : null;
	const highlight = useMemo(
		() =>
			selected ? configLinesFor(selected.id, nodeKind(selected), files) : null,
		[selected, files],
	);

	const select = useCallback(
		(id: string) => {
			setSelectedId(id);
			const node = nodesById.get(id);
			if (node && files.length && configLinesFor(id, nodeKind(node), files))
				setActivePath(
					files.find((f) => /level\.ya?ml$/.test(f.path))?.path ?? "level.yaml",
				);
		},
		[nodesById, files],
	);

	const title = detail?.level === level ? detail.title : level;
	// While another difficulty loads (or fails to), the previous one stays on screen; a run must only
	// start for the difficulty in the route.
	const runnable =
		detail?.level === level && detail.difficulty === difficulty
			? detail
			: undefined;
	// A save or reset answers with the new level view: graph, inspector and badges render from it.
	const editor = useSourceEditor({
		level,
		difficulty,
		detail: runnable,
		onDetail: query.setData,
	});
	useUnsavedChangesGuard(editor.dirty, editor.discard);
	const surface = detail?.surface ?? graph?.surface;

	return (
		<ArenaShell
			crumbs={[
				{ label: "Levels", href: arenaHref({ name: "levels" }) },
				{ label: title },
			]}
			center={
				<DifficultySwitch
					value={difficulty}
					hrefFor={(d) => arenaHref({ name: "level", level, difficulty: d })}
				/>
			}
			actions={
				<>
					{detail && (
						<span className="hidden text-[13px] text-slate-500 xl:inline">
							{teamSummary(detail)}
						</span>
					)}
					<Button
						type="button"
						size="sm"
						disabled={!runnable}
						onClick={() => setStartOpen(true)}
					>
						<PlayIcon className="size-3.5" aria-hidden="true" />
						Run evaluation
					</Button>
				</>
			}
		>
			{runnable && (
				<StartRunDialog
					key={`${runnable.level}/${runnable.difficulty}`}
					detail={runnable}
					open={startOpen}
					onOpenChange={setStartOpen}
				/>
			)}
			{!detail && query.loading && <LoadingState label="Loading level…" />}
			{query.error && !query.loading && (
				<ErrorState
					title="This level could not be loaded."
					error={query.error}
					onRetry={query.reload}
				/>
			)}
			{detail && graph && !query.error && (
				<div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:grid lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)_minmax(0,20rem)] xl:grid-cols-[minmax(0,22rem)_minmax(0,1fr)_minmax(0,21rem)] lg:overflow-hidden">
					<div className="flex h-80 min-h-0 shrink-0 flex-col lg:h-auto">
						<ConfigSource
							files={files}
							activePath={activePath}
							onSelectPath={setActivePath}
							highlight={highlight}
							editor={runnable ? editor : undefined}
							modified={detail.modified ?? false}
							overrideError={detail.override_error}
						/>
					</div>

					<section
						aria-label="Execution graph"
						className="flex min-h-0 shrink-0 flex-col"
					>
						<div className="flex h-12 shrink-0 items-center gap-3 border-b bg-white px-4">
							<h2 className="text-sm font-semibold">Execution graph</h2>
							{surface && (
								<SurfaceBadge channel={surface.channel} turns={surface.turns} />
							)}
							<span className="hidden truncate text-xs text-slate-500 sm:inline">
								{surface?.channel === "indirect"
									? "attacker never sees a reply"
									: "rendered from level.yaml"}
							</span>
							{query.loading && <Spinner className="ml-auto size-4" />}
						</div>
						<div className="relative h-[34rem] min-h-0 lg:h-auto lg:flex-1">
							<ArenaGraph
								graph={graph}
								selectedId={selectedId}
								onSelect={select}
							/>
						</div>
						<div className="shrink-0 border-t bg-white px-4 py-2.5">
							<GraphLegend graph={graph} />
						</div>
					</section>

					<aside
						aria-label="Inspector"
						className="flex min-h-0 shrink-0 flex-col border-t bg-slate-50/60 lg:border-t-0 lg:border-l"
					>
						<div className="flex h-12 shrink-0 items-center border-b bg-white px-4 text-[13px] text-slate-500">
							Inspector
						</div>
						<div className="min-h-0 flex-1 overflow-y-auto">
							<NodeInspector
								node={selected}
								graph={graph}
								models={detail.models ?? []}
								onSelect={select}
								onOpenFile={(path) => {
									if (files.some((f) => f.path === path)) setActivePath(path);
								}}
							/>
						</div>
						<section
							aria-labelledby="recent-runs-heading"
							className="flex max-h-[45%] shrink-0 flex-col border-t bg-white"
						>
							<div className="flex h-10 shrink-0 items-center justify-between gap-3 border-b px-4">
								<h2
									id="recent-runs-heading"
									className="text-[13px] font-semibold"
								>
									Recent runs
								</h2>
								<a
									href={arenaHref({ name: "levels" })}
									className="text-xs font-medium text-blue-600 hover:underline"
								>
									All runs
								</a>
							</div>
							<div className="min-h-0 overflow-y-auto">
								<RunList
									level={level}
									limit={RECENT_RUNS}
									compact
									label="Recent runs"
								/>
							</div>
						</section>
					</aside>
				</div>
			)}
		</ArenaShell>
	);
}
