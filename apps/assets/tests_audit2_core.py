"""audit2_core probes for assets, over the API."""
from .models import AssetStatus
from .tests_api import ApiTestCase


class DisposalInputTests(ApiTestCase):
    def test_proceeds_that_are_not_a_sum_are_refused_in_words(self):
        asset = self.asset()
        answers = {}
        for bad in ("NaN", "Infinity", "-5"):
            answers[bad] = self.post(f"assets/{asset.pk}/dispose/",
                                     {"on_date": "2026-04-15", "proceeds": bad}).status_code
        print("DISPOSE ANSWERS", answers)
        self.assertEqual(answers, {"NaN": 400, "Infinity": 400, "-5": 400})
        asset.refresh_from_db()
        self.assertEqual(asset.status, AssetStatus.IN_SERVICE)
