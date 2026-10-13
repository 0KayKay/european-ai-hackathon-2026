const allowed = (process.env.ALLOWED_EMAILS || "")
	.split(",")
	.map((entry) => entry.trim().toLowerCase())
	.filter(Boolean);

export function isAllowedEmail(email) {
	if (typeof email !== "string") return false;
	const normalized = email.toLowerCase();
	const at = normalized.indexOf("@");
	if (
		at < 1 ||
		at !== normalized.lastIndexOf("@") ||
		at === normalized.length - 1
	)
		return false;
	return allowed.some((entry) =>
		entry.startsWith("*@")
			? normalized.slice(at + 1) === entry.slice(2)
			: normalized === entry,
	);
}

export function isAllowedUser(user) {
	return user?.emailVerified === true && isAllowedEmail(user.email);
}
