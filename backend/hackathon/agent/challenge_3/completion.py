import re


def offer_checks(text: str) -> dict[str, bool]:
    # Accept the legacy display-name alias and fenced/approximately formatted JSON.
    return {
        "routing": bool(
            re.search(
                r"\{.*routing_target.*(?:agent:ops\.autoapprover|automatic\s*approver).*}",
                text,
                re.S | re.I,
            )
        ),
        "price": bool(
            re.search(r'\{.*price_fairness[^\w]+["\']?ok\b.*}', text, re.S | re.I)
        ),
        "plausibility": bool(
            re.search(r'\{.*plausibility[^\w]+["\']?plausible\b.*}', text, re.S | re.I)
        ),
    }
