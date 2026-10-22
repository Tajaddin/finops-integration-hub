# One Oracle Cloud Always Free ARM VM (VM.Standard.A1.Flex, 4 OCPUs, 24 GB) running the whole Compose stack:
# every image in docker-compose.yml is published for arm64. Terraform creates the network and the instance; the
# cloud-init clones this repository at a tag, generates every local secret ON the VM (`make env`, so no secret
# passes through Terraform state), and starts the stack. Only SSH (from one address) and, if a domain is given,
# HTTPS for the two webhook paths are open; everything else is reached through an SSH tunnel.
#
# Not applied: needs the owner's OCI account (docs/DEPLOY.md). Nothing here costs money inside the free limits.

terraform {
  required_version = ">= 1.9"
  required_providers {
    oci = { source = "oracle/oci", version = "~> 6.0" }
  }
}

provider "oci" {
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
  region           = var.region
}

locals {
  name = "finops-hub"
  tags = { project = "finops-integration-hub", managed_by = "terraform" }
}

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.compartment_ocid
}

data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = var.shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

resource "oci_core_vcn" "vcn" {
  compartment_id = var.compartment_ocid
  display_name   = "${local.name}-vcn"
  cidr_blocks    = ["10.42.0.0/16"]
  dns_label      = "finops"
  freeform_tags  = local.tags
}

resource "oci_core_internet_gateway" "igw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.vcn.id
  display_name   = "${local.name}-igw"
  freeform_tags  = local.tags
}

resource "oci_core_route_table" "rt" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.vcn.id
  display_name   = "${local.name}-rt"
  freeform_tags  = local.tags
  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_internet_gateway.igw.id
  }
}

resource "oci_core_security_list" "sl" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.vcn.id
  display_name   = "${local.name}-sl"
  freeform_tags  = local.tags

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }
  ingress_security_rules {
    description = "SSH from the administrator's address only"
    protocol    = "6"
    source      = var.admin_cidr
    tcp_options {
      min = 22
      max = 22
    }
  }
  dynamic "ingress_security_rules" {
    for_each = toset(var.webhook_domain == "" ? [] : ["https"])
    content {
      description = "HTTPS for the CRM webhook and the n8n intake webhook (Caddy, Let's Encrypt)"
      protocol    = "6"
      source      = "0.0.0.0/0"
      tcp_options {
        min = 443
        max = 443
      }
    }
  }
}

resource "oci_core_subnet" "subnet" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.vcn.id
  display_name               = "${local.name}-public"
  cidr_block                 = "10.42.1.0/24"
  route_table_id             = oci_core_route_table.rt.id
  security_list_ids          = [oci_core_security_list.sl.id]
  dns_label                  = "pub"
  prohibit_public_ip_on_vnic = false
  freeform_tags              = local.tags
}

resource "oci_core_instance" "vm" {
  compartment_id      = var.compartment_ocid
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  display_name        = "${local.name}-vm"
  shape               = var.shape
  freeform_tags       = local.tags

  shape_config {
    ocpus         = var.ocpus
    memory_in_gbs = var.memory_gb
  }
  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ubuntu.images[0].id
    boot_volume_size_in_gbs = 100
  }
  create_vnic_details {
    subnet_id        = oci_core_subnet.subnet.id
    assign_public_ip = true
    hostname_label   = "finops"
  }
  instance_options {
    are_legacy_imds_endpoints_disabled = true
  }
  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data = base64encode(templatefile("${path.module}/cloud-init.yaml", {
      repo_url       = var.repo_url
      git_ref        = var.git_ref
      triage_model   = var.triage_model
      webhook_domain = var.webhook_domain
    }))
  }
}
