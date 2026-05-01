"""
MS Teams Call Engine
Uses Microsoft Graph Communications API to initiate test calls.

Azure AD App requirements:
  - Application permission: Calls.Initiate.All  (admin consent required)
  - Application permission: Calls.InitiateGroupCall.All  (optional)
  - The app must be configured as a Teams calling bot in Teams Admin Center

Targets supported:
  - Teams UPN / Object ID  (user@domain.com  or  xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
  - Phone number            (+35312345678)
  - SIP URI                 (sip:user@domain.com)
"""

import time
import json
import logging
import threading
from datetime import datetime

import requests

log = logging.getLogger("teams_engine")

# Call result constants
RESULT_ANSWERED  = "ANSWERED"
RESULT_NO_ANSWER = "NO-ANSWER"
RESULT_BUSY      = "BUSY"
RESULT_REJECTED  = "REJECTED"
RESULT_ERROR     = "ERROR"

GRAPH_BASE  = "https://graph.microsoft.com/v1.0"
LOGIN_BASE  = "https://login.microsoftonline.com"


class TeamsEngine:
    """
    Authenticates with Azure AD (client credentials flow)
    and places test calls via Microsoft Graph Communications API.
    """

    def __init__(self, tenant_id: str, client_id: str, client_secret: str,
                 caller_display: str = "Call Tester",
                 callback_uri: str = "https://callback.example.com/teams"):
        self.tenant_id       = tenant_id.strip()
        self.client_id       = client_id.strip()
        self.client_secret   = client_secret.strip()
        self.caller_display  = caller_display
        self.callback_uri    = callback_uri        # Must be HTTPS; polling used regardless
        self._token: str     = ""
        self._token_expiry   = 0
        self._lock           = threading.Lock()

    # ── Authentication ────────────────────────────────────────────────────────

    def authenticate(self) -> tuple[bool, str]:
        """Acquire a client-credentials token from Azure AD."""
        try:
            import msal
        except ImportError:
            return False, "msal not installed — run: pip install msal"

        try:
            app = msal.ConfidentialClientApplication(
                client_id         = self.client_id,
                client_credential = self.client_secret,
                authority         = f"{LOGIN_BASE}/{self.tenant_id}",
            )
            result = app.acquire_token_for_client(
                scopes=["https://graph.microsoft.com/.default"]
            )
            if "access_token" in result:
                self._token        = result["access_token"]
                self._token_expiry = time.time() + result.get("expires_in", 3600) - 60
                log.info("Teams authenticated OK")
                return True, "Authenticated — token acquired"
            err = result.get("error_description") or result.get("error", "Unknown error")
            return False, err
        except Exception as e:
            return False, str(e)

    def _ensure_token(self):
        if time.time() >= self._token_expiry:
            self.authenticate()

    def _hdrs(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }

    # ── Connectivity check ────────────────────────────────────────────────────

    def verify_connection(self) -> tuple[bool, str]:
        """Quick check: can we reach Graph and is the token valid?"""
        self._ensure_token()
        try:
            r = requests.get(
                f"{GRAPH_BASE}/organization",
                headers=self._hdrs(), timeout=10
            )
            if r.ok:
                orgs = r.json().get("value", [{}])
                name = orgs[0].get("displayName", "Unknown org") if orgs else "Unknown org"
                return True, f"Connected — Org: {name}"
            return False, f"Graph API {r.status_code}: {r.text[:120]}"
        except Exception as e:
            return False, str(e)

    # ── Build call target ─────────────────────────────────────────────────────

    @staticmethod
    def _build_target(target: str) -> dict:
        """
        Convert a target string into a Graph API invitee object.
        Supports: UPN, Object ID, phone number (+E.164), sip: URI.
        """
        t = target.strip()
        if t.lower().startswith("sip:"):
            return {
                "identity": {
                    "user": {
                        "@odata.type": "#microsoft.graph.identity",
                        "displayName": t,
                        "id":          t,
                    }
                }
            }
        if t.startswith("+") or (t.isdigit() and len(t) >= 7):
            return {
                "identity": {
                    "phone": {
                        "@odata.type": "#microsoft.graph.identity",
                        "id": t,
                    }
                }
            }
        # UPN or Object ID
        return {
            "identity": {
                "user": {
                    "@odata.type": "#microsoft.graph.identity",
                    "id": t,
                }
            }
        }

    # ── Test call ─────────────────────────────────────────────────────────────

    def test_call(self, target: str, ring_timeout: int = 30,
                  answer_duration: int = 3) -> dict:
        """
        Initiate a call to *target*, poll for up to *ring_timeout* seconds,
        hang up if answered after *answer_duration* seconds.
        Returns a result dict.
        """
        result = {
            "number":     target,
            "result":     RESULT_ERROR,
            "api_code":   "",
            "duration_s": 0,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note":       "",
            "platform":   "MS Teams",
        }

        self._ensure_token()
        if not self._token:
            result["note"] = "Not authenticated — run Authenticate first"
            return result

        # ── 1. Initiate call ──────────────────────────────────────────────────
        payload = {
            "@odata.type":         "#microsoft.graph.call",
            "callbackUri":          self.callback_uri,
            "targets":              [self._build_target(target)],
            "requestedModalities":  ["audio"],
            "mediaConfig": {
                "@odata.type": "#microsoft.graph.serviceHostedMediaConfig",
                "preFetchMedia": [],
            },
            "tenantId": self.tenant_id,
        }

        t_start = time.time()
        try:
            r = requests.post(
                f"{GRAPH_BASE}/communications/calls",
                headers=self._hdrs(),
                json=payload,
                timeout=20,
            )
        except requests.RequestException as e:
            result["note"] = f"Network error: {e}"
            return result

        if r.status_code == 401:
            self.authenticate()
            result["note"] = "Token expired — re-authenticate and retry"
            return result

        if not r.ok:
            result["api_code"] = str(r.status_code)
            try:
                err = r.json().get("error", {}).get("message", r.text[:200])
            except Exception:
                err = r.text[:200]
            result["note"] = f"HTTP {r.status_code}: {err}"
            return result

        call_data = r.json()
        call_id   = call_data.get("id", "")
        result["api_code"] = "201"

        if not call_id:
            result["note"] = "Call initiated but no call ID returned"
            return result

        log.debug("Call initiated — id=%s  target=%s", call_id, target)

        # ── 2. Poll for state ─────────────────────────────────────────────────
        deadline = time.time() + ring_timeout
        last_state = ""

        while time.time() < deadline:
            time.sleep(2.0)
            try:
                pr = requests.get(
                    f"{GRAPH_BASE}/communications/calls/{call_id}",
                    headers=self._hdrs(),
                    timeout=10,
                )
            except requests.RequestException:
                continue

            if not pr.ok:
                break

            data      = pr.json()
            state     = data.get("state", "")
            last_state = state

            if state == "established":
                elapsed = time.time() - t_start
                time.sleep(answer_duration)
                self._hangup(call_id)
                result["result"]     = RESULT_ANSWERED
                result["duration_s"] = round(elapsed, 1)
                result["note"]       = "Call answered"
                return result

            if state == "terminated":
                ri   = data.get("resultInfo") or {}
                code = ri.get("code", 0)
                sub  = ri.get("subCode", 0)
                result["api_code"] = f"{code}.{sub}"
                if code in (486, 600):
                    result["result"] = RESULT_BUSY
                    result["note"]   = f"{code} Busy / Do Not Disturb"
                elif code in (403, 404, 410):
                    result["result"] = RESULT_REJECTED
                    result["note"]   = f"{code} User not found / forbidden"
                elif code in (480, 487, 603):
                    result["result"] = RESULT_NO_ANSWER
                    result["note"]   = f"{code} No answer / declined"
                else:
                    result["result"] = RESULT_NO_ANSWER
                    result["note"]   = f"Terminated — code {code}.{sub}"
                return result

        # ── 3. Timeout ────────────────────────────────────────────────────────
        self._hangup(call_id)
        result["result"]     = RESULT_NO_ANSWER
        result["duration_s"] = round(time.time() - t_start, 1)
        result["note"]       = f"No answer in {ring_timeout}s (last state: {last_state})"
        return result

    def _hangup(self, call_id: str):
        try:
            requests.delete(
                f"{GRAPH_BASE}/communications/calls/{call_id}",
                headers=self._hdrs(),
                timeout=10,
            )
            log.debug("Hung up call %s", call_id)
        except Exception as e:
            log.debug("Hang up failed for %s: %s", call_id, e)
