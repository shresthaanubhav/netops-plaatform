variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "ap-southeast-2"
}

variable "instance_type" {
  description = "EC2 instance size"
  type        = string
  default     = "t3.micro"
}

variable "my_ip" {
  description = "Your public IP, for SSH access — CIDR format, e.g. 1.2.3.4/32"
  type        = string
}
