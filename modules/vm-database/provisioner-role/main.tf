# Role the portal worker assumes while running the vm-database template. Creating
# anything requires the template's tag on the request, and changing or deleting
# anything requires that tag on the resource, so the role cannot touch infrastructure
# the portal did not create.

locals {
  template_tag = "aws:ResourceTag/portal-template"
  request_tag  = "aws:RequestTag/portal-template"
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = var.trusted_role_arns
    }
  }
}

data "aws_iam_policy_document" "permissions" {
  statement {
    sid = "Describe"
    actions = [
      "ec2:Describe*",
      "rds:Describe*",
      "rds:ListTagsForResource",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "ReadAmazonLinuxAmiParameter"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:*::parameter/aws/service/ami-amazon-linux-latest/*"]
  }

  statement {
    sid     = "CreateTaggedEc2Resources"
    actions = ["ec2:RunInstances", "ec2:CreateSecurityGroup"]
    resources = [
      "arn:aws:ec2:*:*:instance/*",
      "arn:aws:ec2:*:*:volume/*",
      "arn:aws:ec2:*:*:security-group/*",
    ]

    condition {
      test     = "StringEquals"
      variable = local.request_tag
      values   = ["vm-database"]
    }
  }

  # RunInstances and CreateSecurityGroup also authorize against the existing
  # resources they reference. Those are not created here, so no tag is demanded.
  statement {
    sid     = "UseExistingNetwork"
    actions = ["ec2:RunInstances", "ec2:CreateSecurityGroup"]
    resources = [
      "arn:aws:ec2:*::image/*",
      "arn:aws:ec2:*:*:network-interface/*",
      "arn:aws:ec2:*:*:subnet/*",
      "arn:aws:ec2:*:*:vpc/*",
    ]
  }

  statement {
    sid       = "UseOwnSecurityGroups"
    actions   = ["ec2:RunInstances"]
    resources = ["arn:aws:ec2:*:*:security-group/*"]

    condition {
      test     = "StringEquals"
      variable = local.template_tag
      values   = ["vm-database"]
    }
  }

  statement {
    sid       = "TagOnCreateOnly"
    actions   = ["ec2:CreateTags"]
    resources = ["arn:aws:ec2:*:*:*/*"]

    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values = [
        "RunInstances",
        "CreateSecurityGroup",
        "AuthorizeSecurityGroupIngress",
        "AuthorizeSecurityGroupEgress",
      ]
    }
  }

  statement {
    sid = "ManageOwnEc2Resources"
    actions = [
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:DeleteSecurityGroup",
      "ec2:ModifyInstanceAttribute",
      "ec2:ModifyInstanceMetadataOptions",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:StartInstances",
      "ec2:StopInstances",
      "ec2:TerminateInstances",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = local.template_tag
      values   = ["vm-database"]
    }
  }

  statement {
    sid       = "PassOnlyTheEnvironmentInstanceRole"
    actions   = ["iam:PassRole"]
    resources = [var.instance_role_arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ec2.amazonaws.com"]
    }
  }

  statement {
    sid       = "CreateTaggedDatabases"
    actions   = ["rds:CreateDBInstance", "rds:CreateDBSubnetGroup", "rds:AddTagsToResource"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = local.request_tag
      values   = ["vm-database"]
    }
  }

  statement {
    sid = "ManageOwnDatabases"
    actions = [
      "rds:CreateDBInstance",
      "rds:DeleteDBInstance",
      "rds:DeleteDBSubnetGroup",
      "rds:ModifyDBInstance",
      "rds:ModifyDBSubnetGroup",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = local.template_tag
      values   = ["vm-database"]
    }
  }

  # manage_master_user_password makes RDS create its secret with the caller's
  # permissions. RDS names these secrets "rds!db-<id>".
  statement {
    sid = "RdsManagedSecrets"
    actions = [
      "secretsmanager:CreateSecret",
      "secretsmanager:DeleteSecret",
      "secretsmanager:DescribeSecret",
      "secretsmanager:TagResource",
    ]
    resources = ["arn:aws:secretsmanager:*:*:secret:rds!*"]
  }
}

resource "aws_iam_role" "this" {
  name                 = var.role_name
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
  tags                 = var.tags
}

resource "aws_iam_role_policy" "this" {
  name   = "provision-vm-database"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.permissions.json
}
