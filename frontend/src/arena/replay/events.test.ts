import { describe, expect, it } from "vitest";
import {
	emailReviewCase03,
	hiddenMailboxCase01,
	hiddenMailboxCase03,
	hiddenMailboxCase07,
} from "../fixtures/runs";
import { eventSummary } from "./EventLog";
import { eventHeading, highlightAt } from "./events";

describe("highlightAt", () => {
	it("marks the path up to the current event", () => {
		const h = highlightAt(hiddenMailboxCase03.events, 2);
		expect(h.nodes).toEqual({
			attacker: "visited",
			"guardrail:input-sanitizer": "visited",
			"guardrail:input-judge": "current",
		});
		expect(h.edges).toEqual({ "attacker->guardrails:user->agent": "current" });
		expect(h.annotations).toEqual({
			attacker: "0.00s",
			"guardrail:input-sanitizer": "no changes",
			"guardrail:input-judge": "SAFE · passed",
		});
	});

	it("keeps a blocking guardrail blocked and a met success check as alert", () => {
		const blocked = highlightAt(hiddenMailboxCase01.events, 4);
		expect(blocked.nodes?.["guardrail:input-judge"]).toBe("blocked");
		expect(blocked.annotations?.["guardrail:input-judge"]).toBe(
			"UNSAFE · blocked",
		);
		expect(blocked.nodes?.success).toBe("current");
		expect(blocked.annotations?.success).toBe("not met");

		const events = hiddenMailboxCase03.events;
		const met = highlightAt(events, events.length - 1);
		expect(met.nodes?.success).toBe("alert");
		expect(met.annotations?.success).toBe("met");
		expect(met.annotations?.["tool:get_file_content"]).toBe("2.17s");
		expect(met.nodes?.agent).toBe("visited");
		expect(met.edges?.["output->success"]).toBe("current");
	});

	it("shows only the current turn of a multi-turn case", () => {
		const events = hiddenMailboxCase07.events;
		const turn3 = events.findIndex((e) => e.turn === 3);
		const h = highlightAt(events, turn3);
		expect(h.nodes).toEqual({ attacker: "current" });
		expect(h.annotations).toEqual({ attacker: "14.10s" });
	});

	it("tolerates an index out of range", () => {
		expect(highlightAt([], 0)).toEqual({
			nodes: {},
			edges: {},
			annotations: {},
		});
	});
});

describe("event text", () => {
	it("names events by team and guardrail label", () => {
		const [injection, userTask] = emailReviewCase03.events;
		expect(eventHeading(injection, "red").title).toBe(
			"Attacker · planted content",
		);
		expect(eventHeading(injection, "green").title).toBe(
			"Planted content (benign)",
		);
		expect(eventHeading(userTask, "red").title).toBe("User task");
		const auth = emailReviewCase03.events.find(
			(e) => e.node === "guardrail:send-email-auth",
		);
		expect(
			auth && eventHeading(auth, "red", emailReviewCase03.graph).title,
		).toBe("Send-email auth · passed");
		expect(auth && eventHeading(auth, "red").title).toBe(
			"send-email-auth · passed",
		);
	});

	it("summarizes recorded content on one line", () => {
		const [injection] = emailReviewCase03.events;
		expect(eventSummary(injection)).toBe(
			"<planted email subject 3> · <planted email body 3>",
		);
		const judge = hiddenMailboxCase01.events[2];
		expect(eventSummary(judge)).toBe(
			"<rule as returned by the judge> · <judge reason as returned: request targets restricted data>",
		);
	});
});
