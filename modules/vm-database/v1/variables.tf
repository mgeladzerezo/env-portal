variable "environment_id" {
  description = "Portal ID of the environment."
  type        = string
}

variable "name" {
  description = "Unique name used as a prefix for resources."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,38}[a-z0-9]$", var.name))
    error_message = "name must be 3-40 lowercase letters, digits or dashes."
  }
}

variable "owner" {
  description = "User who requested the environment."
  type        = string
}

variable "expires_at" {
  description = "RFC 3339 timestamp after which the portal destroys the environment."
  type        = string

  validation {
    condition     = can(timeadd(var.expires_at, "0s"))
    error_message = "expires_at must be an RFC 3339 timestamp."
  }
}

variable "size" {
  description = "Template size: small, medium or large."
  type        = string

  validation {
    condition     = contains(["small", "medium", "large"], var.size)
    error_message = "size must be small, medium or large."
  }
}

variable "vpc_id" {
  description = "VPC the environment is placed in."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets (at least two availability zones) for the VM and the database."
  type        = list(string)

  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "RDS needs subnets in at least two availability zones."
  }
}

variable "instance_profile_name" {
  description = "Existing instance profile for the VM (SSM access). The module does not create IAM roles."
  type        = string
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}
