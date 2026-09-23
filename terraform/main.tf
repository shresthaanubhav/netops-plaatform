terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

resource "aws_vpc" "netops_vpc" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "netops-vpc"
  }
}

resource "aws_subnet" "netops_subnet" {
  vpc_id                  = aws_vpc.netops_vpc.id
  cidr_block              = "10.0.1.0/24"
  map_public_ip_on_launch = true
  availability_zone       = "${var.aws_region}a"

  tags = {
    Name = "netops-subnet"
  }
}

resource "aws_internet_gateway" "netops_igw" {
  vpc_id = aws_vpc.netops_vpc.id

  tags = {
    Name = "netops-igw"
  }
}

resource "aws_route_table" "netops_rt" {
  vpc_id = aws_vpc.netops_vpc.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.netops_igw.id
  }

  tags = {
    Name = "netops-rt"
  }
}

resource "aws_route_table_association" "netops_rta" {
  subnet_id      = aws_subnet.netops_subnet.id
  route_table_id = aws_route_table.netops_rt.id
}

resource "aws_security_group" "netops_sg" {
  name        = "netops-sg"
  description = "Allow SSH, Grafana, Prometheus"
  vpc_id      = aws_vpc.netops_vpc.id

  ingress {
    description = "SSH from my IP only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.my_ip]
  }

  ingress {
    description = "Grafana"
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = [var.my_ip]
  }

  ingress {
    description = "Prometheus"
    from_port   = 9090
    to_port     = 9090
    protocol    = "tcp"
    cidr_blocks = [var.my_ip]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "netops-sg"
  }
}

resource "aws_instance" "netops_node" {
  ami                    = "ami-0c101f26f147fa7fd"
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.netops_subnet.id
  vpc_security_group_ids = [aws_security_group.netops_sg.id]

  tags = {
    Name = "netops-node"
  }
}
