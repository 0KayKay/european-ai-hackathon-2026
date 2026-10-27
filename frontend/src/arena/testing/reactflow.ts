/**
 * jsdom shims for React Flow (see https://reactflow.dev/learn/advanced-use/testing): nodes are only
 * shown once React Flow has measured them, which needs ResizeObserver, DOMMatrixReadOnly and
 * element sizes. Call `mockReactFlow()` at the top of a test file that renders the graph.
 */
class ResizeObserverShim {
	private readonly callback: ResizeObserverCallback;

	constructor(callback: ResizeObserverCallback) {
		this.callback = callback;
	}

	observe(target: Element) {
		const { offsetWidth: width = 1, offsetHeight: height = 1 } =
			target as HTMLElement;
		const contentRect = { x: 0, y: 0, width, height } as DOMRectReadOnly;
		this.callback(
			[{ target, contentRect } as unknown as ResizeObserverEntry],
			this as unknown as ResizeObserver,
		);
	}

	unobserve() {}

	disconnect() {}
}

class DOMMatrixReadOnlyShim {
	m22: number;

	constructor(transform?: string) {
		const scale = transform?.match(/scale\(([0-9.]+)\)/)?.[1];
		this.m22 = scale !== undefined ? Number(scale) : 1;
	}
}

let installed = false;

export function mockReactFlow() {
	if (installed) return;
	installed = true;
	const g = globalThis as Record<string, unknown>;
	g.ResizeObserver = ResizeObserverShim;
	g.DOMMatrixReadOnly = DOMMatrixReadOnlyShim;
	Object.defineProperties(HTMLElement.prototype, {
		offsetHeight: {
			configurable: true,
			get(this: HTMLElement) {
				return Number.parseFloat(this.style.height) || 1;
			},
		},
		offsetWidth: {
			configurable: true,
			get(this: HTMLElement) {
				return Number.parseFloat(this.style.width) || 1;
			},
		},
	});
	(SVGElement.prototype as unknown as { getBBox: () => DOMRect }).getBBox =
		() => ({ x: 0, y: 0, width: 0, height: 0 }) as DOMRect;
}
