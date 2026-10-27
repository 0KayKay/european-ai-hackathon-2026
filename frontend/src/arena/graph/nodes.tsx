import {
	Handle,
	type NodeProps,
	type NodeTypes,
	Position,
} from "@xyflow/react";
import {
	BotIcon,
	BoxIcon,
	CircleCheckIcon,
	CrosshairIcon,
	DatabaseIcon,
	ListFilterIcon,
	type LucideIcon,
	MailWarningIcon,
	MessageSquareQuoteIcon,
	ReplyIcon,
	ShieldCheckIcon,
	WrenchIcon,
} from "lucide-react";
import { memo, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import type { GraphNode } from "../types";
import { type NodeHighlight, useGraphContext } from "./context";
import {
	type FlowNode,
	type FlowNodeType,
	flowTypeName,
	formatPosition,
	groupPosition,
	SIDE_HANDLE_OFFSETS,
} from "./layout";

// Handles --------------------------------------------------------------------

const SIDES = [
	["left", Position.Left],
	["right", Position.Right],
	["top", Position.Top],
	["bottom", Position.Bottom],
] as const;
const VERTICAL_OFFSETS = { a: "50%", b: "68%" } as const;

/** Invisible handles on every side; `pickHandles` in layout.ts chooses which ones an edge uses. */
function Handles() {
	return SIDES.flatMap(([side, position]) =>
		(["a", "b"] as const).flatMap((slot) =>
			(["source", "target"] as const).map((type) => (
				<Handle
					key={`${type}-${side}-${slot}`}
					id={`${type === "source" ? "s" : "t"}-${side}-${slot}`}
					type={type}
					position={position}
					isConnectable={false}
					className="!pointer-events-none !min-h-0 !min-w-0 !size-1 !border-0 !bg-transparent"
					style={
						side === "left" || side === "right"
							? { top: SIDE_HANDLE_OFFSETS[slot] }
							: { left: VERTICAL_OFFSETS[slot] }
					}
				/>
			)),
		),
	);
}

// Visual tokens --------------------------------------------------------------

interface Tone {
	kicker: string;
	icon: LucideIcon;
	card: string;
	kickerText: string;
	iconBox: string;
}

const TONES: Record<Exclude<FlowNodeType, "guardrails" | "tools">, Tone> = {
	attacker: {
		kicker: "Red team",
		icon: CrosshairIcon,
		card: "border-red-200 bg-white",
		kickerText: "text-red-600",
		iconBox: "bg-red-50 text-red-600",
	},
	injection: {
		kicker: "Injection point",
		icon: MailWarningIcon,
		card: "border-amber-300 bg-amber-50/60",
		kickerText: "text-amber-700",
		iconBox: "bg-amber-100 text-amber-700",
	},
	user_task: {
		kicker: "User task · fixed",
		icon: MessageSquareQuoteIcon,
		card: "border-slate-200 bg-white",
		kickerText: "text-slate-500",
		iconBox: "bg-slate-100 text-slate-600",
	},
	agent: {
		kicker: "Agent",
		icon: BotIcon,
		card: "border-blue-200 bg-white",
		kickerText: "text-blue-600",
		iconBox: "bg-blue-50 text-blue-600",
	},
	data: {
		kicker: "Data",
		icon: DatabaseIcon,
		card: "border-amber-200 bg-amber-50/40",
		kickerText: "text-amber-700",
		iconBox: "bg-amber-100 text-amber-700",
	},
	success: {
		kicker: "Success check",
		icon: CircleCheckIcon,
		card: "border-dashed border-violet-300 bg-violet-50/50",
		kickerText: "text-violet-600",
		iconBox: "bg-violet-100 text-violet-600",
	},
	output: {
		kicker: "Output",
		icon: ReplyIcon,
		card: "border-slate-200 bg-white",
		kickerText: "text-slate-500",
		iconBox: "bg-slate-100 text-slate-600",
	},
	generic: {
		kicker: "Node",
		icon: BoxIcon,
		card: "border-slate-200 bg-white",
		kickerText: "text-slate-500",
		iconBox: "bg-slate-100 text-slate-600",
	},
};

const HIGHLIGHT_CLASSES: Record<NodeHighlight, string> = {
	visited: "border-blue-400",
	current: "ring-2 ring-blue-500 shadow-lg shadow-blue-500/20",
	blocked: "ring-2 ring-amber-500",
	alert: "ring-2 ring-red-500 shadow-lg shadow-red-500/20",
};

function useNodeState(id: string) {
	const { selectedId, onSelect, highlight } = useGraphContext();
	const state = highlight?.nodes?.[id];
	return {
		selected: selectedId === id,
		select: () => onSelect?.(id),
		highlight: state,
		dimmed: Boolean(highlight?.nodes) && !state,
		annotation: highlight?.annotations?.[id],
	};
}

function stateClasses({
	selected,
	highlight,
	dimmed,
}: {
	selected: boolean;
	highlight?: NodeHighlight;
	dimmed: boolean;
}) {
	return cn(
		highlight && HIGHLIGHT_CLASSES[highlight],
		dimmed && "opacity-45",
		selected && "outline-2 outline-offset-2 outline-blue-500",
	);
}

const cardBase =
	"nodrag nopan relative flex h-full w-full flex-col rounded-xl border text-left shadow-sm transition-[box-shadow,opacity,border-color] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500";
const kickerBase = "text-[10px] font-semibold uppercase tracking-[0.12em]";

// Simple nodes ---------------------------------------------------------------

function SimpleNode({
	node,
	type,
	children,
}: {
	node: GraphNode;
	type: keyof typeof TONES;
	children?: ReactNode;
}) {
	const tone = TONES[type];
	const Icon = tone.icon;
	const state = useNodeState(node.id);
	return (
		<button
			type="button"
			onClick={state.select}
			aria-pressed={state.selected}
			aria-label={`${tone.kicker}: ${node.label}`}
			data-node-id={node.id}
			data-highlight={state.highlight ?? undefined}
			className={cn(
				cardBase,
				"gap-2 px-3.5 py-3",
				tone.card,
				stateClasses(state),
			)}
		>
			<Handles />
			<span className="flex min-w-0 items-start gap-2.5">
				<span
					className={cn(
						"mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg",
						tone.iconBox,
					)}
				>
					<Icon className="size-4" aria-hidden="true" />
				</span>
				<span className="min-w-0 flex-1">
					<span className={cn(kickerBase, "block", tone.kickerText)}>
						{tone.kicker}
					</span>
					<span
						className={cn(
							"block font-semibold leading-snug text-slate-900",
							type === "user_task"
								? "line-clamp-2 text-[13px]"
								: "truncate text-sm",
						)}
					>
						{type === "user_task" ? `“${node.label}”` : node.label}
					</span>
				</span>
				{state.annotation && (
					<span className="shrink-0 font-mono text-[11px] text-slate-500">
						{state.annotation}
					</span>
				)}
			</span>
			{children}
			{node.subtitle && (
				<span className="line-clamp-2 text-xs leading-snug text-slate-500">
					{node.subtitle}
				</span>
			)}
		</button>
	);
}

const makeSimple = (type: keyof typeof TONES) =>
	memo(function KindNode({ data }: NodeProps<FlowNode>) {
		return <SimpleNode node={data.node} type={type} />;
	});

const AgentNode = memo(function AgentNode({ data }: NodeProps<FlowNode>) {
	const model = data.node.details?.model?.model;
	return (
		<SimpleNode node={{ ...data.node, subtitle: null }} type="agent">
			<span className="flex min-w-0 flex-wrap items-center gap-1.5">
				{model && (
					<span className="truncate font-mono text-[11px] text-slate-600">
						{model}
					</span>
				)}
				{data.node.subtitle && data.node.subtitle !== model && (
					<span className="truncate rounded-md border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[11px] text-slate-600">
						{data.node.subtitle}
					</span>
				)}
			</span>
		</SimpleNode>
	);
});

// Group nodes ----------------------------------------------------------------

function MemberRow({
	member,
	icon: Icon,
	tag,
	mono,
}: {
	member: GraphNode;
	icon?: LucideIcon;
	tag?: string;
	mono?: boolean;
}) {
	const state = useNodeState(member.id);
	return (
		<li>
			<button
				type="button"
				onClick={(event) => {
					event.stopPropagation();
					state.select();
				}}
				aria-pressed={state.selected}
				data-node-id={member.id}
				data-highlight={state.highlight ?? undefined}
				className={cn(
					"nodrag nopan flex w-full items-center gap-2 rounded-lg border border-slate-200 bg-white px-2.5 text-left text-[13px] text-slate-800 transition-colors hover:border-slate-300 focus-visible:outline-2 focus-visible:outline-blue-500",
					mono ? "h-[30px] font-mono text-xs" : "h-9",
					state.selected && "border-blue-500 bg-blue-50/70 text-blue-900",
					stateClasses({ ...state, selected: false }),
				)}
			>
				{Icon && (
					<Icon
						className="size-3.5 shrink-0 text-slate-500"
						aria-hidden="true"
					/>
				)}
				<span className="min-w-0 flex-1 truncate">{member.label}</span>
				{(state.annotation ?? tag) && (
					<span
						className={cn(
							"shrink-0 text-[10.5px]",
							state.annotation
								? "font-mono text-slate-600"
								: "font-medium text-slate-400",
						)}
					>
						{state.annotation ?? tag}
					</span>
				)}
			</button>
		</li>
	);
}

function GroupNode({
	node,
	members,
	kicker,
	aside,
	className,
	kickerClass,
	children,
}: {
	node: GraphNode;
	members: GraphNode[];
	kicker: string;
	aside?: string | null;
	className: string;
	kickerClass: string;
	children: ReactNode;
}) {
	const state = useNodeState(node.id);
	const { highlight } = useGraphContext();
	// A group is part of the visited path as soon as one of its members is.
	const memberVisited = members.some((m) => highlight?.nodes?.[m.id]);
	return (
		<div
			data-node-id={node.id}
			data-highlight={state.highlight ?? undefined}
			className={cn(
				"relative flex h-full w-full flex-col rounded-xl border px-2.5 pb-2.5 shadow-sm transition-[box-shadow,opacity]",
				className,
				stateClasses({ ...state, dimmed: state.dimmed && !memberVisited }),
			)}
		>
			<Handles />
			<button
				type="button"
				onClick={state.select}
				aria-pressed={state.selected}
				aria-label={`${kicker}${aside ? ` ${aside}` : ""}`}
				className="nodrag nopan flex h-10 shrink-0 items-center justify-between gap-2 px-1 text-left focus-visible:outline-2 focus-visible:outline-blue-500"
			>
				<span className={cn(kickerBase, kickerClass)}>{kicker}</span>
				{aside && (
					<span className="truncate font-mono text-[11px] text-slate-500">
						{aside}
					</span>
				)}
			</button>
			{children}
		</div>
	);
}

const GuardrailsNode = memo(function GuardrailsNode({
	data,
}: NodeProps<FlowNode>) {
	const position = groupPosition(data.node.id) ?? data.node.subtitle ?? "";
	return (
		<GroupNode
			node={data.node}
			members={data.members}
			kicker="Guardrails"
			aside={formatPosition(position)}
			className="border-emerald-200 bg-emerald-50/60"
			kickerClass="text-emerald-700"
		>
			<ul
				className="flex flex-col gap-1.5"
				aria-label={`Guardrails at ${position}`}
			>
				{data.members.map((member) => (
					<MemberRow
						key={member.id}
						member={member}
						icon={member.details?.model ? ShieldCheckIcon : ListFilterIcon}
						tag={member.details?.model ? "LLM" : "rule"}
					/>
				))}
			</ul>
		</GroupNode>
	);
});

const ToolsNode = memo(function ToolsNode({ data }: NodeProps<FlowNode>) {
	return (
		<GroupNode
			node={data.node}
			members={data.members}
			kicker="Tools"
			aside={data.node.subtitle}
			className="border-slate-200 bg-slate-50/80"
			kickerClass="text-slate-500"
		>
			<ul className="flex flex-col gap-1" aria-label="Tools">
				{data.members.map((member) => (
					<MemberRow key={member.id} member={member} icon={WrenchIcon} mono />
				))}
			</ul>
		</GroupNode>
	);
});

export const nodeTypes: NodeTypes = {
	[flowTypeName("attacker")]: makeSimple("attacker"),
	[flowTypeName("injection")]: makeSimple("injection"),
	[flowTypeName("user_task")]: makeSimple("user_task"),
	[flowTypeName("agent")]: AgentNode,
	[flowTypeName("data")]: makeSimple("data"),
	[flowTypeName("success")]: makeSimple("success"),
	[flowTypeName("output")]: makeSimple("output"),
	[flowTypeName("generic")]: makeSimple("generic"),
	[flowTypeName("guardrails")]: GuardrailsNode,
	[flowTypeName("tools")]: ToolsNode,
};
