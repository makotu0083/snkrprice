# =========================================================
# GitHub Actions 用 Mercari Scraper
#  - update=1 のみ
#  - 新品・未使用
#  - 販売中のみ
# =========================================================

import os
import json
import asyncio
import re
from datetime import date
from urllib.parse import quote

import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright, Page
from bs4 import BeautifulSoup

# ===============================
# 環境変数
# ===============================
SPREADSHEET_URL = os.environ["SPREADSHEET_URL"]
SERVICE_ACCOUNT_INFO = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])

INPUT_GID = int(os.environ.get("INPUT_GID", 0))
OUTPUT_GID = int(os.environ.get("OUTPUT_GID", 777284074))

# ===============================
# Google Sheets 認証
# ===============================
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_info(
    SERVICE_ACCOUNT_INFO,
    scopes=SCOPES
)
gc = gspread.authorize(creds)

sh = gc.open_by_url(SPREADSHEET_URL)
input_ws = sh.get_worksheet_by_id(INPUT_GID)
output_ws = sh.get_worksheet_by_id(OUTPUT_GID)

# ===============================
# サイズ抽出パターン
# ===============================
SIZE_PATTERNS = [
    r"表記サイズ[：:\s]*([0-9]{2}\.?[0-9]?\s*cm)",
    r"サイズ[：:\s]*([0-9]{2}\.?[0-9]?\s*cm)",
    r"\b([0-9]{2}\.?[0-9]?)\s*cm\b",
    r"\bUS\s*([0-9]{1,2}\.?[0-9]?)\b",
    r"JAPAN\s*([0-9]{1,2}\.?[0-9]?)",
    r"JP\s*([0-9]{1,2}\.?[0-9]?)",
]

# ===============================
# 検索API → 商品候補抽出（販売中＋新品）
# ===============================
def extract_item_candidates(data):
    found = []

    if not isinstance(data, dict):
        return found

    for x in data.get("items", []):
        try:
            # 新品・未使用
            if int(x.get("itemConditionId", -1)) != 1:
                continue

            # 販売中のみ
            status = (
                x.get("status")
                or x.get("itemStatus")
                or x.get("itemStatusId")
            )
            if status not in ("on_sale", "ON_SALE", 1):
                continue

            price = int(str(x.get("price")).replace(",", ""))
            item_id = x.get("id") or x.get("itemId")
            if not item_id:
                continue

            found.append({
                "id": item_id,
                "price": price,
            })
        except Exception:
            continue

    return found

# ===============================
# 検索 URL
# ===============================
def build_search_url(keyword: str) -> str:
    return f"https://jp.mercari.com/search?keyword={quote(keyword)}"

# ===============================
# 1キーワード処理
# ===============================
async def fetch_cheapest_per_size(page: Page, keyword: str):
    collected = []

    async def handle_response(response):
        try:
            if "application/json" not in response.headers.get("content-type", ""):
                return

            if not (
                "entities:search" in response.url
                or "search_items" in response.url
            ):
                return

            data = json.loads(await response.text())
            collected.extend(extract_item_candidates(data))
        except Exception:
            pass

    page.on("response", lambda r: asyncio.create_task(handle_response(r)))

    await page.goto(build_search_url(keyword), wait_until="domcontentloaded", timeout=120_000)

    for _ in range(5):
        await page.mouse.wheel(0, 3000)
        await page.wait_for_timeout(1200)

    uniq_items = {x["id"]: x for x in collected}.values()
    sorted_items = sorted(uniq_items, key=lambda x: x["price"])

    cheapest = {}

    for item in sorted_items:
        url = f"https://jp.mercari.com/item/{item['id']}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            await page.wait_for_timeout(1500)
        except Exception:
            continue

        html = await page.content()
        size = None
        on_sale = False

        # __NEXT_DATA__ 解析
        m = re.search(r'<script id="__NEXT_DATA__".*?>(.*?)</script>', html, re.S)
        if m:
            try:
                j = json.loads(m.group(1))
                item_json = (
                    j.get("props", {})
                     .get("pageProps", {})
                     .get("item", {})
                     .get("item", {})
                )

                # 念のため販売中再確認
                if item_json.get("itemStatus") != "ON_SALE":
                    continue

                size = item_json.get("itemSize", {}).get("name")
            except Exception:
                pass

        # フォールバック
        if not size:
            text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
            for pat in SIZE_PATTERNS:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    size = m.group(1).strip()
                    break

        if not size:
            continue

        if size not in cheapest:
            cheapest[size] = {
                "size": size,
                "price": item["price"],
                "url": url,
            }

    return cheapest

# ===============================
# メイン処理
# ===============================
async def main():
    today = date.today().isoformat()

    rows = input_ws.get_all_records()
    targets = [r for r in rows if str(r.get("update", "")).strip() == "1"]

    print(f"[INFO] update=1 targets: {len(targets)}")

    delete_ids = {str(r["ID"]) for r in targets}

    output = output_ws.get_all_values()
    if output:
        header, body = output[0], output[1:]
        kept = [r for r in body if r and r[0] not in delete_ids]
        output_ws.clear()
        output_ws.append_row(header)
        if kept:
            output_ws.append_rows(kept)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = await browser.new_page()

        rows_to_add = []

        for r in targets:
            print(f"[START] {r['ID']} / {r['NAME']}")
            result = await fetch_cheapest_per_size(page, r["NAME"])

            for v in result.values():
                rows_to_add.append([
                    r["ID"],
                    r["NAME"],
                    v["size"],
                    "ME",
                    v["price"],
                    v["url"],
                    today,
                ])

        await browser.close()

    if rows_to_add:
        output_ws.append_rows(rows_to_add)
        print(f"[DONE] appended {len(rows_to_add)} rows")
    else:
        print("[DONE] no data")

# ===============================
# 実行
# ===============================
if __name__ == "__main__":
    asyncio.run(main())
