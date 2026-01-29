# =========================================================
# GitHub Actions 用 Mercari Scraper（update=1 のみ実行）
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
# 環境変数（GitHub Actions）
# ===============================
SPREADSHEET_URL = os.environ["SPREADSHEET_URL"]
SERVICE_ACCOUNT_INFO = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])

INPUT_GID = 0
OUTPUT_GID = 777284074

# ===============================
# Google Sheets 認証（Service Account）
# ===============================
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
credentials = Credentials.from_service_account_info(
    SERVICE_ACCOUNT_INFO,
    scopes=SCOPES
)
gc = gspread.authorize(credentials)

sh = gc.open_by_url(SPREADSHEET_URL)
input_ws = sh.get_worksheet_by_id(INPUT_GID)
output_ws = sh.get_worksheet_by_id(OUTPUT_GID)

# ===============================
# サイズ抽出パターン
# ===============================
SIZE_PATTERNS = [
    r"([0-9]{2}\.?[0-9]?)\s*cm",
    r"\bUS\s*([0-9]{1,2}\.?[0-9]?)\b",
]

# ===============================
# JSONから商品候補抽出
# ===============================
def extract_item_candidates(data):
    items = []
    for x in data.get("items", []):
        try:
            if int(x.get("itemConditionId", -1)) != 1:
                continue
            price = int(str(x.get("price")).replace(",", ""))
            items.append({
                "id": x.get("id") or x.get("itemId"),
                "price": price,
            })
        except Exception:
            continue
    return items

# ===============================
# 検索URL
# ===============================
def build_search_url(keyword: str) -> str:
    return f"https://jp.mercari.com/search?keyword={quote(keyword)}"

# ===============================
# 1キーワード処理
# ===============================
async def fetch_cheapest_per_size(page: Page, keyword: str):
    collected = []

    async def handle_response(res):
        if "application/json" not in res.headers.get("content-type", ""):
            return
        if "search_items" not in res.url:
            return
        try:
            data = json.loads(await res.text())
            collected.extend(extract_item_candidates(data))
        except Exception:
            pass

    page.on("response", lambda r: asyncio.create_task(handle_response(r)))

    await page.goto(build_search_url(keyword), timeout=120_000)
    for _ in range(5):
        await page.mouse.wheel(0, 3000)
        await page.wait_for_timeout(1200)

    uniq = {x["id"]: x for x in collected}.values()
    sorted_items = sorted(uniq, key=lambda x: x["price"])

    result = {}

    for item in sorted_items:
        await page.goto(f"https://jp.mercari.com/item/{item['id']}", timeout=120_000)
        html = await page.content()

        size = None

        # __NEXT_DATA__ からサイズ取得
        m = re.search(r'<script id="__NEXT_DATA__".*?>(.*?)</script>', html, re.S)
        if m:
            try:
                j = json.loads(m.group(1))
                size = (
                    j.get("props", {})
                     .get("pageProps", {})
                     .get("item", {})
                     .get("item", {})
                     .get("itemSize", {})
                     .get("name")
                )
            except Exception:
                pass

        # フォールバック（本文テキスト）
        if not size:
            text = BeautifulSoup(html, "html.parser").get_text()
            for pat in SIZE_PATTERNS:
                m = re.search(pat, text)
                if m:
                    size = m.group(1)
                    break

        if not size:
            continue

        if size not in result:
            result[size] = {
                "size": size,
                "price": item["price"],
                "url": f"https://jp.mercari.com/item/{item['id']}",
            }

    return result

# ===============================
# メイン処理
# ===============================
async def main():
    today = date.today().isoformat()

    rows = input_ws.get_all_records()
    targets = [r for r in rows if str(r.get("update", "")).strip() == "1"]

    print(f"[INFO] update=1 targets: {len(targets)}")

    delete_ids = {r["ID"] for r in targets}

    # 既存出力削除（対象IDのみ）
    output = output_ws.get_all_values()
    if not output:
        return

    header, body = output[0], output[1:]
    kept = [r for r in body if r[0] not in delete_ids]

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

        new_rows = []

        for r in targets:
            print(f"[START] {r['ID']} / {r['NAME']}")
            result = await fetch_cheapest_per_size(page, r["NAME"])
            for item in result.values():
                new_rows.append([
                    r["ID"],
                    r["NAME"],
                    item["size"],
                    "ME",
                    item["price"],
                    item["url"],
                    today,
                ])

        await browser.close()

    if new_rows:
        output_ws.append_rows(new_rows)
        print(f"[DONE] appended {len(new_rows)} rows")
    else:
        print("[DONE] no data")

# ===============================
# 実行
# ===============================
if __name__ == "__main__":
    asyncio.run(main())
