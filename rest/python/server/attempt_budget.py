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

"""Attempt budget (dev.ucp.shopping.attempt_budget) for the sample server.

Caps failed Complete Checkout attempts per checkout session. Once the budget is
spent the checkout is escalated to the Buyer (`requires_escalation` plus
`continue_url`) and further completions are refused before payment processing.
"""

from typing import Any

import config
import db
from sqlalchemy.ext.asyncio import AsyncSession

CAPABILITY = "dev.ucp.shopping.attempt_budget"
ERROR_CODE = "attempt_limit_reached"
_DEFAULT_MAX_FAILED = 3
_TERMINAL = ("completed", "canceled")


def enabled() -> bool:
  """Return whether the discovery profile advertises the capability."""
  return bool(config.get_profile_capability(CAPABILITY))


def max_failed() -> int:
  """Return the per-session limit from the capability's profile config."""
  entries = config.get_profile_capability(CAPABILITY)
  for entry in entries:
    value = ((entry.get("config") or {}).get("complete") or {}).get(
      "max_failed"
    )
    if isinstance(value, int) and value >= 1:
      return value
  return _DEFAULT_MAX_FAILED


async def counter(session: AsyncSession, checkout_id: str) -> dict[str, int]:
  """Return {"used", "limit"} for a checkout, creating its row if needed."""
  limit = max_failed()
  await db.ensure_attempt_counter(session, checkout_id, limit)
  used, stored_limit = await db.get_attempt_counter(session, checkout_id)
  return {"used": used, "limit": stored_limit}


def refusal_message() -> dict[str, Any]:
  """Return the standard exhausted-budget error message."""
  return {
    "type": "error",
    "code": ERROR_CODE,
    "severity": "requires_buyer_review",
    "path": "$.attempts.complete",
    "content": (
      "Too many failed payment attempts. Continue with the buyer to finish"
      " this order."
    ),
  }


def decorate(
  body: dict[str, Any], attempts: dict[str, int], continue_url: str
) -> dict[str, Any]:
  """Add `attempts` to a checkout body; escalate it when exhausted.

  Args:
    body: Checkout response body (mutated and returned).
    attempts: The {"used", "limit"} counter.
    continue_url: Handoff URL used when the budget is spent.

  Returns:
    The decorated body.

  """
  body["attempts"] = {"complete": dict(attempts)}
  exhausted = attempts["used"] >= attempts["limit"]
  if exhausted and body.get("status") not in _TERMINAL:
    body["status"] = "requires_escalation"
    body["continue_url"] = continue_url
    messages = [
      m for m in body.get("messages", []) if m.get("code") != ERROR_CODE
    ]
    messages.append(refusal_message())
    body["messages"] = messages
  return body
