import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
	within,
} from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import {
	cloneLevel,
	hiddenMailboxHard,
	levelsFixture,
	moveGuardrail,
} from "../fixtures";
import { LevelList } from "../LevelList";
import { mockArenaFetch, requests } from "../testing/fetch";
import { mockReactFlow } from "../testing/reactflow";
import type { LevelDetail } from "../types";
import { LEAVE_CONFIRM, LevelView } from "./LevelView";
import { DISCARD_CONFIRM, RESET_CONFIRM } from "./useSourceEditor";

beforeAll(mockReactFlow);
afterEach(() => {
	cleanup();
	vi.restoreAllMocks();
	window.location.hash = "";
});

const LEVEL_URL = "/api/v1/arena/levels/hidden-mailbox/hard";
const SOURCE_URL = "/api/v1/arena/levels/hidden-mailbox/source";
const LEVEL_YAML = "hidden-mailbox/level.yaml";
const INPUT_JUDGE = "hidden-mailbox/prompts/input_judge.md";

/** The fixture as served by a backend with editing: version and per-file flags. */
function served(version = 0): LevelDetail {
	const level = cloneLevel(hiddenMailboxHard);
	level.version = version;
	level.modified = false;
	level.override_error = null;
	for (const file of level.source.files) {
		file.modified = false;
		file.editable = file.path.startsWith("hidden-mailbox/");
	}
	return level;
}

const yamlOf = (level: LevelDetail) =>
	level.source.files.find((f) => f.path === LEVEL_YAML)?.content ?? "";

/** The saved level: input judge moved to the output, level.yaml modified, version 1. */
function savedWithMovedJudge(content: string): LevelDetail {
	const level = served(1);
	level.modified = true;
	level.graph = moveGuardrail(level.graph, "input-judge", "agent->output");
	const file = level.source.files.find((f) => f.path === LEVEL_YAML);
	if (file) {
		file.content = content;
		file.modified = true;
	}
	return level;
}

const json = (status: number, body: unknown) =>
	new Response(JSON.stringify(body), { status });

const flowNode = (id: string) =>
	document.querySelector<HTMLElement>(
		`.react-flow__node[data-id="${CSS.escape(id)}"]`,
	);

const configPane = () => screen.getByRole("region", { name: "Config source" });

async function openEditor(routes: Record<string, unknown> = {}) {
	window.location.hash = "#/arena/levels/hidden-mailbox/hard";
	const fetch = mockArenaFetch({ [LEVEL_URL]: served(), ...routes });
	render(<LevelView level="hidden-mailbox" difficulty="hard" />);
	fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
	const editor = screen.getByRole("textbox", { name: `Edit ${LEVEL_YAML}` });
	return { fetch, editor: editor as HTMLTextAreaElement };
}

describe("editing level files", () => {
	it("shows the level's own files as editable text and shared files read-only", async () => {
		const { editor } = await openEditor();
		expect(editor.value).toBe(yamlOf(served()));
		expect(within(configPane()).getByText("editing")).toBeInTheDocument();
		const tabs = within(screen.getByRole("tablist", { name: "Config files" }));
		fireEvent.click(tabs.getByRole("tab", { name: /judge_system\.md/ }));
		expect(
			screen.queryByRole("textbox", { name: /judge_system/ }),
		).not.toBeInTheDocument();
		expect(within(configPane()).getByText("read-only")).toBeInTheDocument();
		fireEvent.click(tabs.getByRole("tab", { name: /input_judge\.md/ }));
		expect(
			screen.getByRole("textbox", { name: `Edit ${INPUT_JUDGE}` }),
		).toBeInTheDocument();
	});

	it("saves the changed files and renders the graph from the response", async () => {
		const original = yamlOf(served());
		const edited = original.replace(
			'at: "user->agent"\n        description: Blocks',
			'at: "agent->output"\n        description: Blocks',
		);
		const { fetch, editor } = await openEditor({
			[SOURCE_URL]: () => savedWithMovedJudge(edited),
		});
		expect(
			within(flowNode("guardrails:user->agent")!).getByText("Input judge"),
		).toBeInTheDocument();

		const save = screen.getByRole("button", { name: "Save" });
		expect(save).toBeDisabled();
		fireEvent.change(editor, { target: { value: edited } });
		expect(screen.getByText("1 changed file")).toBeInTheDocument();
		fireEvent.click(save);

		expect(await screen.findByText("Saved · version 1")).toBeInTheDocument();
		const put = requests(fetch).find(([method]) => method === "PUT");
		expect(put).toEqual([
			"PUT",
			SOURCE_URL,
			{
				files: [{ path: LEVEL_YAML, content: edited }],
				base_version: 0,
				difficulty: "hard",
			},
		]);
		// Edit mode is left; graph, badges and the source come from the response.
		expect(
			screen.queryByRole("textbox", { name: `Edit ${LEVEL_YAML}` }),
		).not.toBeInTheDocument();
		await waitFor(() =>
			expect(
				within(flowNode("guardrails:agent->output")!).getByText("Input judge"),
			).toBeInTheDocument(),
		);
		const tab = screen.getByRole("tab", { name: /level\.yaml/ });
		expect(tab).toHaveTextContent("Modified");
		expect(screen.getByRole("tabpanel")).toHaveTextContent(
			'at: "agent->output"',
		);
		expect(
			screen.getByRole("button", { name: "Reset to repository version" }),
		).toBeInTheDocument();
	});

	it("shows validation errors inline with the file and line", async () => {
		const { editor } = await openEditor({
			[SOURCE_URL]: () =>
				json(422, {
					detail: "The level config is invalid; nothing was saved.",
					errors: [
						{
							path: INPUT_JUDGE,
							message: "unknown placeholder {{nope}}",
							line: 2,
						},
					],
				}),
		});
		fireEvent.change(editor, { target: { value: `${editor.value}# x\n` } });
		fireEvent.click(screen.getByRole("button", { name: "Save" }));

		const alert = await screen.findByRole("alert", {
			name: "Validation errors",
		});
		expect(alert).toHaveTextContent(
			`${INPUT_JUDGE}:2 unknown placeholder {{nope}}`,
		);
		// The file with the problem opens, its line is marked, and its tab shows the error.
		const judge = await screen.findByRole("textbox", {
			name: `Edit ${INPUT_JUDGE}`,
		});
		expect(judge).toHaveAttribute("aria-invalid", "true");
		expect(
			screen.getByRole("tabpanel").querySelector("[data-error-line]")
				?.textContent,
		).toBe("2");
		expect(
			within(screen.getByRole("tab", { name: /input_judge/ })).getByRole(
				"img",
				{ name: "has errors" },
			),
		).toBeInTheDocument();
		// The edits are kept for fixing.
		fireEvent.click(screen.getByRole("tab", { name: /level\.yaml/ }));
		expect(
			(
				screen.getByRole("textbox", {
					name: `Edit ${LEVEL_YAML}`,
				}) as HTMLTextAreaElement
			).value,
		).toContain("# x");
	});

	it("offers to load the other version on a conflict", async () => {
		const theirs = savedWithMovedJudge("id: hidden-mailbox\n# their version\n");
		const { editor } = await openEditor({
			[SOURCE_URL]: () =>
				json(409, {
					detail: "Someone else saved this level in the meantime.",
					version: 1,
					current: theirs,
				}),
		});
		fireEvent.change(editor, { target: { value: "id: hidden-mailbox\n" } });
		fireEvent.click(screen.getByRole("button", { name: "Save" }));

		const banner = await screen.findByRole("status", { name: "Save conflict" });
		expect(banner).toHaveTextContent("Someone else saved this level");
		expect(banner).toHaveTextContent("version 1");
		fireEvent.click(
			within(banner).getByRole("button", { name: "Load their version" }),
		);
		const reloaded = screen.getByRole("textbox", {
			name: `Edit ${LEVEL_YAML}`,
		}) as HTMLTextAreaElement;
		expect(reloaded.value).toBe("id: hidden-mailbox\n# their version\n");
		expect(
			screen.queryByRole("status", { name: "Save conflict" }),
		).not.toBeInTheDocument();
		await waitFor(() =>
			expect(
				within(flowNode("guardrails:agent->output")!).getByText("Input judge"),
			).toBeInTheDocument(),
		);
	});

	it("asks before discarding changes on cancel", async () => {
		const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
		const { editor } = await openEditor();
		fireEvent.change(editor, { target: { value: "changed" } });

		fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
		expect(confirm).toHaveBeenCalledWith(DISCARD_CONFIRM);
		expect(editor).toBeInTheDocument();

		confirm.mockReturnValue(true);
		fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
		expect(
			screen.queryByRole("textbox", { name: `Edit ${LEVEL_YAML}` }),
		).not.toBeInTheDocument();
		expect(screen.getByRole("tabpanel")).toHaveTextContent(
			"id: hidden-mailbox",
		);
	});

	it("cancels without asking when nothing changed", async () => {
		const confirm = vi.spyOn(window, "confirm");
		await openEditor();
		fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
		expect(confirm).not.toHaveBeenCalled();
	});

	it("adds a new file to the save", async () => {
		const { fetch, editor } = await openEditor({
			[SOURCE_URL]: () => savedWithMovedJudge(yamlOf(served())),
		});
		fireEvent.change(editor, {
			target: { value: editor.value.replace("input_judge.md", "strict.md") },
		});
		fireEvent.click(screen.getByRole("button", { name: "New file" }));
		fireEvent.change(screen.getByRole("textbox", { name: "New file path" }), {
			target: { value: "prompts/strict.md" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Add" }));
		const created = screen.getByRole("textbox", {
			name: "Edit hidden-mailbox/prompts/strict.md",
		});
		fireEvent.change(created, { target: { value: "Check {{content}}.\n" } });
		fireEvent.click(screen.getByRole("button", { name: "Save" }));

		await screen.findByText("Saved · version 1");
		const [, , body] = requests(fetch).find(([m]) => m === "PUT") ?? [];
		expect(
			(body as { files: { path: string }[] }).files.map((f) => f.path),
		).toEqual([LEVEL_YAML, "hidden-mailbox/prompts/strict.md"]);
	});

	it("resets to the repository version after confirmation", async () => {
		const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
		window.location.hash = "#/arena/levels/hidden-mailbox/hard";
		const modified = savedWithMovedJudge("id: hidden-mailbox\n");
		const fetch = mockArenaFetch({
			[LEVEL_URL]: modified,
			[`${SOURCE_URL}?difficulty=hard&base_version=1`]: () => served(2),
		});
		render(<LevelView level="hidden-mailbox" difficulty="hard" />);
		await screen.findByRole("region", { name: "Config source" });
		// The level and its level.yaml carry the badge.
		expect(within(configPane()).getAllByText("Modified")).toHaveLength(2);

		fireEvent.click(
			screen.getByRole("button", { name: "Reset to repository version" }),
		);
		expect(confirm).toHaveBeenCalledWith(RESET_CONFIRM);
		expect(
			await screen.findByText("Reset to the repository version"),
		).toBeInTheDocument();
		expect(requests(fetch).map(([method, url]) => `${method} ${url}`)).toEqual([
			`GET ${LEVEL_URL}`,
			`DELETE ${SOURCE_URL}?difficulty=hard&base_version=1`,
		]);
		expect(within(configPane()).queryByText("Modified")).toBeNull();
		await waitFor(() =>
			expect(
				within(flowNode("guardrails:user->agent")!).getByText("Input judge"),
			).toBeInTheDocument(),
		);
		expect(
			screen.queryByRole("button", { name: "Reset to repository version" }),
		).not.toBeInTheDocument();
	});

	it("does not reset without confirmation", async () => {
		vi.spyOn(window, "confirm").mockReturnValue(false);
		window.location.hash = "#/arena/levels/hidden-mailbox/hard";
		const fetch = mockArenaFetch({
			[LEVEL_URL]: savedWithMovedJudge("id: hidden-mailbox\n"),
		});
		render(<LevelView level="hidden-mailbox" difficulty="hard" />);
		fireEvent.click(
			await screen.findByRole("button", {
				name: "Reset to repository version",
			}),
		);
		expect(requests(fetch).map(([method]) => method)).toEqual(["GET"]);
	});

	it("shows saved edits that no longer load and lets them be fixed", async () => {
		window.location.hash = "#/arena/levels/hidden-mailbox/hard";
		const level = served(3);
		level.override_error = {
			message: "levels/hidden-mailbox/level.yaml: [easy] retired: Extra inputs",
			errors: [
				{
					path: LEVEL_YAML,
					message: "[easy] retired: Extra inputs are not permitted",
					line: 3,
				},
			],
			files: [
				{ path: LEVEL_YAML, content: "id: hidden-mailbox\nretired: 1\n" },
			],
		};
		mockArenaFetch({ [LEVEL_URL]: level });
		render(<LevelView level="hidden-mailbox" difficulty="hard" />);
		const banner = await screen.findByRole("alert", {
			name: "Saved edits do not load",
		});
		expect(banner).toHaveTextContent(
			`${LEVEL_YAML}:3 · [easy] retired: Extra inputs are not permitted`,
		);
		fireEvent.click(
			within(banner).getByRole("button", { name: "Fix the saved edits" }),
		);
		const editor = screen.getByRole("textbox", {
			name: `Edit ${LEVEL_YAML}`,
		}) as HTMLTextAreaElement;
		expect(editor.value).toBe("id: hidden-mailbox\nretired: 1\n");
		expect(screen.getByText("1 changed file")).toBeInTheDocument();
	});

	it("warns before navigating away with unsaved changes", async () => {
		const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
		const { editor } = await openEditor();
		fireEvent.change(editor, { target: { value: "changed" } });

		window.location.hash = "#/arena";
		await waitFor(() => expect(confirm).toHaveBeenCalledWith(LEAVE_CONFIRM));
		expect(window.location.hash).toBe("#/arena/levels/hidden-mailbox/hard");
		expect(editor.value).toBe("changed");

		const unload = new Event("beforeunload", { cancelable: true });
		window.dispatchEvent(unload);
		expect(unload.defaultPrevented).toBe(true);
	});
});

describe("level list", () => {
	it("marks levels with saved edits", async () => {
		mockArenaFetch({
			"/api/v1/arena/levels": [
				{ ...levelsFixture[0], version: 2, modified: true },
				{ ...levelsFixture[1], version: 0, modified: false },
			],
		});
		render(<LevelList />);
		const cards = within(
			await screen.findByRole("list", { name: "Levels" }),
		).getAllByRole("listitem");
		expect(within(cards[0]).getByText("Modified")).toBeInTheDocument();
		expect(within(cards[1]).queryByText("Modified")).toBeNull();
	});
});
