import "@xyflow/react/dist/style.css";
import {
	Background,
	BackgroundVariant,
	type FitViewOptions,
	MarkerType,
	Panel,
	ReactFlow,
	ReactFlowProvider,
	useReactFlow,
} from "@xyflow/react";
import { MaximizeIcon, MinusIcon, PlusIcon } from "lucide-react";
import { type CSSProperties, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ArenaGraph as ArenaGraphData } from "../types";
import { GraphContext, type GraphHighlight } from "./context";
import { buildFlow, type FlowEdge } from "./layout";
import { nodeTypes } from "./nodes";

interface EdgeTone {
	color: string;
	dashed?: boolean;
	legend: string;
}

/** Edge styles by kind (IMPLEMENTATION §4 edge kinds). */
export const EDGE_TONES: Record<string, EdgeTone> = {
	attacker: { color: "#e5484d", legend: "attacker-controlled text" },
	flow: { color: "#94a3b8", legend: "agent flow" },
	tool_call: { color: "#3b82f6", legend: "tool call / result" },
	tool_result: { color: "#3b82f6", legend: "tool call / result" },
	success: { color: "#8b5cf6", dashed: true, legend: "success check" },
	reply_loop: {
		color: "#64748b",
		dashed: true,
		legend: "next turn (reply loop)",
	},
};
const FALLBACK_TONE: EdgeTone = { color: "#94a3b8", legend: "other" };

function styleEdge(edge: FlowEdge, highlight?: GraphHighlight): FlowEdge {
	const kind = edge.data?.kind ?? "flow";
	const tone = EDGE_TONES[kind] ?? FALLBACK_TONE;
	const states = (edge.data?.graphEdgeIds ?? [])
		.map((id) => highlight?.edges?.[id])
		.filter(Boolean);
	const current = states.includes("current");
	const visited = states.length > 0;
	const dimmed = Boolean(highlight?.edges) && !visited;
	const style: CSSProperties = {
		stroke: tone.color,
		strokeWidth: current ? 3 : visited ? 2.5 : 1.75,
		strokeDasharray: tone.dashed ? "6 5" : undefined,
		opacity: dimmed ? 0.25 : 1,
	};
	return {
		...edge,
		style,
		animated: current,
		markerEnd: {
			type: MarkerType.ArrowClosed,
			color: tone.color,
			width: 16,
			height: 16,
		},
		pathOptions:
			kind === "reply_loop"
				? { offset: 56, borderRadius: 10 }
				: { borderRadius: 10 },
		labelStyle: {
			fill: kind === "attacker" ? "#c2410c" : "#475569",
			fontSize: 11,
		},
		labelBgStyle: { fill: "#ffffff", fillOpacity: 0.92 },
		labelBgPadding: [6, 3],
		labelBgBorderRadius: 999,
		className: cn(`arena-edge-${kind}`),
	} as FlowEdge;
}

function ZoomControls({ fitViewOptions }: { fitViewOptions: FitViewOptions }) {
	const { zoomIn, zoomOut, fitView } = useReactFlow();
	return (
		<Panel position="top-right" className="!m-3 flex gap-1">
			<Button
				type="button"
				variant="outline"
				size="icon-sm"
				aria-label="Zoom out"
				onClick={() => void zoomOut()}
			>
				<MinusIcon />
			</Button>
			<Button
				type="button"
				variant="outline"
				size="icon-sm"
				aria-label="Zoom in"
				onClick={() => void zoomIn()}
			>
				<PlusIcon />
			</Button>
			<Button
				type="button"
				variant="outline"
				size="icon-sm"
				aria-label="Fit graph to view"
				onClick={() => void fitView(fitViewOptions)}
			>
				<MaximizeIcon />
			</Button>
		</Panel>
	);
}

export function GraphLegend({ graph }: { graph: ArenaGraphData }) {
	const entries = useMemo(() => {
		const seen = new Map<string, EdgeTone>();
		for (const kind of Object.keys(EDGE_TONES)) {
			if (!graph.edges.some((e) => e.kind === kind)) continue;
			const tone = EDGE_TONES[kind];
			if (!seen.has(tone.legend)) seen.set(tone.legend, tone);
		}
		return [...seen.values()];
	}, [graph]);
	return (
		<ul
			aria-label="Legend"
			className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs text-slate-500"
		>
			{entries.map((tone) => (
				<li key={tone.legend} className="flex items-center gap-2">
					<svg width="22" height="6" aria-hidden="true">
						<line
							x1="1"
							y1="3"
							x2="21"
							y2="3"
							stroke={tone.color}
							strokeWidth="2"
							strokeDasharray={tone.dashed ? "4 3" : undefined}
						/>
					</svg>
					{tone.legend}
				</li>
			))}
		</ul>
	);
}

export interface ArenaGraphProps {
	graph: ArenaGraphData;
	selectedId?: string | null;
	onSelect?: (nodeId: string) => void;
	/** Visited/current state for replay and live views. */
	highlight?: GraphHighlight;
	className?: string;
}

/** Execution graph of a level, rendered from the backend graph JSON. */
export function ArenaGraph({
	graph,
	selectedId = null,
	onSelect,
	highlight,
	className,
}: ArenaGraphProps) {
	const flow = useMemo(() => buildFlow(graph), [graph]);
	const edges = useMemo(
		() => flow.edges.map((edge) => styleEdge(edge, highlight)),
		[flow, highlight],
	);
	const context = useMemo(
		() => ({ selectedId, onSelect, highlight }),
		[selectedId, onSelect, highlight],
	);
	// Leave room on the left for the reply loop and its label, which lie outside the nodes.
	const fitViewOptions = useMemo(() => {
		const loop = graph.edges.some((e) => e.kind === "reply_loop");
		return {
			padding: {
				top: "28px",
				right: "28px",
				bottom: "28px",
				left: loop ? "120px" : "28px",
			},
		} as const;
	}, [graph]);
	// Re-fit whenever a different graph (e.g. another difficulty) is shown.
	const layoutKey = useMemo(
		() =>
			flow.nodes
				.map(
					(n) =>
						`${n.id}@${n.position.x},${n.position.y}:${n.data.members.length}`,
				)
				.join("|"),
		[flow],
	);

	return (
		<GraphContext.Provider value={context}>
			<ReactFlowProvider>
				<div
					className={cn("relative h-full w-full", className)}
					data-testid="arena-graph"
					data-channel={graph.surface.channel}
				>
					<ReactFlow
						key={layoutKey}
						nodes={flow.nodes}
						edges={edges}
						nodeTypes={nodeTypes}
						fitView
						fitViewOptions={fitViewOptions}
						minZoom={0.25}
						maxZoom={1.75}
						nodesDraggable={false}
						nodesConnectable={false}
						elementsSelectable={false}
						edgesFocusable={false}
					>
						<Background
							variant={BackgroundVariant.Dots}
							gap={20}
							size={1.2}
							color="#cbd5e1"
						/>
						<ZoomControls fitViewOptions={fitViewOptions} />
					</ReactFlow>
				</div>
			</ReactFlowProvider>
		</GraphContext.Provider>
	);
}
