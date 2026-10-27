import { createContext, useContext } from "react";

/**
 * Highlight state for replay and live views. Keys are graph node ids (members such as
 * `guardrail:input-judge` included) and graph edge ids as used in `CaseEvent.node` / `.edge`.
 *
 * When a highlight is given, nodes and edges without an entry are drawn dimmed, so the visited
 * path stands out.
 */
export type NodeHighlight = "visited" | "current" | "blocked" | "alert";
export type EdgeHighlight = "visited" | "current";

export interface GraphHighlight {
	nodes?: Record<string, NodeHighlight>;
	edges?: Record<string, EdgeHighlight>;
	/** Short text shown on a node or member row, e.g. "SAFE · passed" or "1.31s". */
	annotations?: Record<string, string>;
}

export interface GraphContextValue {
	selectedId: string | null;
	onSelect?: (nodeId: string) => void;
	highlight?: GraphHighlight;
}

export const GraphContext = createContext<GraphContextValue>({
	selectedId: null,
});

export const useGraphContext = () => useContext(GraphContext);
