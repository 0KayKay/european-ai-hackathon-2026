// Group across the whole message, retaining order within each block.
export function groupMessageParts(parts: readonly { type: string }[]) {
	const groups = new Map<string | number, number[]>();
	parts.forEach((part, index) => {
		const key =
			part.type === "reasoning"
				? "reasoning"
				: part.type === "tool-call"
					? "tool"
					: index;
		const indices = groups.get(key) ?? [];
		indices.push(index);
		groups.set(key, indices);
	});
	return Array.from(groups, ([key, indices]) => ({
		groupKey: typeof key === "string" ? key : undefined,
		indices,
	}));
}
