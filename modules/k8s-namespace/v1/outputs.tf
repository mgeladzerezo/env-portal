output "namespace" {
  description = "Name of the namespace."
  value       = kubernetes_namespace_v1.this.metadata[0].name
}

output "cpu_quota" {
  description = "Total CPU the namespace may request."
  value       = local.quota.cpu
}

output "memory_quota" {
  description = "Total memory the namespace may request."
  value       = local.quota.memory
}
