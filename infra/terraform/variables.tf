variable "tenancy_ocid" { type = string }
variable "user_ocid" { type = string }
variable "fingerprint" { type = string }
variable "private_key_path" { type = string }
variable "compartment_ocid" { type = string }

variable "region" {
  type    = string
  default = "us-ashburn-1"
}

variable "shape" {
  description = "Always Free ARM shape"
  type        = string
  default     = "VM.Standard.A1.Flex"
}

variable "ocpus" {
  type    = number
  default = 4
  validation {
    condition     = var.ocpus >= 1 && var.ocpus <= 4
    error_message = "The Always Free allowance is 4 OCPUs."
  }
}

variable "memory_gb" {
  type    = number
  default = 24
  validation {
    condition     = var.memory_gb >= 16 && var.memory_gb <= 24
    error_message = "The stack needs 16 GB; the Always Free allowance is 24 GB."
  }
}

variable "ssh_public_key" {
  description = "Contents of the public key allowed to SSH to the VM"
  type        = string
}

variable "admin_cidr" {
  description = "The one address allowed to SSH (your address /32)"
  type        = string
  validation {
    condition     = can(cidrhost(var.admin_cidr, 0)) && var.admin_cidr != "0.0.0.0/0"
    error_message = "Give a specific CIDR, not the whole internet."
  }
}

variable "repo_url" {
  type    = string
  default = "https://github.com/Tajaddin/finops-integration-hub.git"
}

variable "git_ref" {
  description = "Tag or commit to deploy"
  type        = string
  default     = "v1.0.0"
}

variable "triage_model" {
  description = "The VM has no GPU: a small model on the CPU (qwen2.5:3b) or `fake` for the deterministic stand-in"
  type        = string
  default     = "qwen2.5:3b"
}

variable "webhook_domain" {
  description = "A DNS name pointing at the VM to expose the two webhook paths over HTTPS; empty keeps everything private"
  type        = string
  default     = ""
}
