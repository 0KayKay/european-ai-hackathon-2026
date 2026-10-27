import type {
	ArenaGraph,
	GraphNode,
	LevelDetail,
	LevelSummary,
} from "../types";
import { emailReviewHard } from "./email-review-hard";
import { hiddenMailboxHard } from "./hidden-mailbox-hard";

export { emailReviewHard, hiddenMailboxHard };

/** GET /api/v1/arena/levels */
export const levelsFixture: LevelSummary[] = [
	{
		id: "hidden-mailbox",
		title: "Level 1 · Hidden Mailbox",
		description:
			"Onboarding assistant with a shared drive; one restricted file holds a finance mailbox.",
		channel: "direct",
		turns: "multi",
		difficulties: ["easy", "medium", "hard"],
	},
	{
		id: "email-review",
		title: "Level 2 · Email Review",
		description:
			"Mail assistant that reads the inbox and can send email; the attacker plants one email.",
		channel: "indirect",
		turns: "single",
		difficulties: ["easy", "medium", "hard"],
	},
];

/** A deep copy, so tests can modify a fixture freely. */
export const cloneLevel = (level: LevelDetail): LevelDetail =>
	structuredClone(level);

/**
 * Moves a guardrail to another position the way the backend graph builder would after a config
 * change: the member joins `guardrails:<position>` (created if needed) and empty groups disappear.
 */
export function moveGuardrail(
	graph: ArenaGraph,
	guardrailId: string,
	position: string,
): ArenaGraph {
	const groupId = `guardrails:${position}`;
	let nodes: GraphNode[] = graph.nodes.map((node) =>
		node.id === `guardrail:${guardrailId}` ? { ...node, group: groupId } : node,
	);
	if (!nodes.some((node) => node.id === groupId))
		nodes.push({
			id: groupId,
			kind: "guardrails",
			label: "Guardrails",
			subtitle: position,
			group: null,
		});
	const used = new Set(nodes.map((node) => node.group).filter(Boolean));
	const removed = new Set(
		nodes
			.filter((node) => node.kind === "guardrails" && !used.has(node.id))
			.map((node) => node.id),
	);
	nodes = nodes.filter((node) => !removed.has(node.id));
	return {
		...graph,
		nodes,
		edges: graph.edges.filter(
			(edge) => !removed.has(edge.source) && !removed.has(edge.target),
		),
	};
}
