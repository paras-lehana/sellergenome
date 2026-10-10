"""Protected test-identity and fixed endpoint policy for all upstream egress."""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit


class SourcePolicyError(ValueError):
    """Safe denial code; input values are never included in the message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def approved_glids() -> frozenset[str]:
    """Read the owner-approved manifest; missing or malformed data denies all."""
    path = os.getenv("APPROVED_GLIDS_PATH", "")
    if not path:
        return frozenset()
    try:
        values = json.loads(Path(path).read_text())
        if isinstance(values, dict):
            values = values.get("approved_glids", values.get("glids"))
        if not isinstance(values, list) or not all(
            isinstance(x, str) and re.fullmatch(r"[0-9]{1,20}", x)
            for x in values
        ):
            return frozenset()
        return frozenset(values)
    except (OSError, ValueError, TypeError):
        return frozenset()


def valid_glid(identifier: str) -> str:
    """Format check only, for upstream.glid_access = prod (D51): a numeric GLID, no manifest lookup."""
    value = (
        identifier.removeprefix("real-") if isinstance(identifier, str) else ""
    )
    if not re.fullmatch(r"[0-9]{1,20}", value):
        raise SourcePolicyError(
            "invalid_seller", "A numeric seller GLID is required."
        )
    return value


def require_approved(identifier: str) -> str:
    """Check exact membership, never substitute a sample or arbitrary GLID."""
    value = (
        identifier.removeprefix("real-") if isinstance(identifier, str) else ""
    )
    if not re.fullmatch(r"[0-9]{1,20}", value):
        raise SourcePolicyError(
            "invalid_seller", "A numeric test seller identifier is required."
        )
    if value not in approved_glids():
        raise SourcePolicyError(
            "unapproved_glid",
            "Only identifiers in the protected approved test manifest may call a live source.",
        )
    return value


def approve_endpoint(
    source: str, url: str, test_transport: bool = False, gateway: str = ""
) -> None:
    """Permit documented internal HTTP origins and exact public HTTPS origins.

    ``gateway`` is the IndiaMART pass-through gateway origin (Genome Admin → APIs → Gateway, allow-listed there).
    Only Redash may use it, only over HTTPS, and only on the same fixed Redash paths."""
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in (None, 80, 443)
    ):
        raise SourcePolicyError(
            "invalid_endpoint",
            "Source endpoint is outside the approved endpoint registry.",
        )
    # Injected transports never perform network I/O; their .test hosts allow
    # deterministic fixtures without weakening the real network boundary.
    if (
        test_transport
        and (parsed.hostname or "").endswith(".test")
        and parsed.scheme == "https"
    ):
        return
    hosts = {
        "chat": {"lms.imutils.com"},
        "pns": {"audio.imutils.com"},
        "company": {"company.imutils.com"},
        "context": {"prod-global-context.imbi.prod"},
        "sts": {"merp.intermesh.net", "weberp.intermesh.net"},
        "redash": {"redash.intermesh.net"},
    }
    family = "redash" if source.startswith("redash") else source
    registry_path = os.getenv("SOURCE_ENDPOINTS_PATH")
    if not registry_path and os.getenv("PRIVATE_CONFIG_DIR"):
        registry_path = str(
            Path(os.environ["PRIVATE_CONFIG_DIR"]) / "source_endpoints.json"
        )
    if registry_path:
        try:
            registry = json.loads(Path(registry_path).read_text())
            entries = registry.get(source, registry.get(family, []))
            origin = f"{parsed.scheme}://{parsed.netloc}"
            for entry in entries:
                if isinstance(entry, dict) and entry.get("origin") == origin:
                    paths = entry.get("paths", [])
                    if any(
                        isinstance(path, str)
                        and (
                            parsed.path == path
                            or (
                                path.endswith("/*")
                                and parsed.path.startswith(path[:-1])
                            )
                        )
                        for path in paths
                    ):
                        return
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    host_allowed = parsed.hostname in hosts.get(family, set())
    if gateway and family == "redash":
        through = urlsplit(gateway)
        host_allowed = host_allowed or (
            through.scheme == parsed.scheme == "https" and bool(through.netloc) and parsed.netloc == through.netloc
        )
    transport_allowed = parsed.scheme == "https" or (
        parsed.scheme == "http" and family in {"chat", "pns", "context", "sts", "company"}
    )
    fixed_paths = {
        "chat": "/addressbook/conversationList/",
        "pns": "/pns-call-insights",
        "context": "/go/api/globalcontext/v1/userCtx",
        "sts": "/go/api/common/v1/newhistory",
    }
    path_allowed = parsed.path == fixed_paths.get(family)
    if family == "company":
        path_allowed = bool(re.fullmatch(r"/wservce/company/detail/glusrid/[0-9]*/alias/[A-Za-z0-9_-]*/cat_link//token/[^/]+/req_server/home_server/modid/tdw/is_mob_device/", parsed.path))
    if family == "redash":
        path_allowed = bool(
            re.fullmatch(
                r"/api/(query_results(?:/[0-9]+\.json)?|jobs/[A-Za-z0-9_-]{1,100})",
                parsed.path,
            )
        )
    if not host_allowed or not transport_allowed or not path_allowed:
        raise SourcePolicyError(
            "invalid_endpoint",
            "Source endpoint is outside the approved endpoint registry.",
        )


def safe_target_list() -> list[dict]:
    """Protected UI can choose approved aliases without accepting arbitrary IDs."""
    return [
        {
            "seller_id": "real-" + value,
            "label": "Approved test seller " + value,
            "glid": value,
        }
        for value in sorted(approved_glids(), key=int)
    ]


def seed_approved_targets(store) -> int:
    """Create private empty test stubs without replacing supplied seller records."""
    created = 0
    for target in safe_target_list():
        identifier = target["seller_id"]
        if store.get("sellers", identifier):
            continue
        store.put(
            "sellers",
            identifier,
            {
                "id": identifier,
                "name": target["label"],
                "company": "Approved internal test account",
                "city": "Unknown",
                "category": "Unspecified",
                "language": "en-IN",
                "language_status": "unverified_default",
                "segment": "Test account",
                "persona_tag": "balanced",
                "source_mode": "private",
                "is_test_target": True,
                "updated_at": None,
                "metrics": {},
                "evidence": [],
                "provenance": "owner-approved Internal GLIDs manifest",
            },
        )
        created += 1
    return created
