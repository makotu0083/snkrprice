# =========================================================
# GitHub Actions 用 Mercari Scraper
#  - update=1 のみ
#  - 新品・未使用
#  - 販売中のみ（URLで status=on_sale）
#  - size は数値のみで出力
#  - URL に afid を付与
# =========================================================

import os
import json
import asyncio
import re
from datetime import datetime
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
OUTPUT_GID = int(os.environ.get("OUTPUT_GID", 208209208))

AFID = "4997609843"  # ★ 固定 afid

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
]

# ===============================
# サイズ正規化（数値のみ）
# ===============================
def normalize_size(size_str: str) -> str | None:
    if not size_str:
        return None
    m = re.search(r"([0-9]{1,2}(?:\.[0-9])?)", size_str)
    return m.group(1) if m else None

# ===============================
# 検索API → 商品候補抽出
# ===============================
def extract_item_candidates(data):
    items = []
    if not isinstance(data, dict):
        return items

    for x in data.get("items", []):
        try:
            if int(x.get("itemConditionId", -1)) != 1:
                continue

            price = int(str(x.get("price")).replace(",", ""))
            item_id = x.get("id") or x.get("itemId")
            if not item_id:
                continue

            items.append({
                "id": item_id,
                "price": price,
            })
        except Exception:
            continue

    return items

# ===============================
# 検索 URL（販売中のみ）
# ===============================
def build_search_url(keyword: str) -> str:
    return (
        "https://jp.mercari.com/search"
        f"?keyword={quote(keyword)}"
        "&status=on_sale"
    )

# ===============================
# 1キーワード処理
# ===============================
async def fetch_cheapest_per_size(page: Page, keyword: str):
    collected = []

    async def handle_response(response):
        try:
            if "application/json" not in response.headers.get("content-type", ""):
                return
            if "search" not in response.url:
                return

            data = json.loads(await response.text())
            collected.extend(extract_item_candidates(data))
        except Exception:
            pass

    page.on("response", lambda r: asyncio.create_task(handle_response(r)))

    await page.goto(
        build_search_url(keyword),
        wait_until="domcontentloaded",
        timeout=120_000
    )

    for _ in range(5):
        await page.mouse.wheel(0, 3000)
        await page.wait_for_timeout(1200)

    uniq_items = {x["id"]: x for x in collected}.values()
    sorted_items = sorted(uniq_items, key=lambda x: x["price"])

    cheapest = {}

    for item in sorted_items:
        base_url = f"https://jp.mercari.com/item/{item['id']}"
        url = f"{base_url}?afid={AFID}"  # ★ afid 付き URL

        try:
            await page.goto(base_url, wait_until="domcontentloaded", timeout=120_000)
            await page.wait_for_timeout(1500)
        except Exception:
            continue

        html = await page.content()
        size = None

        # JSONからサイズ取得
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

        # テキストからサイズ取得
        if not size:
            text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
            for pat in SIZE_PATTERNS:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    size = m.group(1).strip()
                    break

        normalized_size = normalize_size(size)
        if not normalized_size:
            continue

        if normalized_size not in cheapest:
            cheapest[normalized_size] = {
                "size": normalized_size,
                "price": item["price"],
                "url": url,
            }

    return cheapest

# ===============================
# メイン処理
# ===============================
async def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
            print(f"[INFO] size_count={len(result)}")

            for v in result.values():
                rows_to_add.append([
                    r["ID"],
                    r["NAME"],
                    v["size"],
                    "メルカリ",
                    v["price"],
                    v["url"],  # ← afid 付き
                    now,
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
