output "public_ip" {
  description = "Public IP of the VM. Point your DOMAIN's A record here."
  value       = yandex_compute_instance.vm.network_interface.0.nat_ip_address
}

output "ssh" {
  description = "Ready-to-use SSH command."
  value       = "ssh ${var.ssh_user}@${yandex_compute_instance.vm.network_interface.0.nat_ip_address}"
}
