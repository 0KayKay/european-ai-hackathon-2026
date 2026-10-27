import { ArrowRightIcon } from "lucide-react";
import { listLevels } from "./api";
import {
	ArenaShell,
	difficultyLabel,
	ErrorState,
	LoadingState,
	SurfaceBadge,
} from "./components";
import { arenaHref } from "./router";
import type { LevelSummary } from "./types";
import { useArenaQuery } from "./useArenaQuery";

function LevelCard({ level }: { level: LevelSummary }) {
	const first = level.difficulties[0] ?? "easy";
	return (
		<li className="flex flex-col gap-4 rounded-xl border bg-white p-5 shadow-sm transition-shadow hover:shadow-md">
			<div className="flex flex-wrap items-center gap-2">
				<SurfaceBadge channel={level.channel} turns={level.turns} />
			</div>
			<div className="flex flex-col gap-1.5">
				<h2 className="text-base font-semibold tracking-tight">
					<a
						href={arenaHref({
							name: "level",
							level: level.id,
							difficulty: first,
						})}
						className="hover:text-blue-700 focus-visible:outline-2 focus-visible:outline-blue-500"
					>
						{level.title}
					</a>
				</h2>
				<p className="text-sm leading-relaxed text-slate-600">
					{level.description}
				</p>
			</div>
			<dl className="grid grid-cols-2 gap-2 text-[13px]">
				<div className="rounded-lg bg-slate-50 px-3 py-2">
					<dt className="text-slate-500">Attacker</dt>
					<dd className="font-medium">
						{level.channel === "direct"
							? "Chat user (direct)"
							: "Planted data (indirect)"}
					</dd>
				</div>
				<div className="rounded-lg bg-slate-50 px-3 py-2">
					<dt className="text-slate-500">Turns</dt>
					<dd className="font-medium">
						{level.turns === "multi"
							? "Multi-turn conversation"
							: "Single turn per case"}
					</dd>
				</div>
			</dl>
			<nav
				aria-label={`${level.title} difficulties`}
				className="mt-auto flex flex-wrap gap-2"
			>
				{level.difficulties.map((difficulty) => (
					<a
						key={difficulty}
						href={arenaHref({ name: "level", level: level.id, difficulty })}
						className="flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[13px] font-medium text-slate-700 transition-colors hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700"
					>
						{difficultyLabel(difficulty)}
						<ArrowRightIcon className="size-3.5" aria-hidden="true" />
					</a>
				))}
			</nav>
		</li>
	);
}

export function LevelList() {
	const query = useArenaQuery((signal) => listLevels({ signal }), []);
	return (
		<ArenaShell crumbs={[{ label: "Levels" }]}>
			<div className="min-h-0 flex-1 overflow-y-auto">
				<div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-8 sm:px-6">
					<div className="flex flex-col gap-1">
						<h1 className="text-xl font-semibold tracking-tight">Levels</h1>
						<p className="text-sm text-slate-600">
							Each level is described by a config file. Open a level to see its
							execution graph, guardrails and prompts.
						</p>
					</div>
					{query.loading && !query.data && (
						<LoadingState label="Loading levels…" />
					)}
					{query.error && (
						<ErrorState
							title="Levels could not be loaded."
							error={query.error}
							onRetry={query.reload}
						/>
					)}
					{query.data && !query.error && (
						<ul
							aria-label="Levels"
							className="grid gap-4 md:grid-cols-2 xl:grid-cols-3"
						>
							{query.data.map((level) => (
								<LevelCard key={level.id} level={level} />
							))}
						</ul>
					)}
					{query.data?.length === 0 && (
						<p className="text-sm text-slate-500">No levels are configured.</p>
					)}
				</div>
			</div>
		</ArenaShell>
	);
}
