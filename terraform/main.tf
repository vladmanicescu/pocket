terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile
}

data "aws_ssm_parameter" "al2023_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "tls_private_key" "this" {
  algorithm = "ED25519"
}

resource "local_file" "private_key_pem" {
  content         = tls_private_key.this.private_key_openssh
  filename        = "${path.module}/${var.key_name}.pem"
  file_permission = "0600"
}

resource "aws_key_pair" "this" {
  key_name   = var.key_name
  public_key = tls_private_key.this.public_key_openssh
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.project_name}-vpc"
  }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.project_name}-igw"
  }
}

resource "aws_subnet" "this" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.subnet_cidr
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.project_name}-subnet"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = {
    Name = "${var.project_name}-public-rt"
  }
}

resource "aws_route_table_association" "this" {
  subnet_id      = aws_subnet.this.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "nodes" {
  name        = "${var.project_name}-sg"
  description = "Security group for Kubernetes, NFS and GitLab hosts"
  vpc_id      = aws_vpc.this.id

  lifecycle {
    create_before_destroy = true
  }

  ingress {
    description = "SSH access"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidrs
  }

  ingress {
    description = "ICMP access"
    from_port   = -1
    to_port     = -1
    protocol    = "icmp"
    cidr_blocks = var.allowed_ssh_cidrs
  }

  ingress {
    description = "Allow all traffic between hosts in the same security group"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidrs
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidrs
  }

  ingress {
    description = "Kubernetes API"
    from_port   = 6443
    to_port     = 6443
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidrs
  }

  ingress {
    description = "NFS"
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  ingress {
    description = "GitLab Container Registry"
    from_port   = 5050
    to_port     = 5050
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidrs
  }

  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-sg"
  }
}

locals {
  vm_map = {
    for vm in var.vm_definitions : vm.name => vm
  }

  ansible_inventory = <<-EOT
[k8s]
%{ for vm_name, instance in aws_instance.nodes ~}
${vm_name} ansible_host=${instance.public_ip}
%{ endfor ~}

[nfs]
nfs-srv ansible_host=${aws_instance.nfs_srv.public_ip}

[gitlab]
gitlab-srv ansible_host=${aws_instance.gitlab_srv.public_ip}

[k8s:vars]
ansible_user=ec2-user
ansible_ssh_private_key_file=../terraform/${var.key_name}.pem
ansible_python_interpreter=/usr/bin/python3

[nfs:vars]
ansible_user=ec2-user
ansible_ssh_private_key_file=../terraform/${var.key_name}.pem
ansible_python_interpreter=/usr/bin/python3

[gitlab:vars]
ansible_user=ec2-user
ansible_ssh_private_key_file=../terraform/${var.key_name}.pem
ansible_python_interpreter=/usr/bin/python3
EOT
}

resource "aws_instance" "nodes" {
  for_each = local.vm_map

  ami                    = data.aws_ssm_parameter.al2023_ami.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.this.id
  vpc_security_group_ids = [aws_security_group.nodes.id]
  key_name               = aws_key_pair.this.key_name
  private_ip             = each.value.private_ip

  user_data = <<-EOF
              #cloud-config
              preserve_hostname: false
              hostname: ${each.value.hostname}
              fqdn: ${each.value.hostname}
              manage_etc_hosts: true
              EOF

  root_block_device {
    volume_size           = var.root_volume_size
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Name     = each.value.name
    Hostname = each.value.hostname
    Gateway  = try(each.value.gateway, "")
    Role     = each.value.name == "k8s-cp1" ? "control-plane" : "worker"
  }
}

resource "aws_ebs_volume" "extra" {
  for_each = local.vm_map

  availability_zone = aws_instance.nodes[each.key].availability_zone
  size              = each.value.extra_disk_size
  type              = "gp3"

  tags = {
    Name = "${each.value.name}-extra-disk"
  }
}

resource "aws_volume_attachment" "extra" {
  for_each = local.vm_map

  device_name  = "/dev/sdf"
  volume_id    = aws_ebs_volume.extra[each.key].id
  instance_id  = aws_instance.nodes[each.key].id
  force_detach = true
}

resource "aws_instance" "nfs_srv" {
  ami                    = data.aws_ssm_parameter.al2023_ami.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.this.id
  vpc_security_group_ids = [aws_security_group.nodes.id]
  key_name               = aws_key_pair.this.key_name
  private_ip             = cidrhost(var.subnet_cidr, 20)

  user_data = <<-EOF
              #cloud-config
              preserve_hostname: false
              hostname: nfs-srv
              fqdn: nfs-srv
              manage_etc_hosts: true
              EOF

  root_block_device {
    volume_size           = 30
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Name = "nfs-srv"
    Role = "nfs-server"
  }
}

resource "aws_instance" "gitlab_srv" {
  ami                    = data.aws_ssm_parameter.al2023_ami.value
  instance_type          = "t3.large"
  subnet_id              = aws_subnet.this.id
  vpc_security_group_ids = [aws_security_group.nodes.id]
  key_name               = aws_key_pair.this.key_name
  private_ip             = cidrhost(var.subnet_cidr, 30)

  user_data = <<-EOF
              #cloud-config
              preserve_hostname: false
              hostname: gitlab-srv
              fqdn: gitlab-srv
              manage_etc_hosts: true
              EOF

  root_block_device {
    volume_size           = 50
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Name = "gitlab-srv"
    Role = "gitlab"
  }
}

resource "local_file" "ansible_inventory" {
  content         = local.ansible_inventory
  filename        = "${path.module}/../ansible/inventory.ini"
  file_permission = "0644"
}