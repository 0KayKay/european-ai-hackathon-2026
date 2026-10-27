import { ArenaShell } from "./components";
import { LevelList } from "./LevelList";
import { LevelView } from "./level/LevelView";
import { type ArenaRoute, arenaHref } from "./router";

function Placeholder({
	crumbs,
	title,
	text,
}: {
	crumbs: { label: string; href?: string }[];
	title: string;
	text: string;
}) {
	return (
		<ArenaShell crumbs={crumbs}>
			<div className="flex flex-1 items-center justify-center p-10">
				<div className="flex max-w-md flex-col items-center gap-2 rounded-xl border bg-white p-6 text-center shadow-sm">
					<h1 className="font-semibold">{title}</h1>
					<p className="text-sm text-slate-500">{text}</p>
					<a
						href={arenaHref({ name: "levels" })}
						className="mt-2 text-sm font-medium text-blue-600 hover:underline"
					>
						Go to levels
					</a>
				</div>
			</div>
		</ArenaShell>
	);
}

/** The arena area; one screen per hash route. */
export function ArenaApp({ route }: { route: ArenaRoute }) {
	switch (route.name) {
		case "levels":
			return <LevelList />;
		case "level":
			return (
				<LevelView
					key={route.level}
					level={route.level}
					difficulty={route.difficulty}
				/>
			);
		case "run":
			return (
				<Placeholder
					crumbs={[{ label: "Runs" }, { label: route.runId }]}
					title={`Run ${route.runId}`}
					text="The run view is not available yet."
				/>
			);
		case "case":
			return (
				<Placeholder
					crumbs={[
						{ label: "Runs" },
						{
							label: route.runId,
							href: arenaHref({ name: "run", runId: route.runId }),
						},
						{ label: `Case ${route.caseId}` },
					]}
					title={`Case ${route.caseId}`}
					text="The case replay is not available yet."
				/>
			);
		case "not_found":
			return (
				<Placeholder
					crumbs={[{ label: "Not found" }]}
					title="Page not found"
					text={`There is no arena page at “${route.path}”.`}
				/>
			);
	}
}
