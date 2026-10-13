from deepteam.attacks.attack_engine import AttackEngine
from deepteam.test_case import RTTestCase


class NoRefinementAttackEngine(AttackEngine):
    def refine(
        self,
        test_cases: list[RTTestCase],
        purpose: str | None = None,
    ) -> list[RTTestCase]:
        return test_cases

    async def a_refine(
        self,
        test_cases: list[RTTestCase],
        purpose: str | None = None,
    ) -> list[RTTestCase]:
        return test_cases
