import "@testing-library/jest-dom/vitest";
import "./i18n";

globalThis.ResizeObserver = class {
	observe() {}
	unobserve() {}
	disconnect() {}
};

HTMLElement.prototype.scrollTo = () => {};
