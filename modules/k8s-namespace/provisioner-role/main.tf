# Role the portal worker assumes while running the k8s-namespace template. On the AWS
# side it can only look up the cluster. Inside the cluster it is mapped to a Kubernetes
# group; the ClusterRole bound to that group (shipped in the portal's Helm chart)
# limits it to namespaces, quotas, limit ranges, network policies and role bindings.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  cluster_arn = "arn:aws:eks:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:cluster/${var.cluster_name}"
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
    sid       = "FindTheCluster"
    actions   = ["eks:DescribeCluster"]
    resources = [local.cluster_arn]
  }
}

resource "aws_iam_role" "this" {
  name                 = var.role_name
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
  tags                 = var.tags
}

resource "aws_iam_role_policy" "this" {
  name   = "provision-k8s-namespace"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.permissions.json
}

resource "aws_eks_access_entry" "this" {
  cluster_name      = var.cluster_name
  principal_arn     = aws_iam_role.this.arn
  kubernetes_groups = [var.kubernetes_group]
  type              = "STANDARD"
  tags              = var.tags
}
