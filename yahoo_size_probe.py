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
    r = requests.get(SEARCH_API, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json().get("items", []) or []

# ==================================================
# 商品ページからサイズ抽出（安定化版）
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
                raise ValueError("page content too small")

            matches = SIZE_PATTERN.findall(text)
            return sorted(set(m + "cm" for m in matches))

        except Exception:
            if attempt == 1:
                print(f"[WARN] retry page: {item_id}")
                await asyncio.sleep(5)
            else:
                print(f"[WARN] blocked: {item_id}")
                return []

        finally:
            await asyncio.sleep(1.0)

# ==================================================
# Sheets utility
# ==================================================
def load_input_products():
    ws = gc.open_by_url(SPREADSHEET_URL).get_worksheet_by_id(INPUT_SHEET_GID)
    rows = ws.get_all_records()
    return {row["NAME"]: row["ID"] for row in rows if row.get("ID") and row.get("NAME")}

def prepare_output_sheet():
    ws = gc.open_by_url(SPREADSHEET_URL).get_worksheet_by_id(OUTPUT_SHEET_GID)
    values = ws.get_all_values()
    if not values:
        ws.append_row(HEADERS)
        return ws, {}
    existing = ws.get_all_records()
    row_map = {
        (r["ID"], r["size"], r["site"]): idx + 2
        for idx, r in enumerate(existing)
    }
    return ws, row_map

# ==================================================
# メイン
# ==================================================
async def run():
    id_name_map = load_input_products()
    output_ws, row_map = prepare_output_sheet()

    for keyword, product_id in id_name_map.items():
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

                item_id = item["id"]
                price = item["price"]
                sizes = await extract_sizes(page, item_id)
                if not sizes:
                    continue

                for size in sizes:
                    if size not in size_min_map or price < size_min_map[size]["price"]:
                        size_min_map[size] = {
                            "price": price,
                            "url": f"https://paypayfleamarket.yahoo.co.jp/item/{item_id}",
                        }

            await browser.close()

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for size, data in size_min_map.items():
            values = [
                product_id,
                keyword,
                size,
                "Yahoo!フリマ",
                data["price"],
                data["url"],
                now,
            ]

            key = (product_id, size, "YA")
            if key in row_map:
                output_ws.update(
                    f"A{row_map[key]}:G{row_map[key]}",
                    [values],
                    value_input_option="USER_ENTERED",
                )
                print(f"更新 {size} ¥{data['price']}")
            else:
                output_ws.append_row(values, value_input_option="USER_ENTERED")
                print(f"追加 {size} ¥{data['price']}")

        print("[INFO] keyword done → sleep 90s")
        await asyncio.sleep(90)

# ==================================================
# 実行
# ==================================================
if __name__ == "__main__":
    asyncio.run(run())
