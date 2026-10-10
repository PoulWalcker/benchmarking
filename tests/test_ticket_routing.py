"""Task-owned rubric expectations for independently verified ticket routing."""

import json
import unittest

from sapi_config_lab.paths import workspace_root
from verification.rubric import Criterion, RubricCard


class TicketRoutingTests(unittest.TestCase):
    def test_versioned_rubric_card_is_present(self):
        path = workspace_root() / "tasks/ticket-routing/evaluation/rubric.json"
        self.assertTrue(path.is_file())
        document = json.loads(path.read_text())
        card = RubricCard(**{**document, "criteria": tuple(Criterion(**item) for item in document["criteria"])})
        self.assertEqual((card.id, card.version, card.origin), ("ticket-routing", "1.0.0", "local"))
