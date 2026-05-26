"""Generic HTTP reachability check - the configured URL responds with a 2xx."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, ClassVar

import httpx

from chap_checker.checks.base import CheckContext, CheckResult, Status, format_request_error, register_check

if TYPE_CHECKING:
    from dhis2w_client import Dhis2Client


@register_check
class Http2xxCheck:
    """Unauthenticated GET on the target's base URL; assert the final status is 2xx.

    Lightweight reachability probe. Sends no credentials and follows
    redirects (up to 5 hops) so that a server which redirects `/` to a
    login page is still reported as OK. The point is to learn whether
    DNS, TLS, and the reverse proxy / load balancer in front of the
    target are healthy - distinct from `dhis2_ping`, which can fail for
    purely auth reasons even when the front door is fine.

    Works against `http://` and `https://` URLs alike (the name is the
    OSI-layer protocol, not the scheme). Runs before `dhis2_ping` for
    readability (`order = 5`) but has no `requires` link, so disabling
    either check leaves the other working.
    """

    name: ClassVar[str] = "http_2xx"
    description: ClassVar[str] = (
        "Target URL returns a 2xx status code (unauthenticated reachability probe, follows redirects)."
    )
    order: ClassVar[int] = 5
    requires: ClassVar[list[str]] = []

    async def run(self, client: Dhis2Client, ctx: CheckContext) -> CheckResult:  # noqa: ARG002 - probe is unauthenticated, doesn't use the typed client
        url = str(ctx.target.base_url)
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=ctx.target.timeout_s,
                verify=ctx.target.verify_tls,
                follow_redirects=True,
                max_redirects=5,
            ) as h:
                response = await h.get(url)
        except Exception as exc:  # noqa: BLE001 - surface any transport error as a result
            return CheckResult(
                name=self.name,
                status=Status.ERROR,
                message=format_request_error(exc, path=url),
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        duration_ms = (time.perf_counter() - start) * 1000
        details: dict[str, Any] = {
            "http_status": response.status_code,
            "final_url": str(response.url),
        }
        if 200 <= response.status_code < 300:
            return CheckResult(
                name=self.name,
                status=Status.OK,
                message=f"{response.status_code} from {response.url}",
                details=details,
                duration_ms=duration_ms,
            )
        return CheckResult(
            name=self.name,
            status=Status.FAIL,
            message=f"Unexpected status {response.status_code} from {response.url}.",
            details=details,
            duration_ms=duration_ms,
        )
