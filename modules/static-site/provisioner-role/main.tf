# Role the portal worker assumes while running the static-site template. It can manage
# buckets that follow the template's naming scheme and CloudFront distributions that
# carry the template's tag, and nothing else.

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
    sid = "SiteBuckets"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:DeleteBucketPolicy",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
      "s3:Get*",
      "s3:ListBucket",
      "s3:ListBucketVersions",
      "s3:PutBucketPolicy",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutBucketTagging",
      "s3:PutEncryptionConfiguration",
    ]
    # The module names every bucket "<environment>-site".
    resources = ["arn:aws:s3:::*-site", "arn:aws:s3:::*-site/*"]
  }

  statement {
    sid = "ReadCloudFront"
    actions = [
      "cloudfront:GetCachePolicy",
      "cloudfront:GetDistribution",
      "cloudfront:GetOriginAccessControl",
      "cloudfront:ListCachePolicies",
      "cloudfront:ListTagsForResource",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "CreateTaggedDistributions"
    actions   = ["cloudfront:CreateDistribution", "cloudfront:CreateDistributionWithTags", "cloudfront:TagResource"]
    resources = ["arn:aws:cloudfront::*:distribution/*"]

    condition {
      test     = "StringEquals"
      variable = local.request_tag
      values   = ["static-site"]
    }
  }

  statement {
    sid       = "ManageOwnDistributions"
    actions   = ["cloudfront:UpdateDistribution", "cloudfront:DeleteDistribution"]
    resources = ["arn:aws:cloudfront::*:distribution/*"]

    condition {
      test     = "StringEquals"
      variable = local.template_tag
      values   = ["static-site"]
    }
  }

  # Origin access controls cannot be tagged, so they cannot be scoped by tag.
  statement {
    sid = "OriginAccessControls"
    actions = [
      "cloudfront:CreateOriginAccessControl",
      "cloudfront:DeleteOriginAccessControl",
      "cloudfront:UpdateOriginAccessControl",
    ]
    resources = ["arn:aws:cloudfront::*:origin-access-control/*"]
  }
}

resource "aws_iam_role" "this" {
  name                 = var.role_name
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
  tags                 = var.tags
}

resource "aws_iam_role_policy" "this" {
  name   = "provision-static-site"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.permissions.json
}
