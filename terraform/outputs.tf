output "instance_public_ip" {
  description = "Public IP of the EC2 instance"
  value       = aws_instance.netops_node.public_ip
}

output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.netops_node.id
}
