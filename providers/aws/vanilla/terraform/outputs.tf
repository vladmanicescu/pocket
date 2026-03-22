output "k8s_public_ips" {
  value = {
    for name, instance in aws_instance.nodes : name => instance.public_ip
  }
}

output "k8s_private_ips" {
  value = {
    for name, instance in aws_instance.nodes : name => instance.private_ip
  }
}

output "nfs_public_ip" {
  value = aws_instance.nfs_srv.public_ip
}

output "nfs_private_ip" {
  value = aws_instance.nfs_srv.private_ip
}

output "gitlab_public_ip" {
  value = aws_instance.gitlab_srv.public_ip
}

output "gitlab_private_ip" {
  value = aws_instance.gitlab_srv.private_ip
}

output "ssh_private_key_path" {
  value = local_file.private_key_pem.filename
}

output "ansible_inventory_path" {
  value = local_file.ansible_inventory.filename
}