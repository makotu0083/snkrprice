import urllib.parse
import json
import asyncio
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import os

# ==================================================
# 定数
# ==================================================
SIZE_SPECS_MAP = {
    "23cm": 236665, "23.5cm": 236666, "24cm": 236667, "24.5cm": 236668,
    "25cm": 236669, "25.5cm": 236670, "26cm": 236671, "26.5cm": 236672,
    "27cm": 236673, "27.5cm": 236674, "28cm": 236675, "28.5cm": 236676,
    "29cm": 236677, "29.5cm": 236678, "30cm": 236679, "30.5cm": 260922,
    "31cm": 260923, "31.5cm": 260924, "32cm": 260925,
}
FACET_ID = 27435
BASE_URL = "https://paypayfleamarket.yahoo.co.jp/search"
NO_RESULT_TEXT = "に一致する商品はありません。"

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
# URL生成
# ==================================================
def generate_size_search_urls(keyword):
    encoded = urllib.parse.quote(keyword)
    return {
        size: (
            f"{BASE_URL}/{encoded}"
            f"?sort=price&order=asc"
            f"&specs=C_{FACET_ID}%3A{value_id}"
            f"&conditions=NEW"
            f"&open=1"
        )
        for size, value_id in SIZE_SPECS_MAP.items()
    }

# ==================================================
# Yahoo JSON items 抽出（耐性あり）
# ==================================================
def extract_items(data):
    paths = [
        lambda d: d["props"]["pageProps"]["searchResult"]["items"],
        lambda d: d["props"]["initialState"]["search"]["results"]["items"],
        lambda d: d["props"]["initialState"]["search"]["searchResult"]["items"],
        lambda d: d["props"]["pageProps"]["dehydratedState"]["queries"][0]["state"]["data"]["items"],
    ]

    for getter in paths:
        try:
            items = getter(data)
            if isinstance(items, list) and items:
                return items
        except Exception:
            continue

    return []

# ==================================================
# スクレイピング
# ==================================================
async def fetch_min_price(browser, size, url):
    page = await browser.new_page(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    )

    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_load_state("networkidle")

        soup = BeautifulSoup(await page.content(), "html.parser")
        if NO_RESULT_TEXT in soup.text:
            return size, None, None

        script = await page.locator("script#__NEXT_DATA__").inner_text()
        data = json.loads(script)

        items = extract_items(data)
        if not items:
            return size, None, None

        item = items[0]
        return (
            size,
            item.get("price"),
            f"https://paypayfleamarket.yahoo.co.jp/item/{item.get('id')}",
        )

    except Exception as e:
        print(f"[ERROR] {size}: {e}")
        return size, None, None
    finally:
        await page.close()

# ==================================================
# メイン処理
# ==================================================
async def run():
    input_ws = gc.open_by_url(SPREADSHEET_URL).get_worksheet_by_id(INPUT_SHEET_GID)
    output_ws = gc.open_by_url(SPREADSHEET_URL).get_worksheet_by_id(OUTPUT_SHEET_GID)

    # 入力（ID / NAME）
    input_rows = input_ws.get_all_records()
    id_name_map = {
        row["NAME"]: row["ID"]
        for row in input_rows
        if row.get("ID") and row.get("NAME")
    }

    # ---------- 出力シート安全初期化 ----------
    values = output_ws.get_all_values()
    if not values:
        output_ws.append_row(HEADERS)
        existing = []
    elif len(values) == 1:
        existing = []
    else:
        existing = output_ws.get_all_records()

    row_map = {
        (r["ID"], r["size"], r["site"]): idx + 2
        for idx, r in enumerate(existing)
    }

    # ---------- Playwright ----------
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        try:
            for keyword, product_id in id_name_map.items():
                print(f"========== {keyword} ==========")
                urls = generate_size_search_urls(keyword)

                for size, url in urls.items():
                    size, price, item_url = await fetch_min_price(browser, size, url)

                    site = "YA"
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    values = [
                        product_id,
                        keyword,
                        size,
                        site,
                        price or 0,
                        item_url or "",
                        now,
                    ]

                    key = (product_id, size, site)
                    if key in row_map:
                        output_ws.update(
                            f"A{row_map[key]}:G{row_map[key]}",
                            [values],
                            value_input_option="USER_ENTERED",
                        )
                        print(f"更新 {size} ¥{price}")
                    else:
                        output_ws.append_row(values, value_input_option="USER_ENTERED")
                        print(f"追加 {size} ¥{price}")

        finally:
            await browser.close()

# ==================================================
# 実行
# ==================================================
if __name__ == "__main__":
    asyncio.run(run())
