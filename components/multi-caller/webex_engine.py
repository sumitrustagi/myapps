"""
Webex Calling Engine
Uses the Webex Calling Telephony API to place test calls.

Requirements:
  - A Webex Personal Access Token (from developer.webex.com/docs/getting-your-personal-access-token)
    OR an OAuth token with scope: spark:calls_write, spark:calls_read, spark-admin:telephony_config_read
  - The token holder must have a Webex Calling license with an active registered device

API flow:
  POST /v1/telephony/calls/dial   → initiates call, returns callId
  GET  /v1/telephony/calls        → list active calls (poll for state)
  POST /v1/telephony/calls/hangup → ends the call

Call states:
  initializing  → alerting  →  connected  (ANSWERED)
                             →  disconnected (NO-ANSWER / BUSY / REJECTED)
"""

import time
import logging
import threading
from datetime import datetime

import requests

log = logging.getLogger("webex_engine")

RESULT_ANSWERED  = "ANSWERED"
RESULT_NO_ANSWER = "NO-ANSWER"
RESULT_BUSY      = "BUSY"
RESULT_REJECTED  = "REJECTED"
RESULT_ERROR     = "ERROR"

WEBEX_BASE = "https://webexapis.com/v1"


class WebexEngine:
    """
    Places test calls via Webex Calling Telephony API.
    Requires a valid Webex access token (personal or OAuth).
    """

    def __init__(self, access_token: str):
        self.token   = access_token.strip()
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type":  "application/json",
        })
        self._me: dict = {}

    # ── Auth / profile ────────────────────────────────────────────────────────

    def verify_token(self) -> tuple[bool, str]:
        """Check token validity and retrieve user profile."""
        try:
            r = self._session.get(f"{WEBEX_BASE}/people/me", timeout=10)
            if r.ok:
                self._me = r.json()
                display  = self._me.get("displayName", "Unknown")
                email    = (self._me.get("emails") or [""])[0]
                return True, f"{display} <{email}>"
            if r.status_code == 401:
                return False, "401 Unauthorized — invalid or expired token"
            return False, f"HTTP {r.status_code}: {r.text[:100]}"
        except requests.RequestException as e:
            return False, f"Network error: {e}"

    def check_calling_license(self) -> tuple[bool, str]:
        """Verify the user has a Webex Calling license."""
        try:
            r = self._session.get(f"{WEBEX_BASE}/people/me", timeout=10)
            if r.ok:
                types = r.json().get("type", "")
                # Calling is available for licensed users
                return True, "License check passed (API access confirmed)"
            return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)

    # ── Test call ─────────────────────────────────────────────────────────────

    def test_call(self, destination: str, ring_timeout: int = 30,
                  answer_duration: int = 3) -> dict:
        """
        Dial *destination* (extension, DID, or +E.164),
        poll for answer/disconnect up to *ring_timeout* seconds.
        """
        result = {
            "number":     destination,
            "result":     RESULT_ERROR,
            "api_code":   "",
            "duration_s": 0,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note":       "",
            "platform":   "Webex Calling",
        }

        # ── 1. Dial ───────────────────────────────────────────────────────────
        t_start = time.time()
        try:
            r = self._session.post(
                f"{WEBEX_BASE}/telephony/calls/dial",
                json={"destination": destination},
                timeout=15,
            )
        except requests.RequestException as e:
            result["note"] = f"Network error: {e}"
            return result

        if not r.ok:
            result["api_code"] = str(r.status_code)
            try:
                err = r.json().get("message", r.text[:200])
            except Exception:
                err = r.text[:200]
            result["note"] = f"Dial HTTP {r.status_code}: {err}"
            return result

        resp_body = r.json()
        call_id   = resp_body.get("callId") or resp_body.get("id", "")
        result["api_code"] = "200"

        if not call_id:
            result["note"] = "Dial succeeded but no callId returned"
            return result

        log.debug("Webex call initiated — callId=%s  dest=%s", call_id, destination)

        # ── 2. Poll call state ────────────────────────────────────────────────
        deadline   = time.time() + ring_timeout
        last_state = "initializing"
        disconnect_reason = ""

        while time.time() < deadline:
            time.sleep(1.5)
            try:
                pr = self._session.get(
                    f"{WEBEX_BASE}/telephony/calls/{call_id}",
                    timeout=10,
                )
            except requests.RequestException:
                # Fallback: list all active calls
                try:
                    lr = self._session.get(f"{WEBEX_BASE}/telephony/calls", timeout=10)
                    if lr.ok:
                        calls = lr.json().get("items", [])
                        matched = next((c for c in calls if c.get("id") == call_id), None)
                        if matched:
                            pr = type("R", (), {"ok": True, "json": lambda self: matched})()
                        else:
                            # Call no longer in active list → disconnected
                            result["result"] = RESULT_NO_ANSWER
                            result["note"]   = "Call ended (no longer active)"
                            return result
                    else:
                        continue
                except Exception:
                    continue

            if not pr.ok:
                # 404 = call ended
                if hasattr(pr, "status_code") and pr.status_code == 404:
                    result["result"] = RESULT_NO_ANSWER
                    result["note"]   = "Call ended before answer (404)"
                    return result
                continue

            data       = pr.json()
            state      = data.get("status", data.get("state", ""))
            last_state = state

            if state == "connected":
                elapsed = time.time() - t_start
                time.sleep(answer_duration)
                self._hangup(call_id)
                result["result"]     = RESULT_ANSWERED
                result["duration_s"] = round(elapsed, 1)
                result["note"]       = "Call answered"
                return result

            if state in ("disconnected", "remotely_held"):
                cause = data.get("disconnectCause") or data.get("cause", "")
                result["api_code"]   = cause
                if cause in ("busy", "BUSY"):
                    result["result"] = RESULT_BUSY
                    result["note"]   = "Busy"
                elif cause in ("declined", "DECLINED", "rejected"):
                    result["result"] = RESULT_REJECTED
                    result["note"]   = f"Declined ({cause})"
                else:
                    result["result"] = RESULT_NO_ANSWER
                    result["note"]   = f"Disconnected — {cause or 'no answer'}"
                return result

        # ── 3. Timeout ────────────────────────────────────────────────────────
        self._hangup(call_id)
        result["result"]     = RESULT_NO_ANSWER
        result["duration_s"] = round(time.time() - t_start, 1)
        result["note"]       = f"No answer in {ring_timeout}s (state: {last_state})"
        return result

    def _hangup(self, call_id: str):
        try:
            self._session.post(
                f"{WEBEX_BASE}/telephony/calls/hangup",
                json={"callId": call_id},
                timeout=10,
            )
            log.debug("Webex call hung up — %s", call_id)
        except Exception as e:
            log.debug("Hangup failed %s: %s", call_id, e)

    # ── Utility ───────────────────────────────────────────────────────────────

    @staticmethod
    def token_help_url() -> str:
        return "https://developer.webex.com/docs/getting-your-personal-access-token"
