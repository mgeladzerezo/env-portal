output "summary" {
  description = "Human-readable description of what was created."
  value       = "${var.name}: ${length(terraform_data.replica)} placeholder resource(s) for ${var.owner}"
}

output "replicas" {
  description = "Names of the placeholder resources."
  value       = [for replica in terraform_data.replica : replica.output.name]
}
