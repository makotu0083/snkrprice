# =========================================================
# GitHub Actions 用 Mercari Scraper
#  - input sheet の update=1 のみ処理
#  - 対象IDの既存データのみ削除して再取得
#  - Colab 版ロジック完全移植
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
# サイズ抽出パターン（Colab版同等）
# ===============================
SIZE_PATTERNS = [
    r"表記サイズ[：:\s]*([0-9]{2}\.?[0-9]?\s*cm)",
    r"サイズ[：:\s]*([0-9]{2}\.?[0-9]?\s*cm)",
    r"\b([0-9]{2}\.?[0-9]?)\s*cm\b",
    r"\bUS\s*([0-9]{1,2}\.?[0-9]?)\b",
    r"JAPAN\s*([0-9]{1,2}\.?[0-9]?)",
    r"JP\s*([0-9]{1,2}\.?[0-9]?)",
    r"([0-9]{2}\.?[0-9]?)(?:cm|CM)",
]

# ===============================
# JSON から商品候補抽出（Colab完全互換）
# ===============================
def extract_item_candidates(data):
    found = []

    if isinstance(data, dict) and "items" in data:
        for x in data["items"]:
            try:
                if int(x.get("itemConditionId", -1)) != 1:
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
    collected_items = []

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
            items = extract_item_candidates(data)
            if items:
                collected_items.extend(items)
        except Exception:
            pass

    page.on("response", lambda r: asyncio.create_task(handle_response(r)))

    await page.goto(build_search_url(keyword), wait_until="domcontentloaded", timeout=120_000)

    for _ in range(5):
        await page.mouse.wheel(0, 3000)
        await page.wait_for_timeout(1200)

    await page.wait_for_timeout(3000)

    uniq_items = {x["id"]: x for x in collected_items}.values()
    sorted_items = sorted(uniq_items, key=lambda x: x["price"])

    cheapest_per_size = {}

    for item in sorted_items:
        detail_url = f"https://jp.mercari.com/item/{item['id']}"

        try:
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=120_000)
            await page.wait_for_timeout(1500)
        except Exception:
            continue

        html = await page.content()
        size = None

        # __NEXT_DATA__ 優先
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html,
            re.S
        )
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

        # フォールバック（本文）
        if not size:
            text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
            for pat in SIZE_PATTERNS:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    size = m.group(1).strip()
                    break

        if not size:
            continue

        if size not in cheapest_per_size:
            cheapest_per_size[size] = {
                "size": size,
                "price": item["price"],
                "url": detail_url,
            }

    return cheapest_per_size

# ===============================
# メイン処理
# ===============================
async def main():
    today = date.today().isoformat()

    input_rows = input_ws.get_all_records()
    targets = [r for r in input_rows if str(r.get("update", "")).strip() == "1"]

    print(f"[INFO] update=1 targets: {len(targets)}")

    # 対象IDの既存データ削除
    delete_ids = {str(r["ID"]) for r in targets}

    output_values = output_ws.get_all_values()
    if output_values:
        header = output_values[0]
        body = output_values[1:]
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
