#!/usr/bin/env python3
"""Scheduled Google Sheets -> OpenAI -> Buffer Pinterest publisher.

Designed for GitHub Actions cron or a tiny VPS cron job. The Google Sheet is
the queue and state store, so reruns are safe after transient failures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build


BUFFER_URL = "https://api.buffer.com"
DEFAULT_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

OUTPUT_COLUMNS = [
    "pin_status",
    "pin_title",
    "pin_description",
    "recommended_alt_text",
    "pin_image_url",
    "destination_url",
    "utm_content",
    "attempt_count",
    "last_attempt_at",
    "posted_at",
    "buffer_post_id",
    "buffer_status",
    "buffer_due_at",
    "buffer_error",
    "buffer_response_json",
    "published_image_urls",
    "pin_description_history",
    "scheduled_time_utc",
]


@dataclass
class Config:
    spreadsheet_id: str
    sheet_name: str
    buffer_api_key: str
    openai_api_key: str
    openai_url: str
    openai_model: str
    post_timezone: str
    post_hours: list[int]
    slots_per_run: int
    days_to_queue: int
    dry_run: bool
    enable_social_mirrors: bool
    default_pinterest_channel_id: str
    default_facebook_channel_id: str
    default_instagram_channel_id: str
    default_pinterest_board_service_id: str


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> Config:
    required = ["BUFFER_API_KEY", "OPENAI_API_KEY", "SPREADSHEET_ID"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")

    post_hours = [
        int(part.strip())
        for part in os.environ.get("POST_HOURS", "9,13,18").split(",")
        if part.strip()
    ]
    if not post_hours:
        raise SystemExit("POST_HOURS must contain at least one hour, e.g. 9,13,18")

    return Config(
        spreadsheet_id=os.environ["SPREADSHEET_ID"],
        sheet_name=os.environ.get("SHEET_NAME", "Sheet1"),
        buffer_api_key=os.environ["BUFFER_API_KEY"],
        openai_api_key=os.environ["OPENAI_API_KEY"],
        openai_url=os.environ.get("OPENAI_URL", DEFAULT_OPENAI_URL),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
        post_timezone=os.environ.get("POST_TIMEZONE", "Europe/Paris"),
        post_hours=post_hours,
        slots_per_run=int(os.environ.get("SLOTS_PER_RUN", str(len(post_hours)))),
        days_to_queue=int(os.environ.get("DAYS_TO_QUEUE", "1")),
        dry_run=env_bool("DRY_RUN"),
        enable_social_mirrors=env_bool("ENABLE_SOCIAL_MIRRORS"),
        default_pinterest_channel_id=os.environ.get("DEFAULT_PINTEREST_CHANNEL_ID", ""),
        default_facebook_channel_id=os.environ.get("DEFAULT_FACEBOOK_CHANNEL_ID", ""),
        default_instagram_channel_id=os.environ.get("DEFAULT_INSTAGRAM_CHANNEL_ID", ""),
        default_pinterest_board_service_id=os.environ.get("DEFAULT_PINTEREST_BOARD_SERVICE_ID", ""),
    )


def first_text(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def split_urls(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in re.split(r"\n|,", str(value)) if part.strip()]


def split_history(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        candidates = [str(item).strip() for item in value]
    else:
        candidates = [part.strip() for part in re.split(r"\n---\n|\n(?=20\d\d-\d\d-\d\dT)", str(value))]
    return [item for item in candidates if item]


def unique_http_urls(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value).strip()
        if not re.match(r"^https?://", text, flags=re.I):
            continue
        if text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def ordered_product_images(row: dict[str, str]) -> list[str]:
    canonical = unique_http_urls(
        [
            first_text(
                row.get("primary_image"),
                row.get("image_url"),
                row.get("image"),
                row.get("featured_image"),
                row.get("product_image"),
                row.get("thumbnail_url"),
            ),
            first_text(row.get("gallery_image_1")),
            first_text(row.get("gallery_image_2")),
            first_text(row.get("gallery_image_3")),
            first_text(row.get("gallery_image_4")),
            first_text(row.get("gallery_image_5")),
        ]
    )
    if canonical:
        return canonical
    return unique_http_urls(
        [
            first_text(row.get("pin_image_url")),
            *split_urls(row.get("gallery_images")),
            *split_urls(row.get("images")),
        ]
    )


def inferred_published_urls(row: dict[str, str], all_images: list[str]) -> list[str]:
    published = split_urls(row.get("published_image_urls"))
    if published:
        all_image_set = set(all_images)
        return [url for url in published if url in all_image_set]

    last_image = first_text(row.get("pin_image_url"))
    has_prior_post = first_text(row.get("buffer_post_id"), row.get("buffer_due_at"), row.get("posted_at"), row.get("last_attempt_at"))
    if last_image and has_prior_post and last_image in all_images:
        return all_images[: all_images.index(last_image) + 1]

    return []


def get_service_account_credentials():
    raw_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw_json:
        info = json.loads(raw_json)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if credentials_path:
        return service_account.Credentials.from_service_account_file(credentials_path, scopes=SCOPES)

    raise SystemExit("Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS")


def sheets_client():
    return build("sheets", "v4", credentials=get_service_account_credentials(), cache_discovery=False)


def col_letter(index_1_based: int) -> str:
    result = ""
    index = index_1_based
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def read_sheet(service, cfg: Config) -> tuple[list[str], list[dict[str, str]]]:
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=cfg.spreadsheet_id, range=f"{cfg.sheet_name}!A:ZZ")
        .execute()
    )
    values = result.get("values", [])
    if not values:
        raise SystemExit("Google Sheet is empty")
    headers = [str(value).strip() for value in values[0]]
    rows: list[dict[str, str]] = []
    for row_index, values_row in enumerate(values[1:], start=2):
        row = {header: str(values_row[i]).strip() if i < len(values_row) else "" for i, header in enumerate(headers)}
        row["_row_number"] = str(row_index)
        rows.append(row)
    return headers, rows


def ensure_output_columns(service, cfg: Config, headers: list[str]) -> list[str]:
    missing = [column for column in OUTPUT_COLUMNS if column not in headers]
    if not missing:
        return headers
    updated = [*headers, *missing]
    end_col = col_letter(len(updated))
    service.spreadsheets().values().update(
        spreadsheetId=cfg.spreadsheet_id,
        range=f"{cfg.sheet_name}!A1:{end_col}1",
        valueInputOption="RAW",
        body={"values": [updated]},
    ).execute()
    print(f"Added missing Google Sheet columns: {', '.join(missing)}")
    return updated


def add_utm(raw_url: str, params: dict[str, str]) -> str:
    if not raw_url:
        raise ValueError("Missing product URL")
    separator = "&" if "?" in raw_url else "?"
    query = "&".join(
        f"{urllib.parse.quote(key)}={urllib.parse.quote(str(value))}"
        for key, value in params.items()
        if value
    )
    return f"{raw_url}{separator}{query}" if query else raw_url


def campaign_slug(row: dict[str, str]) -> str:
    campaign = first_text(row.get("campaign_name"), "product-crawl").lower()
    return re.sub(r"(^_+|_+$)", "", re.sub(r"[^a-z0-9]+", "_", campaign)) or "product_crawl"


def clean_product_name(name: str) -> str:
    return re.sub(r"\s+", " ", name or "Luxury Silk Scarf").replace(" - Herbert Accessory", "").strip()


def stable_number(value: str) -> int:
    return sum(ord(ch) for ch in value)


def truncate(text: str, max_length: int) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    return clean[: max_length - 1].rstrip() if len(clean) > max_length else clean


def description_key(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text.lower())
    text = re.sub(r"#[a-z0-9_]+", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def append_description_history(existing: object, description: str, when: str) -> str:
    history = split_history(existing)
    key = description_key(description)
    if key and key not in {description_key(item) for item in history}:
        history.append(f"{when} {truncate(description, 700)}")
    return "\n---\n".join(history[-30:])


def fallback_description(row: dict[str, str], product_url: str) -> str:
    product_name = clean_product_name(first_text(row.get("title"), row.get("pin_title"), "silk scarf"))
    image_index = int(first_text(row.get("selected_image_index"), row.get("slot_index"), "0") or "0")
    seed = stable_number(first_text(row.get("product_id"), row.get("sku")) + str(image_index))
    history_keys = {description_key(item) for item in split_history(row.get("pin_description_history"))}

    openings = [
        "Silk at the neckline can change the whole mood of a simple outfit.",
        "A printed scarf is sometimes the easiest way to look more considered.",
        "Try this with a clean shirt, soft denim, and very little else.",
        "This one feels made for days when the outfit needs one good detail.",
        "A scarf like this is small enough to wear often and special enough to notice.",
        "Fold it narrow, let the print peek through, and keep the rest quiet.",
        "The right silk square makes a plain knit feel intentional.",
        "This is the kind of accessory that earns its place in a travel bag.",
    ]
    middles = [
        f"{product_name} has a light pure silk feel, soft movement, and a print that brings color without making the outfit loud.",
        f"Wear {product_name} at the neck, through a ponytail, or tied on a bag when you want a useful accent.",
        f"The pure silk drape keeps {product_name} polished but easy, especially with shirts, knits, denim, and neutral coats.",
        f"{product_name} also makes a thoughtful silk scarf gift for someone who likes beautiful pieces they can actually wear.",
        f"The print on {product_name} gives you a fresh styling angle each time, from soft everyday looks to dressier moments.",
        f"Because {product_name} is lightweight silk, it works across seasons instead of sitting in the wardrobe for one occasion.",
    ]
    closers = [
        "Save it for silk scarf outfit ideas, travel styling, or a future wardrobe refresh.",
        "A useful piece to keep in mind for scarf styling and thoughtful gifts for her.",
        "A simple way to bring pattern, texture, and a more personal finish to everyday dressing.",
        "Pin it for the next time a basic outfit needs one softer, more elegant detail.",
        "Good for weekday outfits, weekend packing, and anyone collecting pure silk accessories.",
    ]
    hashtags = [
        "#silkscarf #luxuryscarf #puresilk #silkscarfstyle #scarfoutfit #herbertaccessory",
        "#silkscarf #mulberrysilk #silkaccessories #howtowearascarf #giftforher #herbertaccessory",
        "#luxurysilkscarf #silksquare #scarfstyling #puremulberrysilk #wardrobedetails #herbertaccessory",
        "#printedscarf #silkscarfgift #scarfoutfitideas #elegantaccessories #silkstyle #herbertaccessory",
    ]
    candidates = []
    for offset in range(max(len(openings), len(middles), len(closers), len(hashtags))):
        parts = [
            openings[(seed + offset) % len(openings)],
            middles[(seed + image_index + offset * 2) % len(middles)],
            closers[(seed + offset * 3) % len(closers)],
            f"Shop now: {product_url}",
            hashtags[(seed + offset * 5) % len(hashtags)],
        ]
        candidates.append(truncate(" ".join(parts), 800))

    for candidate in candidates:
        if description_key(candidate) not in history_keys:
            return candidate
    return candidates[seed % len(candidates)]


def generate_pin_copy(cfg: Config, row: dict[str, str]) -> tuple[str, str, str]:
    product_url = first_text(row.get("canonical_url"), row.get("url"))
    product_name = clean_product_name(first_text(row.get("title"), "Luxury Silk Scarf"))
    fallback_title = truncate(f"{product_name} | Luxury Silk Scarf - Herbert Accessory", 100)
    fallback_body = fallback_description(row, product_url)
    previous_descriptions = split_history(row.get("pin_description_history"))
    previous_sample = "\n".join(f"- {truncate(item, 240)}" for item in previous_descriptions[-8:])

    system_prompt = (
        "You are a Pinterest content expert for Herbert Accessory, a luxury silk scarf and accessories brand. "
        "Write warm, specific Pin copy that sounds like a real person saving a beautiful product, not a catalog. "
        "Every description must use a fresh opening, fresh sentence rhythm, and a fresh styling angle. "
        "Never repeat or lightly paraphrase any previous descriptions provided by the user. "
        "Avoid opening with Elevate, Discover, Whether you're, Crafted from, Looking for, or Introducing. "
        "Output only JSON with title and description. The description must include the product link before hashtags as: Shop now: [URL]."
    )
    user_prompt = (
        f"Product: {product_name}\n"
        f"Link: {product_url}\n"
        f"Image number for this product: {int(first_text(row.get('selected_image_index'), '0')) + 1}\n"
        f"Previous descriptions to avoid:\n{previous_sample or '- none yet'}\n"
        "Create a Pinterest title under 100 characters and a description under 800 characters with 5-8 hashtags. "
        "Make this one visibly different from all previous descriptions."
    )
    payload = {
        "model": cfg.openai_model,
        "temperature": 0.9,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    request = urllib.request.Request(
        cfg.openai_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {cfg.openai_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
        raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
        title = truncate(first_text(parsed.get("title"), fallback_title), 100)
        description = truncate(first_text(parsed.get("description"), fallback_body), 800)
        if description_key(description) in {description_key(item) for item in previous_descriptions}:
            description = fallback_body
    except Exception as exc:
        print(f"OpenAI copy generation failed; using fallback copy: {exc}", file=sys.stderr)
        title = fallback_title
        description = fallback_body

    alt_text = truncate(f"{product_name} product image for Herbert Accessory", 500)
    return title, description, alt_text


def next_schedule_slots(cfg: Config, count: int) -> list[datetime]:
    tz = ZoneInfo(cfg.post_timezone)
    now = datetime.now(tz)
    today = now.date()
    first_slot_today = datetime(today.year, today.month, today.day, cfg.post_hours[0], tzinfo=tz)
    start_date = today + timedelta(days=1) if now >= first_slot_today else today

    slots: list[datetime] = []
    day_offset = 0
    while len(slots) < count:
        slot_date = start_date + timedelta(days=day_offset)
        for hour in cfg.post_hours:
            if len(slots) >= count:
                break
            local_dt = datetime(slot_date.year, slot_date.month, slot_date.day, hour, tzinfo=tz)
            slots.append(local_dt.astimezone(timezone.utc))
        day_offset += 1
    return slots


def select_batch(rows: list[dict[str, str]], cfg: Config) -> list[dict[str, str]]:
    eligible = []
    for index, row in enumerate(rows):
        status = first_text(row.get("pin_status"), row.get("pinterest_status"), row.get("status")).lower()
        if status in {"completed", "skip", "skipped"}:
            continue
        url = first_text(row.get("url"), row.get("product_url"), row.get("destination_url"), row.get("canonical_url"))
        if not url:
            continue
        all_images = ordered_product_images(row)
        published = inferred_published_urls(row, all_images)
        unpublished = [url for url in all_images if url not in set(published)]
        if not unpublished:
            continue
        priority = int(first_text(row.get("pin_priority"), row.get("priority"), "999") or "999")
        row_num = int(row.get("_row_number", index + 2))
        eligible.append((priority, row_num, row, all_images, unpublished))

    eligible.sort(key=lambda item: (item[0], item[1]))
    batch: list[dict[str, str]] = []
    slots_remaining = cfg.slots_per_run * cfg.days_to_queue
    for _, _, row, all_images, unpublished in eligible:
        if slots_remaining <= 0:
            break
        for image_url in unpublished[:slots_remaining]:
            image_index = all_images.index(image_url)
            item = dict(row)
            item.update(
                {
                    "canonical_url": first_text(row.get("url"), row.get("product_url"), row.get("destination_url"), row.get("canonical_url")),
                    "selected_image_url": image_url,
                    "selected_image_index": str(image_index),
                    "all_image_urls_json": json.dumps(all_images),
                    "slot_index": str(len(batch)),
                }
            )
            batch.append(item)
        slots_remaining -= min(len(unpublished), slots_remaining)
    return batch


def create_buffer_post(cfg: Config, post_input: dict) -> dict:
    mutation = """
    mutation CreatePost($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess {
          post {
            id
            text
            dueAt
            status
            channelId
            assets { id mimeType }
            error { message }
          }
        }
        ... on MutationError { message }
      }
    }
    """
    payload = {"query": mutation, "variables": {"input": post_input}}
    if cfg.dry_run:
        return {"dryRun": True, "request": payload}
    request = urllib.request.Request(
        BUFFER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {cfg.buffer_api_key}",
            "Content-Type": "application/json",
            "User-Agent": "herbert-pinterest-publisher/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"transportError": f"Buffer HTTP {exc.code}: {body}"}
    except urllib.error.URLError as exc:
        return {"transportError": f"Buffer request failed: {exc}"}


def result_error(result: dict) -> str:
    if result.get("dryRun"):
        return ""
    if result.get("transportError"):
        return result["transportError"]
    if result.get("errors"):
        return "; ".join(error.get("message", json.dumps(error)) for error in result["errors"])
    action = result.get("data", {}).get("createPost", {})
    if action.get("message"):
        return action["message"]
    post = action.get("post")
    if not post:
        return "Buffer did not return a created post"
    return first_text((post.get("error") or {}).get("message"))


def post_from_result(result: dict) -> dict:
    return result.get("data", {}).get("createPost", {}).get("post") or {}


def build_posts(cfg: Config, row: dict[str, str], due_at: str) -> list[tuple[str, dict, str]]:
    title, description, alt_text = generate_pin_copy(cfg, row)
    row["pin_title"] = title
    row["pin_description"] = description
    row["recommended_alt_text"] = alt_text

    campaign = campaign_slug(row)
    utm_content = truncate(
        re.sub(r"(^_+|_+$)", "", re.sub(r"[^a-z0-9]+", "_", first_text(row.get("product_id"), row.get("sku"), row.get("_row_number")).lower())),
        60,
    ) or "pin"
    row["utm_content"] = utm_content
    pinterest_url = add_utm(
        row["canonical_url"],
        {
            "utm_source": "pinterest",
            "utm_medium": "social",
            "utm_campaign": campaign,
            "utm_content": utm_content,
        },
    )
    row["destination_url"] = pinterest_url

    pinterest_channel_id = first_text(
        row.get("pinterest_channel_id"),
        row.get("buffer_channel_1_id"),
        row.get("buffer_pinterest_channel_id"),
        cfg.default_pinterest_channel_id,
    )
    board_service_id = first_text(
        row.get("pinterest_board_service_id"),
        row.get("board_service_id"),
        cfg.default_pinterest_board_service_id,
    )
    if not pinterest_channel_id:
        raise ValueError("Missing Pinterest Buffer channel ID")
    if not board_service_id:
        raise ValueError("Missing Pinterest board service ID")

    base = {
        "schedulingType": "automatic",
        "mode": "customScheduled",
        "dueAt": due_at,
        "assets": {"images": [{"url": row["selected_image_url"]}]},
    }
    posts = [
        (
            "pinterest",
            {
                **base,
                "channelId": pinterest_channel_id,
                "text": description,
                "metadata": {
                    "pinterest": {
                        "boardServiceId": board_service_id,
                        "title": title,
                        "url": pinterest_url,
                    }
                },
            },
            pinterest_url,
        )
    ]

    if cfg.enable_social_mirrors:
        platform_defaults = [
            ("facebook", first_text(row.get("facebook_channel_id"), row.get("buffer_channel_2_id"), cfg.default_facebook_channel_id)),
            ("instagram", first_text(row.get("instagram_channel_id"), row.get("buffer_channel_3_id"), cfg.default_instagram_channel_id)),
        ]
        for platform, channel_id in platform_defaults:
            if not channel_id:
                continue
            platform_url = add_utm(
                row["canonical_url"],
                {
                    "utm_source": platform,
                    "utm_medium": "social",
                    "utm_campaign": campaign,
                    "utm_content": utm_content,
                },
            )
            metadata = {"facebook": {"type": "post"}} if platform == "facebook" else {"instagram": {"type": "post", "shouldShareToFeed": True}}
            text = description.replace(pinterest_url, platform_url)
            posts.append((platform, {**base, "channelId": channel_id, "text": text, "metadata": metadata}, platform_url))

    return posts


def update_row(service, cfg: Config, headers: list[str], row_number: int, values: dict[str, str]) -> None:
    row_values = [""] * len(headers)
    for key, value in values.items():
        if key in headers:
            row_values[headers.index(key)] = value
    end_col = col_letter(len(headers))
    service.spreadsheets().values().update(
        spreadsheetId=cfg.spreadsheet_id,
        range=f"{cfg.sheet_name}!A{row_number}:{end_col}{row_number}",
        valueInputOption="RAW",
        body={"values": [row_values]},
    ).execute()


def process_item(service, cfg: Config, headers: list[str], row: dict[str, str], due_at: str) -> bool:
    row_number = int(row["_row_number"])
    now = datetime.now(timezone.utc).isoformat()
    try:
        posts = build_posts(cfg, row, due_at)
        results = []
        pinterest_success = False
        pinterest_post: dict = {}
        errors: list[str] = []
        for platform, post_input, _destination_url in posts:
            result = create_buffer_post(cfg, post_input)
            results.append({"platform": platform, "result": result})
            error = result_error(result)
            if error:
                errors.append(f"{platform}: {error}")
            if platform == "pinterest" and not error:
                pinterest_success = True
                pinterest_post = post_from_result(result)

        all_images = json.loads(row["all_image_urls_json"])
        published = inferred_published_urls(row, all_images)
        if pinterest_success and row["selected_image_url"] not in published:
            published.append(row["selected_image_url"])
        all_done = all(image in published for image in all_images)
        status = "completed" if pinterest_success and all_done else ("pending" if pinterest_success else "failed")
        error_text = "; ".join(errors)
        description_history = append_description_history(row.get("pin_description_history"), row.get("pin_description", ""), now) if pinterest_success else row.get("pin_description_history", "")
        update_values = {
            **row,
            "pin_status": "dry_run" if cfg.dry_run else status,
            "pin_image_url": row["selected_image_url"],
            "published_image_urls": ",".join(published),
            "pin_description_history": description_history,
            "buffer_post_id": first_text(pinterest_post.get("id")),
            "buffer_status": first_text(pinterest_post.get("status"), "dry_run" if cfg.dry_run else ""),
            "buffer_due_at": first_text(pinterest_post.get("dueAt"), due_at),
            "buffer_error": error_text,
            "last_attempt_at": now,
            "posted_at": now if pinterest_success and not cfg.dry_run else "",
            "buffer_response_json": json.dumps(results)[:1200],
            "scheduled_time_utc": due_at,
        }
        if cfg.dry_run:
            print(json.dumps(update_values, indent=2, sort_keys=True))
            return True
        update_row(service, cfg, headers, row_number, update_values)
        print(f"Row {row_number}: {status} for {row.get('title', '<untitled>')}")
        if error_text:
            print(f"Row {row_number} Buffer warning/error: {error_text}", file=sys.stderr)
        return pinterest_success
    except Exception as exc:
        error_text = str(exc)
        print(f"Row {row_number} failed before Buffer post: {error_text}", file=sys.stderr)
        if not cfg.dry_run:
            update_row(
                service,
                cfg,
                headers,
                row_number,
                {
                    **row,
                    "pin_status": "failed",
                    "buffer_error": error_text,
                    "last_attempt_at": now,
                    "attempt_count": str(int(first_text(row.get("attempt_count"), "0") or "0") + 1),
                },
            )
        return False


def main() -> None:
    cfg = load_config()
    service = sheets_client()
    headers, rows = read_sheet(service, cfg)
    headers = ensure_output_columns(service, cfg, headers)
    batch = select_batch(rows, cfg)
    if not batch:
        print("No pending rows with unpublished images.")
        return

    slots = next_schedule_slots(cfg, len(batch))
    row_state = {}
    for source_row in rows:
        row_number = source_row.get("_row_number")
        all_images = ordered_product_images(source_row)
        row_state[row_number] = {
            "published": inferred_published_urls(source_row, all_images),
            "history": split_history(source_row.get("pin_description_history")),
        }

    success_count = 0
    for row, slot in zip(batch, slots):
        state = row_state.setdefault(row["_row_number"], {"published": [], "history": []})
        row["published_image_urls"] = ",".join(state["published"])
        row["pin_description_history"] = "\n---\n".join(state["history"])
        due_at = slot.isoformat().replace("+00:00", "Z")
        row["attempt_count"] = str(int(first_text(row.get("attempt_count"), "0") or "0") + 1)
        if process_item(service, cfg, headers, row, due_at):
            success_count += 1
            if row["selected_image_url"] not in state["published"]:
                state["published"].append(row["selected_image_url"])
            updated_history = append_description_history(
                row.get("pin_description_history"),
                row.get("pin_description", ""),
                datetime.now(timezone.utc).isoformat(),
            )
            state["history"] = split_history(updated_history)

    print(f"Finished. Pinterest successes: {success_count}/{len(batch)}")
    if success_count == 0 and not cfg.dry_run:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
