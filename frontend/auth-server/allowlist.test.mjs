import assert from "node:assert/strict";
import { test } from "node:test";

process.env.ALLOWED_EMAILS =
	"*@codify.ch,*@lapiscode.com,*@calenora.com,person@example.com";
const { isAllowedEmail, isAllowedUser } = await import("./allowlist.mjs");

test("accepts exact addresses and the configured domains", () => {
	assert.equal(isAllowedEmail("PERSON@example.com"), true);
	assert.equal(isAllowedEmail("Ada@Codify.ch"), true);
	assert.equal(isAllowedEmail("dev@lapiscode.com"), true);
	assert.equal(isAllowedEmail("team@calenora.com"), true);
});

test("rejects suffix tricks and missing identities", () => {
	for (const email of [
		null,
		"",
		"@codify.ch",
		"a@evilcodify.ch",
		"a@codify.ch.evil",
		"a@@codify.ch",
		"else@example.com",
	]) {
		assert.equal(isAllowedEmail(email), false, String(email));
	}
});

test("OAuth admission requires a verified allowlisted email", () => {
	assert.equal(
		isAllowedUser({ email: "dev@codify.ch", emailVerified: true }),
		true,
	);
	assert.equal(
		isAllowedUser({ email: "dev@codify.ch", emailVerified: false }),
		false,
	);
	assert.equal(
		isAllowedUser({ email: "dev@other.com", emailVerified: true }),
		false,
	);
});
