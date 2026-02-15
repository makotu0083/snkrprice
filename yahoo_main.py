import asyncio
import json
import re
import requests
import os
from datetime import datetime

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

import gspread
from google.oauth2.service_account import Credentials

# ==================================================
# 定数
# ==================================================
SEARCH_API = "https://paypayfleamarket.yahoo.co.jp/api/v1/search"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

SIZE_PATTERN = re.compile(r"\b(2[3-9](?:\.5)?|3[0-2](?:\.5)?)cm\b")

INPUT_SHEET_GID = 0
OUTPUT_SHEET_GID = 1994370799

HEADERS = ["ID", "NAME", "size", "site", "price", "url", "updated_at"]
SITE_CODE = "Yahoo!フリマ"

KEYWORD_SLEEP_SEC = 90

# ==================================================
# Google Sheets 認証
# ==================================================
creds_dict = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])

creds = Credentials.from_service_account_info(
    creds_dict,
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
)

gc = gspread.authorize(creds)

SPREADSHEET_URL = os.environ["SPREADSHEET_URL"]

# ==================================================
# Utility
# ==================================================
def normalize_size(size_with_cm: str):
    return size_with_cm.replace("cm", "").strip()

# ==================================================
# search API
# ==================================================
def search_items(keyword, limit=80):

    params = {
        "query": keyword,
        "sort": "price",
        "order": "asc",
        "page": 1,
        "limit": limit,
    }

    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
        "Referer": "https://paypayfleamarket.yahoo.co.jp/",
    }

    r = requests.get(
        SEARCH_API,
        params=params,
        headers=headers,
        timeout=20
    )

    r.raise_for_status()

    return r.json().get("items", []) or []

# ==================================================
# extract size
# ==================================================
async def extract_sizes(page, item_id):

    url = f"https://paypayfleamarket.yahoo.co.jp/item/{item_id}"

    for attempt in (1, 2):

        try:

            await page.goto(url, timeout=30000)

            await page.wait_for_load_state("networkidle")

            await asyncio.sleep(1.5)

            html = await page.content()

            soup = BeautifulSoup(html, "html.parser")

            text = soup.get_text(" ", strip=True)

            if len(text) < 500:
                raise Exception("blocked")

            matches = SIZE_PATTERN.findall(text)

            return sorted(set(m + "cm" for m in matches))

        except:

            if attempt == 1:

                print(f"[WARN] retry page: {item_id}")

                await asyncio.sleep(5)

            else:

                print(f"[WARN] blocked: {item_id}")

                return []

        finally:

            await asyncio.sleep(1)

# ==================================================
# sheet utils
# ==================================================
def load_input_products():

    ws = gc.open_by_url(
        SPREADSHEET_URL
    ).get_worksheet_by_id(INPUT_SHEET_GID)

    rows = ws.get_all_records()

    return {
        row["NAME"]: row["ID"]
        for row in rows
        if row.get("ID") and row.get("NAME")
    }

def prepare_output_sheet():

    ws = gc.open_by_url(
        SPREADSHEET_URL
    ).get_worksheet_by_id(OUTPUT_SHEET_GID)

    all_values = ws.get_all_values()

    if not all_values:

        ws.append_row(HEADERS)

        existing = []

        last_row = 1

    elif len(all_values) == 1:

        existing = []

        last_row = 1

    else:

        existing = ws.get_all_records()

        last_row = len(all_values)

    row_map = {}

    existing_sizes_map = {}

    for idx, r in enumerate(existing, start=2):

        pid = str(r.get("ID", "")).strip()

        size = normalize_size(
            str(r.get("size", "")).strip()
        )

        site = str(r.get("site", "")).strip()

        if not pid or not size or not site:
            continue

        row_map[(pid, size, site)] = idx

        existing_sizes_map.setdefault(
            (pid, site),
            set()
        ).add(size)

    return ws, row_map, existing_sizes_map, last_row

# ==================================================
# main
# ==================================================
async def run():

    id_name_map = load_input_products()

    output_ws, row_map, existing_sizes_map, last_row = prepare_output_sheet()

    # ★追加（最小修正）
    all_batch_updates = []

    for keyword, product_id_raw in id_name_map.items():

        product_id = str(product_id_raw).strip()

        print(f"\n=== KEYWORD: {keyword} ===")

        async with async_playwright() as p:

            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

            page = await browser.new_page()

            items = search_items(keyword)

            size_min_map = {}

            for item in items:

                if item.get("itemStatus") != "OPEN":
                    continue

                if item.get("condition") != "new":
                    continue

                item_id = item.get("id")

                price = item.get("price")

                if not item_id or price is None:
                    continue

                sizes = await extract_sizes(page, item_id)

                if not sizes:
                    continue

                for s in sizes:

                    size = normalize_size(s)

                    if (
                        size not in size_min_map
                        or price < size_min_map[size]["price"]
                    ):

                        size_min_map[size] = {

                            "price": int(price),

                            "url": f"https://paypayfleamarket.yahoo.co.jp/item/{item_id}",
                        }

            await browser.close()

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        existing_sizes = existing_sizes_map.get(
            (product_id, SITE_CODE),
            set()
        )

        target_sizes = set(existing_sizes) | set(size_min_map.keys())

        batch_updates = []

        for size in sorted(target_sizes, key=lambda x: float(x)):

            if size in size_min_map:

                price = size_min_map[size]["price"]

                url = size_min_map[size]["url"]

            else:

                price = 0

                url = ""

            values = [
                product_id,
                keyword,
                size,
                SITE_CODE,
                price,
                url,
                now,
            ]

            key = (product_id, size, SITE_CODE)

            if key in row_map:

                row = row_map[key]

            else:

                last_row += 1

                row = last_row

                row_map[key] = row

                existing_sizes_map.setdefault(
                    (product_id, SITE_CODE),
                    set()
                ).add(size)

            batch_updates.append({

                "range": f"A{row}:G{row}",

                "values": [values]

            })

            print(f"更新 size={size} price={price}")

        # ★変更（最小修正）
        all_batch_updates.extend(batch_updates)

        print(f"[INFO] sleep {KEYWORD_SLEEP_SEC}s")

        await asyncio.sleep(KEYWORD_SLEEP_SEC)

    # ★追加（最小修正）
    if all_batch_updates:

        output_ws.batch_update(
            all_batch_updates,
            value_input_option="USER_ENTERED"
        )

# ==================================================
# start
# ==================================================
if __name__ == "__main__":

    asyncio.run(run())
