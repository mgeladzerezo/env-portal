import json
import subprocess
from pathlib import Path

import pytest

from portal.policy import conftest as conftest_module
from portal.policy.base import PolicyCheckError
from portal.policy.builtin import BuiltinPolicyChecker
from portal.policy.conftest import ConftestPolicyChecker

GOOD_TAGS = {"owner": "alice", "expires-at": "2026-01-02T03:04:05Z", "portal-environment-id": "e1"}


def plan(*resources: tuple[str, str, dict], actions: tuple[str, ...] = ("create",)) -> dict:
    return {
        "resource_changes": [
            {
                "address": address,
                "mode": "managed",
                "type": resource_type,
                "change": {"actions": list(actions), "after": after},
            }
            for address, resource_type, after in resources
        ]
    }


def rules(result) -> list[str]:
    return [violation.rule for violation in result.violations]


def test_compliant_plan_passes() -> None:
    result = BuiltinPolicyChecker().check(
        plan(
            ("aws_s3_bucket.site", "aws_s3_bucket", {"tags_all": GOOD_TAGS}),
            (
                "aws_db_instance.this",
                "aws_db_instance",
                {"tags_all": GOOD_TAGS, "publicly_accessible": False, "storage_encrypted": True},
            ),
            ("terraform_data.replica[0]", "terraform_data", {"input": {}}),
        )
    )

    assert result.passed


def test_missing_owner_or_expiry_tag_is_a_violation() -> None:
    result = BuiltinPolicyChecker().check(
        plan(("aws_instance.vm", "aws_instance", {"tags_all": {"owner": "alice"}}))
    )

    assert not result.passed
    (violation,) = result.violations
    assert violation.rule == "required-tags"
    assert violation.resource == "aws_instance.vm"
    assert violation.message == "missing tags: expires-at, portal-environment-id"


def test_security_group_open_to_the_internet_is_a_violation() -> None:
    inline = {"tags_all": GOOD_TAGS, "ingress": [{"cidr_blocks": ["0.0.0.0/0"]}]}
    rule = {"cidr_ipv4": "0.0.0.0/0", "tags_all": GOOD_TAGS}
    internal = {"cidr_ipv4": "10.0.0.0/16", "tags_all": GOOD_TAGS}

    result = BuiltinPolicyChecker().check(
        plan(
            ("aws_security_group.a", "aws_security_group", inline),
            ("aws_vpc_security_group_ingress_rule.b", "aws_vpc_security_group_ingress_rule", rule),
            (
                "aws_vpc_security_group_ingress_rule.c",
                "aws_vpc_security_group_ingress_rule",
                internal,
            ),
        )
    )

    assert [(v.rule, v.resource) for v in result.violations] == [
        ("no-open-ingress", "aws_security_group.a"),
        ("no-open-ingress", "aws_vpc_security_group_ingress_rule.b"),
    ]


def test_public_or_unencrypted_database_is_a_violation() -> None:
    after = {"tags_all": GOOD_TAGS, "publicly_accessible": True, "storage_encrypted": False}

    result = BuiltinPolicyChecker().check(plan(("aws_db_instance.this", "aws_db_instance", after)))

    assert rules(result) == ["private-database", "encrypted-database"]


def test_public_bucket_acl_is_a_violation() -> None:
    after = {"tags_all": GOOD_TAGS, "acl": "public-read"}

    result = BuiltinPolicyChecker().check(plan(("aws_s3_bucket.site", "aws_s3_bucket", after)))

    assert rules(result) == ["no-public-bucket"]


def test_resources_being_deleted_are_not_checked() -> None:
    result = BuiltinPolicyChecker().check(
        plan(("aws_instance.vm", "aws_instance", None), actions=("delete",))
    )

    assert result.passed


def fake_conftest(monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str, stderr: str = ""):
    commands: list[list[str]] = []

    def run(command, **kwargs):
        commands.append(command)
        plan_file = Path(command[-1])
        assert json.loads(plan_file.read_text(encoding="utf-8")) == {"resource_changes": []}
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr(conftest_module.subprocess, "run", run)
    return commands


def test_conftest_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    commands = fake_conftest(
        monkeypatch, 0, json.dumps([{"filename": "plan.json", "successes": 4}])
    )

    result = ConftestPolicyChecker("conftest", tmp_path).check({"resource_changes": []})

    assert result.passed
    assert commands[0][:4] == ["conftest", "test", "--output=json", "--no-color"]
    assert f"--policy={tmp_path}" in commands[0]


def test_conftest_failures_become_violations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = [
        {
            "filename": "plan.json",
            "failures": [
                {
                    "msg": "aws_instance.vm: missing tags: owner",
                    "metadata": {"rule": "required-tags", "resource": "aws_instance.vm"},
                },
                {"msg": "something without metadata"},
            ],
        }
    ]
    fake_conftest(monkeypatch, 1, json.dumps(report))

    result = ConftestPolicyChecker("conftest", tmp_path).check({"resource_changes": []})

    assert not result.passed
    assert [(v.rule, v.resource) for v in result.violations] == [
        ("required-tags", "aws_instance.vm"),
        ("conftest", ""),
    ]


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        (2, ""),
        (0, "not json"),
        (1, "[]"),
    ],
    ids=["tool-error", "garbage-output", "failure-without-details"],
)
def test_conftest_problems_are_errors_not_approvals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, returncode: int, stdout: str
) -> None:
    fake_conftest(monkeypatch, returncode, stdout, stderr="rego_parse_error")

    with pytest.raises(PolicyCheckError):
        ConftestPolicyChecker("conftest", tmp_path).check({"resource_changes": []})


def test_missing_conftest_binary_is_an_error(tmp_path: Path) -> None:
    checker = ConftestPolicyChecker(str(tmp_path / "no-such-conftest"), tmp_path)

    with pytest.raises(PolicyCheckError, match="could not run conftest"):
        checker.check({"resource_changes": []})
