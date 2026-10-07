# The worker pod's identity (IRSA). On its own it can only read and write environment
# state and assume the per-template provisioner roles; every permission to create
# infrastructure lives in those roles.

data "aws_iam_policy_document" "worker_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_issuer}:sub"
      values   = ["system:serviceaccount:${var.namespace}:${var.worker_service_account}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_issuer}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "worker" {
  name               = "${var.name}-worker"
  assume_role_policy = data.aws_iam_policy_document.worker_trust.json
}

module "provisioner_static_site" {
  source = "../../modules/static-site/provisioner-role"

  role_name         = "${var.name}-provision-static-site"
  trusted_role_arns = [aws_iam_role.worker.arn]
}

module "provisioner_vm_database" {
  source = "../../modules/vm-database/provisioner-role"

  role_name         = "${var.name}-provision-vm-database"
  trusted_role_arns = [aws_iam_role.worker.arn]
  instance_role_arn = var.environment_instance_role_arn
}

module "provisioner_k8s_namespace" {
  source = "../../modules/k8s-namespace/provisioner-role"

  role_name         = "${var.name}-provision-k8s-namespace"
  trusted_role_arns = [aws_iam_role.worker.arn]
  cluster_name      = var.cluster_name
}

locals {
  provisioner_role_arns = {
    "static-site"   = module.provisioner_static_site.role_arn
    "vm-database"   = module.provisioner_vm_database.role_arn
    "k8s-namespace" = module.provisioner_k8s_namespace.role_arn
  }
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid       = "ListEnvironmentState"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["environments/*"]
    }
  }

  statement {
    sid       = "ReadWriteEnvironmentState"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/environments/*"]
  }

  statement {
    sid       = "StateLocks"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.locks.arn]
  }

  statement {
    sid       = "AssumeProvisionerRoles"
    actions   = ["sts:AssumeRole"]
    resources = values(local.provisioner_role_arns)
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "state-and-provisioner-roles"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

# Optional role for the CI pipeline: push images, nothing else.

locals {
  create_ci_role = var.github_repository != ""
}

data "aws_iam_policy_document" "ci_trust" {
  count = local.create_ci_role ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/main"]
    }
  }
}

data "aws_iam_policy_document" "ci" {
  count = local.create_ci_role ? 1 : 0

  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "PushImages"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [for repository in aws_ecr_repository.this : repository.arn]
  }
}

resource "aws_iam_role" "ci" {
  count = local.create_ci_role ? 1 : 0

  name               = "${var.name}-ci"
  assume_role_policy = data.aws_iam_policy_document.ci_trust[0].json
}

resource "aws_iam_role_policy" "ci" {
  count = local.create_ci_role ? 1 : 0

  name   = "push-images"
  role   = aws_iam_role.ci[0].id
  policy = data.aws_iam_policy_document.ci[0].json
}
