import asyncio
import json
import re
import requests
from math import inf

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

import gspread
from google.oauth2.service_account import Credentials
import os

# ==================================================
# 定数
# ==================================================
SEARCH_API = "https://paypayfleamarket.yahoo.co.jp/api/v1/search"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 対象サイズ表記（cm固定）
SIZE_PATTERN = re.compile(r"\b(2[3-9](?:\.5)?|3[0-2](?:\.5)?)cm\b")

INPUT_SHEET_GID = 0  # NAME があるシート

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
def search_items(keyword, limit=30):
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
# 商品ページからサイズ抽出
# ==================================================
async def extract_sizes(browser, item_id):
    url = f"https://paypayfleamarket.yahoo.co.jp/item/{item_id}"
    page = await browser.new_page()

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

    finally:
        await page.close()
        await asyncio.sleep(0.3)

# ==================================================
# Sheets から keyword 一覧取得
# ==================================================
def load_keywords_from_sheet():
    ws = gc.open_by_url(SPREADSHEET_URL).get_worksheet_by_id(INPUT_SHEET_GID)

    # B列（NAME）を取得、1行目はヘッダなので除外
    names = ws.col_values(2)[1:]
    keywords = [n.strip() for n in names if n.strip()]

    print(f"[INFO] Loaded {len(keywords)} keywords from sheet")
    return keywords

# ==================================================
# メイン
# ==================================================
async def run():
    keywords = load_keywords_from_sheet()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        for keyword in keywords:
            print("\n========================================")
            print(f"=== KEYWORD: {keyword} ===")

            items = search_items(keyword, limit=30)
            print(f"search API items: {len(items)}")

            # サイズごとの最安値
            size_min_map = {}

            for item in items:
                # --- 条件フィルタ（search API） ---
                if item.get("itemStatus") != "OPEN":
                    continue
                if item.get("condition") != "new":
                    continue

                item_id = item.get("id")
                title = item.get("title")
                price = item.get("price")

                sizes = await extract_sizes(browser, item_id)
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

            # --- keyword ごとの最終結果 ---
            print("\n=== SIZE MIN PRICE RESULT ===")
            for size in sorted(size_min_map.keys(), key=lambda x: float(x.replace("cm", ""))):
                data = size_min_map[size]
                print(
                    f"{size}: ¥{data['price']:,} "
                    f"({data['id']})"
                )

            print("=== SIZE COUNT ===", len(size_min_map))

        await browser.close()

# ==================================================
# 実行
# ==================================================
if __name__ == "__main__":
    asyncio.run(run())
