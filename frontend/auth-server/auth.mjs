import { betterAuth } from "better-auth";
import { Pool } from "pg";
import { isAllowedUser } from "./allowlist.mjs";

for (const name of [
	"BETTER_AUTH_URL",
	"BETTER_AUTH_SECRET",
	"GITHUB_CLIENT_ID",
	"GITHUB_CLIENT_SECRET",
	"DB_USER",
	"DB_PASSWORD",
	"DB_NAME",
]) {
	if (!process.env[name]) throw new Error(`${name} is required`);
}

export const auth = betterAuth({
	baseURL: process.env.BETTER_AUTH_URL,
	secret: process.env.BETTER_AUTH_SECRET,
	trustedOrigins: (process.env.BETTER_AUTH_TRUSTED_ORIGINS || "")
		.split(",")
		.filter(Boolean),
	database: new Pool({
		host: process.env.DB_HOST || "localhost",
		port: Number(process.env.DB_PORT || 5432),
		user: process.env.DB_USER,
		password: process.env.DB_PASSWORD,
		database: process.env.DB_NAME,
	}),
	user: {
		validateUserInfo: ({ user }) =>
			isAllowedUser(user) ? undefined : { error: "email_not_allowed" },
	},
	socialProviders: {
		github: {
			clientId: process.env.GITHUB_CLIENT_ID,
			clientSecret: process.env.GITHUB_CLIENT_SECRET,
		},
	},
});
