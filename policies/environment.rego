# Rules every environment plan has to satisfy. Input is `terraform show -json <plan>`.
# The same rules exist in backend/portal/policy/builtin.py for setups without conftest;
# change both together.
package main

import rego.v1

required_tags := {"owner", "expires-at", "portal-environment-id"}

open_cidrs := {"0.0.0.0/0", "::/0"}

changed contains change if {
	some change in input.resource_changes
	change.mode == "managed"
	some action in change.change.actions
	action in {"create", "update"}
	change.change.after != null
}

tags(after) := after.tags_all if {
	after.tags_all != null
} else := after.tags if {
	after.tags != null
} else := {}

taggable(after) if "tags_all" in object.keys(after)

taggable(after) if "tags" in object.keys(after)

deny contains violation if {
	some change in changed
	taggable(change.change.after)
	present := {name | some name, value in tags(change.change.after); value != ""}
	missing := required_tags - present
	count(missing) > 0
	violation := {
		"msg": sprintf("missing tags: %s", [concat(", ", sort(missing))]),
		"rule": "required-tags",
		"resource": change.address,
	}
}

ingress_cidrs(change) := cidrs if {
	change.type == "aws_security_group"
	cidrs := {cidr |
		some rule in change.change.after.ingress
		some key in ["cidr_blocks", "ipv6_cidr_blocks"]
		some cidr in rule[key]
	}
}

ingress_cidrs(change) := cidrs if {
	change.type == "aws_vpc_security_group_ingress_rule"
	cidrs := {cidr |
		some key in ["cidr_ipv4", "cidr_ipv6"]
		cidr := change.change.after[key]
	}
}

ingress_cidrs(change) := cidrs if {
	change.type == "aws_security_group_rule"
	change.change.after.type == "ingress"
	cidrs := {cidr |
		some key in ["cidr_blocks", "ipv6_cidr_blocks"]
		some cidr in change.change.after[key]
	}
}

deny contains violation if {
	some change in changed
	count(ingress_cidrs(change) & open_cidrs) > 0
	violation := {
		"msg": "ingress from the whole internet is not allowed",
		"rule": "no-open-ingress",
		"resource": change.address,
	}
}

deny contains violation if {
	some change in changed
	change.type == "aws_db_instance"
	change.change.after.publicly_accessible == true
	violation := {
		"msg": "databases must not be publicly accessible",
		"rule": "private-database",
		"resource": change.address,
	}
}

deny contains violation if {
	some change in changed
	change.type == "aws_db_instance"
	not change.change.after.storage_encrypted
	violation := {
		"msg": "database storage must be encrypted",
		"rule": "encrypted-database",
		"resource": change.address,
	}
}

deny contains violation if {
	some change in changed
	change.type in {"aws_s3_bucket", "aws_s3_bucket_acl"}
	startswith(change.change.after.acl, "public-")
	violation := {
		"msg": "public bucket ACLs are not allowed",
		"rule": "no-public-bucket",
		"resource": change.address,
	}
}
