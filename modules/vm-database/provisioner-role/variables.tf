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

variable "instance_role_arn" {
  description = "Role behind the instance profile that environment VMs use. The only role that may be passed."
  type        = string
}
