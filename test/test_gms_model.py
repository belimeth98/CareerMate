"""Provider/model configuration contracts. No network or real credentials."""

import os
import unittest
from unittest.mock import patch

from backend.app.integrations import gms


class GmsConfigurationTests(unittest.TestCase):
    def build(self, **environment):
        with patch.dict(os.environ, {"GMS_API_KEY": "unit-test-placeholder", **environment}, clear=True):
            with patch.object(gms, "ChatOpenAI") as model:
                gms.build_gms_model()
                return model.call_args.kwargs

    def test_default_luna_uses_openai_chat_with_no_reasoning(self):
        options = self.build()
        self.assertEqual(options["model"], "gpt-6-luna")
        self.assertEqual(options["base_url"], gms.GMS_BASE_URL)
        self.assertEqual(options["reasoning_effort"], "none")
        self.assertIs(options["use_responses_api"], False)

    def test_explicit_luna_still_sets_function_calling_compatible_effort(self):
        self.assertEqual(self.build(GMS_MODEL="gpt-6-luna")["reasoning_effort"], "none")

    def test_claude_uses_anthropic_default_without_openai_reasoning_argument(self):
        options = self.build(GMS_MODEL="claude-haiku-5-5")
        self.assertEqual(options["base_url"], gms.GMS_ANTHROPIC_BASE_URL)
        self.assertNotIn("reasoning_effort", options)

    def test_explicit_base_url_is_respected(self):
        options = self.build(GMS_BASE_URL="https://gms.ssafy.io/custom/v1")
        self.assertEqual(options["base_url"], "https://gms.ssafy.io/custom/v1")

    def test_blank_overrides_use_default_model_and_matching_route(self):
        options = self.build(GMS_MODEL="", GMS_BASE_URL="")
        self.assertEqual(options["model"], "gpt-6-luna")
        self.assertEqual(options["base_url"], gms.GMS_BASE_URL)

    def test_missing_key_is_explicit(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError):
            gms.build_gms_model()


if __name__ == "__main__":
    unittest.main()
