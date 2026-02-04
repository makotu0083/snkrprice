import asyncio
import json
import os
import time
from datetime import datetime
from urllib.parse import quote

import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

# ==================================================
# 定数
# ==================================================
BASE_URL = "https://paypayfleamarket.yahoo.co.jp/search"
FACET_ID = 27435

SIZE_SPECS_MAP = {
    "23cm": 236665, "23.5cm": 236666, "24cm": 236667, "24.5cm": 236668,
    "25cm": 236669, "25.5cm": 236670, "26cm": 236671, "26.5cm": 236672,
    "27cm": 236673, "27.5cm": 236674, "28cm": 236675, "28.5cm": 236676,
    "29cm": 236677, "29.5cm": 236678, "30cm": 236679, "30.5cm": 260922,
    "31cm": 260923, "31.5cm": 260924, "32cm": 260925,
}

INPUT_SHEET_GID = 0
OUTPUT_SHEET_GID = 1994370799
HEADERS_ROW = ["ID", "NAME", "size", "site", "price", "url", "updated_at"]

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
# UI（__NEXT_DATA__）から items を安全に取得
# ==================================================
def extract_items_from_next_data(data: dict):
    """
    UIの揺れに対応：pageProps.searchResult / initialState 両対応
    """
    # 1) pageProps -> searchResult
    items = (
        data.get("props", {})
            .get("pageProps", {})
            .get("searchResult", {})
            .get("items")
    )
    if isinstance(items, list) and items:
        return items

    # 2) initialState -> searchState
    items = (
        data.get("props", {})
            .get("initialState", {})
            .get("searchState", {})
            .get("search", {})
            .get("result", {})
            .get("items")
    )
    if isinstance(items, list) and items:
        return items

    return []

# ==================================================
# Yahoo UI（__NEXT_DATA__）から最安取得（安定版）
# ==================================================
async def fetch_min_price_ui(browser, keyword, size, size_id, debug=False):
    encoded = quote(keyword)
    url = (
        f"{BASE_URL}/{encoded}"
        f"?sort=price&order=asc"
        f"&specs=C_{FACET_ID}%3A{size_id}"
        f"&conditions=NEW"
        f"&open=1"
    )

    page = await browser.new_page()
    try:
        # まずページ遷移
        await page.goto(url, timeout=60000)

        # __NEXT_DATA__ の script 出現を待つ（描画完了ではない）
        await page.wait_for_selector("script#__NEXT_DATA__", timeout=60000)

        items = []
        last_keys = None

        # ★ 重要：items が入るまで最大10回ポーリング
        for _ in range(10):
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")
            script = soup.find("script", id="__NEXT_DATA__")
            if not script or not script.string:
                await asyncio.sleep(0.5)
                continue

            data = json.loads(script.string)

            if debug:
                last_keys = (
                    list(data.get("props", {}).keys()),
                    list(data.get("props", {}).get("pageProps", {}).keys()),
                )

            items = extract_items_from_next_data(data)
            if items:
                break

            await asyncio.sleep(0.5)

        if debug:
            print(f"[DBG] size={size} items_len={len(items)} keys={last_keys}")

        if not items:
            return None, None

        # UI側で既に size / 未使用 / 販売中 が反映済み
        item = items[0]
        price = item.get("price")
        item_id = item.get("id")

        if price is None or not item_id:
            return None, None

        return price, f"https://paypayfleamarket.yahoo.co.jp/item/{item_id}"

    except Exception as e:
        print(f"[ERROR] UI fetch failed: {keyword} {size} {e}")
        return None, None

    finally:
        await page.close()
        await asyncio.sleep(0.5)  # bot対策＆安定化

# ==================================================
# メイン処理
# ==================================================
async def run():
    input_ws = gc.open_by_url(SPREADSHEET_URL).get_worksheet_by_id(INPUT_SHEET_GID)
    output_ws = gc.open_by_url(SPREADSHEET_URL).get_worksheet_by_id(OUTPUT_SHEET_GID)

    input_rows = input_ws.get_all_records()
    id_name_map = {
        row["NAME"]: row["ID"]
        for row in input_rows
        if row.get("ID") and row.get("NAME")
    }

    values = output_ws.get_all_values()
    if not values:
        output_ws.append_row(HEADERS_ROW)
        existing = []
    elif len(values) == 1:
        existing = []
    else:
        existing = output_ws.get_all_records()

    row_map = {
        (r["ID"], r["size"], r["site"]): idx + 2
        for idx, r in enumerate(existing)
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        try:
            for keyword, product_id in id_name_map.items():
                print(f"========== {keyword} ==========")

                for size, size_id in SIZE_SPECS_MAP.items():
                    price, url = await fetch_min_price_ui(
                        browser, keyword, size, size_id, debug=True
                    )

                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    row = [
                        product_id,
                        keyword,
                        size,
                        "YA",
                        price or 0,
                        url or "",
                        now,
                    ]

                    key = (product_id, size, "YA")
                    if key in row_map:
                        output_ws.update(
                            f"A{row_map[key]}:G{row_map[key]}",
                            [row],
                            value_input_option="USER_ENTERED",
                        )
                        print(f"更新 {size} ¥{price}")
                    else:
                        output_ws.append_row(row, value_input_option="USER_ENTERED")
                        print(f"追加 {size} ¥{price}")

        finally:
            await browser.close()

# ==================================================
# 実行
# ==================================================
if __name__ == "__main__":
    asyncio.run(run())