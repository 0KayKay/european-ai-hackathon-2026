import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";

// findBy*/waitFor default to 1 s, which fails on loaded CI runners.
configure({ asyncUtilTimeout: 5000 });

globalThis.ResizeObserver = class {
	observe() {}
	unobserve() {}
	disconnect() {}
};

HTMLElement.prototype.scrollTo = () => {};
