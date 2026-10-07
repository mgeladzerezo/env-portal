output "role_arn" {
  description = "ARN the portal worker assumes to provision this template."
  value       = aws_iam_role.this.arn
}
