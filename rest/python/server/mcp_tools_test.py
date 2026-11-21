#   Copyright 2026 UCP Authors
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

"""Unit tests for the MCP ``tools/list`` built from the OpenRPC document."""

import json
import unittest

import config
from routes import mcp


def _load_openrpc() -> dict:
  with mcp._DEFAULT_OPENRPC.open(encoding="utf-8") as f:
    return json.load(f)


class McpToolsTest(unittest.TestCase):
  """Tests build_tools against the vendored shopping OpenRPC."""

  def setUp(self) -> None:
    """Build the tool list once per test."""
    super().setUp()
    self.openrpc = _load_openrpc()
    self.tools = {
      t["name"]: t
      for t in mcp.build_tools(
        self.openrpc, set(config.get_profile_capabilities())
      )
    }

  def test_tools_come_from_openrpc_not_a_hardcoded_list(self) -> None:
    """Tools are built from the OpenRPC document."""
    self.assertFalse(hasattr(mcp, "_TOOLS"))
    self.assertIn("update_cart", self.tools)  # absent from the old list

  def test_only_tools_for_advertised_capabilities_are_listed(self) -> None:
    """Tools of unadvertised capabilities are not listed."""
    self.assertNotIn("search_catalog", self.tools)
    self.assertNotIn("get_product", self.tools)

  def test_every_tool_has_annotations_copied_from_openrpc(self) -> None:
    """Each tool publishes its OpenRPC annotations verbatim."""
    by_method = {m["name"]: m for m in self.openrpc["methods"]}
    for name, tool in self.tools.items():
      with self.subTest(tool=name):
        self.assertEqual(
          tool["annotations"], by_method[name]["x-ucp-mcp-annotations"]
        )

  def test_complete_checkout_is_not_read_only_or_idempotent(self) -> None:
    """complete_checkout is a non-idempotent, destructive commit."""
    ann = self.tools["complete_checkout"]["annotations"]
    self.assertFalse(ann["readOnlyHint"])
    self.assertFalse(ann["idempotentHint"])
    self.assertTrue(ann["destructiveHint"])
    self.assertTrue(ann["openWorldHint"])

  def test_cancel_is_destructive_and_idempotent(self) -> None:
    """A repeated cancel leaves the session canceled: no additional effect."""
    ann = self.tools["cancel_checkout"]["annotations"]
    self.assertTrue(ann["destructiveHint"])
    self.assertTrue(ann["idempotentHint"])

  def test_get_tools_are_read_only(self) -> None:
    """Get tools are read-only."""
    for name in ("get_checkout", "get_cart", "get_order"):
      with self.subTest(tool=name):
        self.assertTrue(self.tools[name]["annotations"]["readOnlyHint"])

  def test_input_schema_requires_openrpc_required_params(self) -> None:
    """The input schema mirrors the OpenRPC required params."""
    schema = self.tools["complete_checkout"]["inputSchema"]
    self.assertEqual(schema["type"], "object")
    self.assertEqual(set(schema["required"]), {"meta", "id", "checkout"})
    # No dangling references to files this sample does not serve.
    self.assertNotIn("../", json.dumps(schema))


if __name__ == "__main__":
  unittest.main()
