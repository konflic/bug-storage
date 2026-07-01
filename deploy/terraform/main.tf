# Yandex Cloud infrastructure for the Bug Database API.
#
# Provisions:
#   - a Compute VM (Ubuntu 22.04) with Docker (via cloud-init)
#   - a secondary SSD disk mounted at /srv/data for persistent bug data
#   - a security group: 443 + 80 open to the world, 22 restricted to your IP
#
# It does NOT deploy the app itself — that's done over SSH with `make deploy`.
# See ../../DEPLOY.md.

terraform {
  required_version = ">= 1.3"
  required_providers {
    yandex = {
      source  = "yandex-cloud/yandex"
      version = ">= 0.100"
    }
  }
}

provider "yandex" {
  cloud_id  = var.cloud_id
  folder_id = var.folder_id
  zone      = var.zone
  # Auth via `yc` CLI token or service-account key; see DEPLOY.md.
}

# --- Network -----------------------------------------------------------------
resource "yandex_vpc_network" "net" {
  name = "${var.name}-net"
}

resource "yandex_vpc_subnet" "subnet" {
  name           = "${var.name}-subnet"
  zone           = var.zone
  network_id     = yandex_vpc_network.net.id
  v4_cidr_blocks = ["10.10.0.0/24"]
}

resource "yandex_vpc_security_group" "sg" {
  name       = "${var.name}-sg"
  network_id = yandex_vpc_network.net.id

  ingress {
    protocol       = "TCP"
    description    = "HTTPS"
    port           = 443
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    protocol       = "TCP"
    description    = "HTTP (ACME / redirect)"
    port           = 80
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    protocol       = "TCP"
    description    = "SSH (restricted)"
    port           = 22
    v4_cidr_blocks = var.ssh_allowed_cidrs
  }
  egress {
    protocol       = "ANY"
    description    = "allow all outbound"
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- Image -------------------------------------------------------------------
data "yandex_compute_image" "ubuntu" {
  family = "ubuntu-2204-lts"
}

# --- Persistent data disk (survives VM recreation) ---------------------------
resource "yandex_compute_disk" "data" {
  name = "${var.name}-data"
  type = "network-ssd"
  zone = var.zone
  size = var.data_disk_gb
}

# --- VM ----------------------------------------------------------------------
resource "yandex_compute_instance" "vm" {
  name        = var.name
  platform_id = "standard-v3"
  zone        = var.zone

  resources {
    cores  = var.cores
    memory = var.memory_gb
  }

  boot_disk {
    initialize_params {
      image_id = data.yandex_compute_image.ubuntu.id
      size     = var.boot_disk_gb
      type     = "network-ssd"
    }
  }

  secondary_disk {
    disk_id     = yandex_compute_disk.data.id
    auto_delete = false
    device_name = "data"
  }

  network_interface {
    subnet_id          = yandex_vpc_subnet.subnet.id
    nat                = true
    security_group_ids = [yandex_vpc_security_group.sg.id]
  }

  metadata = {
    ssh-keys  = "${var.ssh_user}:${file(var.ssh_public_key_path)}"
    user-data = file("${path.module}/cloud-init.yaml")
  }
}
