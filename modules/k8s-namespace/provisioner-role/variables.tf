variable "role_name" {
  description = "Name of the provisioner role."
  type        = string
}

variable "trusted_role_arns" {
  description = "Roles allowed to assume this one (the portal worker)."
  type        = list(string)
}

variable "tags" {
  description = "Tags for the role."
  type        = map(string)
  default     = {}
}

variable "cluster_name" {
  description = "EKS cluster that hosts environment namespaces."
  type        = string
}

variable "kubernetes_group" {
  description = "Kubernetes group the role is mapped to. A ClusterRole bound to this group defines what it may do."
  type        = string
  default     = "env-portal-provisioners"
}
