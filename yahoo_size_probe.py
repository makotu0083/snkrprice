import asyncio
import json
import re
import requests
import os
from math import inf

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

# サイズ抽出（※現行ロジックを維持）
SIZE_PATTERN = re.compile(r"\b(2[3-9](?:\.5)?|3[0-2](?:\.5)?)cm\b")

INPUT_SHEET_GID = 0  # NAME 列があるシート

# ==================================================
# Google Sheets 認証
# ==================================================
creds_dict = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
creds = Credentials.from_service_account_info(
    creds_dict,
    scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
)
gc = gspread.authorize(creds)
SPREADSHEET_URL = os.environ["SPREADSHEET_URL"]

# ==================================================
# search API
# ==================================================
def search_items(keyword, limit=50):
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
# 商品ページからサイズ抽出（page使い回し）
# ==================================================
async def extract_sizes(page, item_id):
    url = f"https://paypayfleamarket.yahoo.co.jp/item/{item_id}"

    try:
        await page.goto(url, timeout=30000)
        await page.wait_for_load_state("domcontentloaded")

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)

        matches = SIZE_PATTERN.findall(text)
        return sorted(set(m + "cm" for m in matches))

    except Exception as e:
        print(f"[WARN] page failed: {item_id} ({e})")
        return []

# ==================================================
# Sheets から keyword 一覧取得
# ==================================================
def load_keywords_from_sheet():
    ws = gc.open_by_url(SPREADSHEET_URL).get_worksheet_by_id(INPUT_SHEET_GID)

    names = ws.col_values(2)[1:]  # B列、ヘッダ除外
    keywords = [n.strip() for n in names if n.strip()]

    print(f"[INFO] Loaded {len(keywords)} keywords from sheet")
    return keywords

# ==================================================
# メイン
# ==================================================
async def run():
    keywords = load_keywords_from_sheet()

    for keyword in keywords:
        print("\n========================================")
        print(f"=== KEYWORD: {keyword} ===")

        # --- keyword ごとに Chromium を作り直す ---
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = await browser.new_page()

            items = search_items(keyword, limit=50)
            print(f"search API items: {len(items)}")

            size_min_map = {}

            for item in items:
                # search API 側の高速フィルタ（維持）
                if item.get("itemStatus") != "OPEN":
                    continue
                if item.get("condition") != "new":
                    continue

                item_id = item.get("id")
                title = item.get("title")
                price = item.get("price")

                sizes = await extract_sizes(page, item_id)
                if not sizes:
                    continue

                print("\n----------------------------")
                print("id   :", item_id)
                print("title:", title)
                print("price:", price)
                print("sizes:", sizes)

                for size in sizes:
                    if size not in size_min_map or price < size_min_map[size]["price"]:
                        size_min_map[size] = {
                            "price": price,
                            "id": item_id,
                            "title": title,
                            "url": f"https://paypayfleamarket.yahoo.co.jp/item/{item_id}",
                        }

            await browser.close()

        # --- keyword 結果出力 ---
        print("\n=== SIZE MIN PRICE RESULT ===")
        for size in sorted(size_min_map.keys(), key=lambda x: float(x.replace("cm", ""))):
            d = size_min_map[size]
            print(f"{size}: ¥{d['price']:,} ({d['id']})")

        print("=== SIZE COUNT ===", len(size_min_map))

# ==================================================
# 実行
# ==================================================
if __name__ == "__main__":
    asyncio.run(run())
