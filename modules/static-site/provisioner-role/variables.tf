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
