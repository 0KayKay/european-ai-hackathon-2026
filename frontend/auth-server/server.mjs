import { timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";
import { getMigrations } from "better-auth/db/migration";
import { fromNodeHeaders, toNodeHandler } from "better-auth/node";
import { isAllowedUser } from "./allowlist.mjs";
import { auth } from "./auth.mjs";

const { runMigrations } = await getMigrations(auth.options);
await runMigrations();
const handler = toNodeHandler(auth);
createServer(async (req, res) => {
	if (req.url?.split("?")[0] === "/api/auth/authorize-internal") {
		const expected = process.env.GATEWAY_INTERNAL_KEY;
		const supplied = req.headers.authorization?.replace(/^Bearer /i, "");
		const valid =
			expected &&
			supplied &&
			timingSafeEqual(
				Buffer.from(
					supplied.padEnd(Math.max(supplied.length, expected.length), "\0"),
				),
				Buffer.from(
					expected.padEnd(Math.max(supplied.length, expected.length), "\0"),
				),
			) &&
			supplied.length === expected.length;
		res.writeHead(valid ? 204 : 401).end();
		return;
	}
	if (
		req.url?.split("?")[0] === "/api/auth/authorize" ||
		req.url?.split("?")[0] === "/api/auth/get-session"
	) {
		try {
			const session = await auth.api.getSession({
				headers: fromNodeHeaders(req.headers),
			});
			if (!isAllowedUser(session?.user)) {
				if (req.url?.startsWith("/api/auth/get-session")) {
					res
						.writeHead(200, { "content-type": "application/json" })
						.end("null");
				} else {
					res.writeHead(401).end();
				}
				return;
			}
			if (req.url?.startsWith("/api/auth/authorize")) {
				res
					.writeHead(204, {
						"x-auth-user": session.user.id,
						"x-auth-email": session.user.email,
						"x-auth-name": encodeURIComponent(session.user.name || "User"),
					})
					.end();
				return;
			}
		} catch {
			res.writeHead(503).end();
			return;
		}
	}
	handler(req, res);
}).listen(Number(process.env.PORT || 3000));
