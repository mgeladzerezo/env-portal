variable "environment_id" {
  description = "Portal ID of the environment."
  type        = string
}

variable "name" {
  description = "Unique name used as a prefix for resources."
  type        = string
}

variable "owner" {
  description = "User who requested the environment."
  type        = string
}

variable "expires_at" {
  description = "RFC 3339 timestamp after which the portal destroys the environment."
  type        = string
}

variable "size" {
  description = "Template size: small, medium or large."
  type        = string

  validation {
    condition     = contains(["small", "medium", "large"], var.size)
    error_message = "size must be small, medium or large."
  }
}
