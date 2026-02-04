import asyncio
import json
import re
import requests
from urllib.parse import quote

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

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
# メイン
# ==================================================
async def run():
    keyword = 'Nike Air Jordan 1 Retro High OG "Yellow Ochre"'
    print(f"=== KEYWORD: {keyword} ===")

    items = search_items(keyword, limit=30)
    print(f"search API items: {len(items)}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        results = []

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
                continue  # サイズ取れない商品は除外

            result = {
                "id": item_id,
                "title": title,
                "price": price,
                "sizes": sizes,
                "url": f"https://paypayfleamarket.yahoo.co.jp/item/{item_id}",
            }
            results.append(result)

            print("\n----------------------------")
            print("id   :", item_id)
            print("title:", title)
            print("price:", price)
            print("sizes:", sizes)

        await browser.close()

    print("\n=== FINAL RESULT COUNT ===")
    print(len(results))

# ==================================================
# 実行
# ==================================================
if __name__ == "__main__":
    asyncio.run(run())