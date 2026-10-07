variable "region" {
  description = "AWS region for the portal's resources."
  type        = string
  default     = "eu-central-1"
}

variable "name" {
  description = "Prefix for resource names."
  type        = string
  default     = "env-portal"
}

variable "oidc_provider_arn" {
  description = "ARN of the EKS cluster's IAM OIDC provider (for IRSA)."
  type        = string
}

variable "oidc_issuer" {
  description = "OIDC issuer of the cluster without https://, e.g. oidc.eks.eu-central-1.amazonaws.com/id/ABC123."
  type        = string
}

variable "namespace" {
  description = "Kubernetes namespace the portal is installed in."
  type        = string
  default     = "env-portal"
}

variable "worker_service_account" {
  description = "Service account name of the worker. Must match the Helm chart."
  type        = string
  default     = "env-portal-worker"
}

variable "cluster_name" {
  description = "EKS cluster that hosts namespaces created by the k8s-namespace template."
  type        = string
}

variable "environment_instance_role_arn" {
  description = "Role behind the instance profile used by vm-database environments."
  type        = string
}

variable "github_repository" {
  description = "owner/repo allowed to push images through GitHub OIDC. Leave empty to skip the CI role."
  type        = string
  default     = ""
}

variable "github_oidc_provider_arn" {
  description = "ARN of the account's GitHub Actions OIDC provider. Required when github_repository is set."
  type        = string
  default     = ""
}
