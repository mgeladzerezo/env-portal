output "bucket_name" {
  description = "Bucket to upload the site content to."
  value       = aws_s3_bucket.site.bucket
}

output "distribution_id" {
  description = "CloudFront distribution ID, needed for cache invalidations."
  value       = aws_cloudfront_distribution.site.id
}

output "site_url" {
  description = "Public HTTPS URL of the site."
  value       = "https://${aws_cloudfront_distribution.site.domain_name}"
}
