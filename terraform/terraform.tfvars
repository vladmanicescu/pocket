aws_region = "eu-central-1"

instance_type = "t3.medium"

vpc_cidr = "172.31.0.0/16"

subnet_cidr = "172.31.1.0/24"

vm_definitions = [
  {
    name            = "k8s-cp1"
    hostname        = "k8s-cp1"
    private_ip      = "172.31.1.11"
    gateway         = "172.31.1.1"
    extra_disk_size = 3
  },
  {
    name            = "k8s-w1"
    hostname        = "k8s-w1"
    private_ip      = "172.31.1.12"
    gateway         = "172.31.1.1"
    extra_disk_size = 3
  },
  {
    name            = "k8s-w2"
    hostname        = "k8s-w2"
    private_ip      = "172.31.1.13"
    gateway         = "172.31.1.1"
    extra_disk_size = 3
  }
]