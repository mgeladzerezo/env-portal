from collections.abc import Callable, Iterator
from typing import Any

from portal.policy.base import PolicyResult, Violation

REQUIRED_TAGS = ("owner", "expires-at", "portal-environment-id")
OPEN_CIDRS = {"0.0.0.0/0", "::/0"}

Rule = Callable[[str, str, dict[str, Any]], Iterator[Violation]]


def _required_tags(address: str, resource_type: str, after: dict[str, Any]) -> Iterator[Violation]:
    # tags_all includes provider default_tags; it is absent on resources that cannot be tagged.
    tags = after.get("tags_all") or after.get("tags")
    if "tags_all" not in after and "tags" not in after:
        return
    missing = [tag for tag in REQUIRED_TAGS if not (tags or {}).get(tag)]
    if missing:
        yield Violation("required-tags", address, f"missing tags: {', '.join(missing)}")


def _no_open_ingress(
    address: str, resource_type: str, after: dict[str, Any]
) -> Iterator[Violation]:
    if resource_type == "aws_security_group":
        rules = after.get("ingress") or []
        cidrs = {
            cidr
            for rule in rules
            for key in ("cidr_blocks", "ipv6_cidr_blocks")
            for cidr in rule.get(key) or []
        }
    elif resource_type == "aws_vpc_security_group_ingress_rule":
        cidrs = {after.get("cidr_ipv4"), after.get("cidr_ipv6")}
    elif resource_type == "aws_security_group_rule" and after.get("type") == "ingress":
        cidrs = {*(after.get("cidr_blocks") or []), *(after.get("ipv6_cidr_blocks") or [])}
    else:
        return
    if cidrs & OPEN_CIDRS:
        yield Violation(
            "no-open-ingress", address, "ingress from the whole internet is not allowed"
        )


def _private_encrypted_database(
    address: str, resource_type: str, after: dict[str, Any]
) -> Iterator[Violation]:
    if resource_type != "aws_db_instance":
        return
    if after.get("publicly_accessible"):
        yield Violation("private-database", address, "databases must not be publicly accessible")
    if not after.get("storage_encrypted"):
        yield Violation("encrypted-database", address, "database storage must be encrypted")


def _no_public_bucket_acl(
    address: str, resource_type: str, after: dict[str, Any]
) -> Iterator[Violation]:
    if resource_type in {"aws_s3_bucket", "aws_s3_bucket_acl"} and str(
        after.get("acl", "")
    ).startswith("public-"):
        yield Violation("no-public-bucket", address, "public bucket ACLs are not allowed")


RULES: tuple[Rule, ...] = (
    _required_tags,
    _no_open_ingress,
    _private_encrypted_database,
    _no_public_bucket_acl,
)


class BuiltinPolicyChecker:
    """A small rule set evaluated in-process, for setups without conftest.

    The Rego policies in policies/ express the same rules for the conftest checker.
    """

    def check(self, plan: dict[str, Any]) -> PolicyResult:
        violations: list[Violation] = []
        for change in plan.get("resource_changes", []):
            actions = set(change.get("change", {}).get("actions", []))
            after = change.get("change", {}).get("after")
            if change.get("mode") != "managed" or not actions & {"create", "update"} or not after:
                continue
            for rule in RULES:
                violations.extend(rule(change["address"], change["type"], after))
        return PolicyResult(tuple(violations))
