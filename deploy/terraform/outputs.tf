output "state_bucket" {
  description = "Value for PORTAL_STATE_BUCKET."
  value       = aws_s3_bucket.state.bucket
}

output "state_lock_table" {
  description = "Value for PORTAL_STATE_LOCK_TABLE."
  value       = aws_dynamodb_table.locks.name
}

output "worker_role_arn" {
  description = "Value for worker.serviceAccount.roleArn in the Helm chart."
  value       = aws_iam_role.worker.arn
}

output "provisioner_role_arns" {
  description = "Value for PORTAL_PROVISIONER_ROLE_ARNS (template name -> role ARN)."
  value       = local.provisioner_role_arns
}

output "ecr_repository_urls" {
  description = "Image repositories by component."
  value       = { for name, repository in aws_ecr_repository.this : name => repository.repository_url }
}

output "ci_role_arn" {
  description = "Role for GitHub Actions to push images, if enabled."
  value       = one(aws_iam_role.ci[*].arn)
}
