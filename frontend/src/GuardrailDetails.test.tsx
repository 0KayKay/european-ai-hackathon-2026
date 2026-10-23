import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { GuardrailDetails, SubmissionActivity } from "./GuardrailDetails";

afterEach(cleanup);
it("shows the affected rule and reason for a blocked tool check", () => {
	render(
		<GuardrailDetails
			checks={[
				{
					id: "check",
					name: "SendEmailGuardrail",
					status: "blocked",
					stage: "tool",
					rule: "Trusted user authorization",
					reason: "The recipient came from instructions inside the email.",
				},
			]}
		/>,
	);
	expect(screen.getByText("Email authorization")).toBeVisible();
	expect(screen.getByText("Trusted user authorization")).toBeVisible();
	expect(
		screen.getByText("The recipient came from instructions inside the email."),
	).toBeVisible();
	expect(screen.getByText("tool · Blocked")).toBeVisible();
});
it("shows an animated submission indicator with the current activity", () => {
	render(<SubmissionActivity label="Comparing benchmark prices" />);
	expect(
		screen.getByRole("status", { name: "Submission activity" }),
	).toHaveTextContent("Comparing benchmark prices");
});
