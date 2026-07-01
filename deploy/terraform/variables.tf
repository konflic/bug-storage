variable "cloud_id" {
  type        = string
  description = "Yandex Cloud cloud id."
}

variable "folder_id" {
  type        = string
  description = "Yandex Cloud folder id."
}

variable "zone" {
  type        = string
  description = "Availability zone."
  default     = "ru-central1-a"
}

variable "name" {
  type        = string
  description = "Base name for created resources."
  default     = "bugdb"
}

variable "cores" {
  type        = number
  description = "vCPU count."
  default     = 2
}

variable "memory_gb" {
  type        = number
  description = "RAM in GB."
  default     = 2
}

variable "boot_disk_gb" {
  type        = number
  description = "Boot disk size in GB."
  default     = 20
}

variable "data_disk_gb" {
  type        = number
  description = "Persistent data disk size in GB (holds the SQLite DB)."
  default     = 10
}

variable "ssh_user" {
  type        = string
  description = "Login user created on the VM."
  default     = "ubuntu"
}

variable "ssh_public_key_path" {
  type        = string
  description = "Path to your SSH public key (e.g. ~/.ssh/id_ed25519.pub)."
}

variable "ssh_allowed_cidrs" {
  type        = list(string)
  description = "CIDRs allowed to SSH (port 22). Lock this to your IP."
  default     = ["0.0.0.0/0"]
}
