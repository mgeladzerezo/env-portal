locals {
  sizes = {
    small  = { instance_type = "t3.micro", db_class = "db.t4g.micro", db_storage_gb = 20 }
    medium = { instance_type = "t3.small", db_class = "db.t4g.small", db_storage_gb = 50 }
    large  = { instance_type = "t3.medium", db_class = "db.t4g.medium", db_storage_gb = 100 }
  }
  size = local.sizes[var.size]

  tags = merge(var.tags, {
    "Name"                  = var.name
    "owner"                 = var.owner
    "expires-at"            = var.expires_at
    "portal-environment-id" = var.environment_id
    "portal-template"       = "vm-database"
  })
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# No inbound rule at all: the VM is reached through SSM Session Manager.
resource "aws_security_group" "vm" {
  name        = "${var.name}-vm"
  description = "env-portal ${var.name} VM"
  vpc_id      = var.vpc_id
  tags        = local.tags
}

resource "aws_vpc_security_group_egress_rule" "vm_https" {
  security_group_id = aws_security_group.vm.id
  description       = "HTTPS out (package repositories, SSM)"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
  tags              = local.tags
}

resource "aws_vpc_security_group_egress_rule" "vm_to_db" {
  security_group_id            = aws_security_group.vm.id
  description                  = "PostgreSQL to the environment database"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.db.id
  tags                         = local.tags
}

resource "aws_security_group" "db" {
  name        = "${var.name}-db"
  description = "env-portal ${var.name} database"
  vpc_id      = var.vpc_id
  tags        = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "db_from_vm" {
  security_group_id            = aws_security_group.db.id
  description                  = "PostgreSQL from the environment VM only"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.vm.id
  tags                         = local.tags
}

resource "aws_instance" "vm" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = local.size.instance_type
  subnet_id              = var.subnet_ids[0]
  vpc_security_group_ids = [aws_security_group.vm.id]
  iam_instance_profile   = var.instance_profile_name
  tags                   = local.tags
  volume_tags            = local.tags

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 20
    encrypted   = true
  }

  lifecycle {
    # A newer AMI must not replace the VM of an environment that is already running.
    ignore_changes = [ami]
  }
}

resource "aws_db_subnet_group" "this" {
  name       = var.name
  subnet_ids = var.subnet_ids
  tags       = local.tags
}

resource "aws_db_instance" "this" {
  identifier     = var.name
  engine         = "postgres"
  engine_version = "16"
  instance_class = local.size.db_class

  allocated_storage = local.size.db_storage_gb
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = "app"
  username = "app"
  # RDS generates the password and keeps it in Secrets Manager, so it never appears in
  # Terraform state or in the portal's database.
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = false

  # Short-lived by design: no backups to keep and nothing that blocks a destroy.
  backup_retention_period  = 0
  skip_final_snapshot      = true
  delete_automated_backups = true
  deletion_protection      = false
  apply_immediately        = true

  tags = local.tags
}
