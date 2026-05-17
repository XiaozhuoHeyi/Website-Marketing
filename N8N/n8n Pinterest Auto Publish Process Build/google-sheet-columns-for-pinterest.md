# Google Sheet Columns For Pinterest Publisher

Your current product crawl sheet already has the core product columns. Add the
Pinterest columns below once, as new headers to the right of the existing sheet.

## Minimum New Columns

```text
pinterest_channel_id
pinterest_board_service_id
pin_status
pin_priority
buffer_mode
scheduled_time_utc
pin_title
pin_description
recommended_alt_text
pin_image_url
destination_url
utm_content
attempt_count
last_attempt_at
posted_at
buffer_post_id
buffer_status
buffer_due_at
buffer_error
buffer_response_json
published_image_urls
pin_description_history
```

## Values To Fill Manually

Fill these before publishing:

```text
pinterest_board_service_id
```

Optional:

```text
pinterest_channel_id
pin_image_url
pin_priority
buffer_mode
scheduled_time_utc
```

If `pinterest_channel_id` is blank, the publisher uses `buffer_channel_1_id`.
If `pin_image_url` is filled, the workflow uses that image first for the next post.
If `buffer_mode` is blank, the publisher uses `addToQueue`.
Use `customScheduled` plus `scheduled_time_utc` only when you need an exact post
time. Otherwise Buffer queue scheduling is cleaner.

## How The Post Image Is Selected

The workflow automatically chooses the image in this order:

```text
1. primary_image
2. pin_image_url
3. image_url
4. gallery_image_1
5. gallery_image_2
6. gallery_image_3
7. gallery_image_4
8. gallery_image_5
9. gallery_images
```

It removes duplicates, then skips any image already listed in
`published_image_urls`, and uses the first remaining image.

For predictable automatic posting, use these fields like this:

```text
primary_image           = main/default product image
gallery_image_1..5      = extra product images in posting order
published_image_urls    = auto-written by n8n, do not edit manually
pin_description_history = auto-written by n8n, do not edit manually
pin_image_url           = optional override for the next preferred pin image
```

## Status Values

Rows are eligible when `pin_status` is blank or one of:

```text
pending
ready
todo
queued_for_pinterest
retry
failed_retry
```

After sending to Buffer, the workflow writes:

```text
posted_to_buffer
failed
```

## Full Recommended Header

This includes the existing crawler columns plus the Pinterest publisher columns.

```text
crawled_at
campaign_name
product_id
sku
title
url
primary_image
gallery_image_1
gallery_image_2
gallery_image_3
gallery_image_4
gallery_image_5
categories
tags
buffer_channel_1_id
buffer_channel_2_id
buffer_channel_3_id
buffer_channel_4_id
buffer_channel_5_id
buffer_channel_6_id
pinterest_channel_id
pinterest_board_service_id
pin_status
pin_priority
buffer_mode
scheduled_time_utc
pin_title
pin_description
recommended_alt_text
pin_image_url
destination_url
utm_content
attempt_count
last_attempt_at
posted_at
buffer_post_id
buffer_status
buffer_due_at
buffer_error
buffer_response_json
published_image_urls
pin_description_history
```
