output "instance_id" {
  description = "EC2 instance ID. Connect with `aws ssm start-session --target <id>`."
  value       = aws_instance.vm.id
}

output "private_ip" {
  description = "Private IP address of the VM."
  value       = aws_instance.vm.private_ip
}

output "db_endpoint" {
  description = "host:port of the PostgreSQL instance."
  value       = aws_db_instance.this.endpoint
}

output "db_name" {
  description = "Name of the initial database."
  value       = aws_db_instance.this.db_name
}

output "db_secret_arn" {
  description = "Secrets Manager secret that holds the database credentials."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}
