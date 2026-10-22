output "public_ip" {
  description = "SSH as ubuntu@ this address; tunnel the UIs (e.g. ssh -L 3000:127.0.0.1:3000 for Langfuse)"
  value       = oci_core_instance.vm.public_ip
}

output "webhook_urls" {
  description = "Where EspoCRM and the e-mail gateway post, when a webhook domain is set"
  value = var.webhook_domain == "" ? null : {
    espocrm_webhook = "https://${var.webhook_domain}/webhooks/espocrm"
    billing_intake  = "https://${var.webhook_domain}/webhook/billing-message"
  }
}
