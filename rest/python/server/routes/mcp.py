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

"""Minimal MCP (JSON-RPC 2.0) transport for the UCP shopping service.

The discovery profile advertises an ``mcp`` transport at ``<endpoint>/mcp``, but
the server otherwise only implements REST, so spec-conformant MCP clients fail
discovery with an HTTP 404 on that endpoint.

This module adds the minimum needed for capability discovery to succeed over
MCP: it answers ``initialize`` and ``tools/list`` (advertising the shopping
methods from the UCP shopping OpenRPC, with their tool annotations).
Executing tools (``tools/call``) by
bridging to the REST checkout handlers is intentionally left as a follow-up.
"""

import functools
import json
import os
import pathlib
from typing import Any

import config
import dependencies
from fastapi import APIRouter
from fastapi import Depends
from fastapi import Request
from fastapi import Response
from fastapi.responses import JSONResponse

router = APIRouter()

# MCP protocol revision this endpoint speaks.
MCP_PROTOCOL_VERSION = "2025-06-18"

# Location of the OpenRPC document the tools are built from. The default is the
# copy vendored in ``spec/`` (a verbatim copy of ``source/services/shopping/
# mcp.openrpc.json`` from the UCP repo); set ``UCP_MCP_OPENRPC`` to point at a
# checkout of the specification instead.
_DEFAULT_OPENRPC = (
  pathlib.Path(__file__).resolve().parent.parent
  / "spec"
  / "shopping.mcp.openrpc.json"
)

# Tool name -> capability that must be advertised in the discovery profile for
# the tool to be listed. Tools whose capability this sample does not implement
# are not advertised.
_CAPABILITY_BY_TOOL = {
  "create_checkout": "dev.ucp.shopping.checkout",
  "get_checkout": "dev.ucp.shopping.checkout",
  "update_checkout": "dev.ucp.shopping.checkout",
  "complete_checkout": "dev.ucp.shopping.checkout",
  "cancel_checkout": "dev.ucp.shopping.checkout",
  "create_cart": "dev.ucp.shopping.cart",
  "get_cart": "dev.ucp.shopping.cart",
  "update_cart": "dev.ucp.shopping.cart",
  "cancel_cart": "dev.ucp.shopping.cart",
  "get_order": "dev.ucp.shopping.order",
}

_ANNOTATIONS_KEY = "x-ucp-mcp-annotations"
_LOCAL_REF = "#/components/schemas/"


def _input_schema(
  params: list[dict[str, Any]], components: dict[str, Any]
) -> dict[str, Any]:
  """Build an MCP ``inputSchema`` from OpenRPC ``params``.

  ``#/components/schemas/*`` references are inlined (non-recursively beyond one
  level). References to other documents (relative file paths) are replaced by a
  plain object schema: this sample does not publish those files.
  """

  def resolve(schema: Any, depth: int = 0) -> Any:
    if isinstance(schema, list):
      return [resolve(item, depth) for item in schema]
    if not isinstance(schema, dict):
      return schema
    ref = schema.get("$ref")
    if isinstance(ref, str):
      if ref.startswith(_LOCAL_REF) and depth < 4:
        target = components.get(ref[len(_LOCAL_REF) :])
        return resolve(target, depth + 1) if target is not None else {}
      return {"type": "object"}
    return {key: resolve(val, depth) for key, val in schema.items()}

  properties = {}
  required = []
  for param in params:
    properties[param["name"]] = resolve(param.get("schema", {}))
    if param.get("required"):
      required.append(param["name"])
  schema: dict[str, Any] = {"type": "object", "properties": properties}
  if required:
    schema["required"] = required
  return schema


def build_tools(
  openrpc: dict[str, Any], capabilities: set[str]
) -> list[dict[str, Any]]:
  """Build the ``tools/list`` result from an OpenRPC document.

  Each OpenRPC method becomes one tool. The ``x-ucp-mcp-annotations`` member of
  the method is published verbatim as the tool's ``annotations``.
  """
  components = openrpc.get("components", {}).get("schemas", {})
  tools = []
  for method in openrpc["methods"]:
    name = method["name"]
    if _CAPABILITY_BY_TOOL.get(name) not in capabilities:
      continue
    tool: dict[str, Any] = {
      "name": name,
      "title": method.get("summary", name),
      "description": method.get("description") or method.get("summary", name),
      "inputSchema": _input_schema(method.get("params", []), components),
    }
    if _ANNOTATIONS_KEY in method:
      tool["annotations"] = dict(method[_ANNOTATIONS_KEY])
    tools.append(tool)
  return tools


@functools.cache
def _tools() -> list[dict[str, Any]]:
  path = pathlib.Path(os.environ.get("UCP_MCP_OPENRPC") or _DEFAULT_OPENRPC)
  with path.open(encoding="utf-8") as f:
    openrpc = json.load(f)
  capabilities = set(config.get_profile_capabilities())
  return build_tools(openrpc, capabilities)


def _result(request_id: Any, result: Any) -> JSONResponse:
  return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id: Any, code: int, message: str) -> JSONResponse:
  return JSONResponse(
    {
      "jsonrpc": "2.0",
      "id": request_id,
      "error": {"code": code, "message": message},
    }
  )


@router.post("/mcp", dependencies=[Depends(dependencies.verify_signature)])
async def mcp_endpoint(request: Request) -> Response:
  """Minimal MCP (JSON-RPC 2.0) endpoint for capability discovery.

  Answers ``initialize`` and ``tools/list`` so MCP clients can complete
  discovery. ``tools/call`` is not yet bridged to the REST checkout handlers.

  Signature verification applies the same policy as every other inbound
  route (verify-if-present by default, required under
  ``--require_signatures``) so that bridging ``tools/call`` later cannot
  silently open an unsigned transport into the checkout handlers.
  """
  try:
    payload = await request.json()
  except Exception:
    return _error(None, -32700, "Parse error")

  method = payload.get("method")
  request_id = payload.get("id")

  # JSON-RPC notifications (no id), e.g. notifications/initialized.
  if (
    request_id is None
    and isinstance(method, str)
    and method.startswith("notifications/")
  ):
    return Response(status_code=202)

  if method == "initialize":
    return _result(
      request_id,
      {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {
          "name": "ucp-shopping-sample",
          "version": config.get_server_version(),
        },
      },
    )

  if method == "tools/list":
    return _result(request_id, {"tools": _tools()})

  if method == "tools/call":
    return _error(
      request_id,
      -32000,
      "tools/call is not yet implemented on this sample's MCP endpoint; use the"
      " REST transport for checkout operations.",
    )

  return _error(request_id, -32601, f"Method not found: {method}")
