import asyncio
import gspread
import os
import json

from playwright.async_api import async_playwright
from google.oauth2.service_account import Credentials

# =====================
# Sheets設定
# =====================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SPREADSHEET_URL = os.environ["SPREADSHEET_URL"]
TARGET_GID = int(os.environ.get("TARGET_GID", "0"))

SERVICE_ACCOUNT_INFO = json.loads(
    os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
)

# =====================
# 商品情報取得
# =====================

async def fetch_product(product_code):

    url = f"https://snkrdunk.com/products/{product_code}"

    try:

        async with async_playwright() as p:

            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )

            page = await browser.new_page()

            print(f"[ACCESS] {product_code}")

            await page.goto(url, timeout=90000)
            await page.wait_for_load_state("networkidle")

            name = await page.text_content("h1")

            name_jp = ""
            jp = await page.query_selector("p.product-name-jp")

            if jp:
                name_jp = (await jp.text_content()).strip()

            info = {}

            rows = await page.query_selector_all(
                "table.product-detail-info-table tr"
            )

            for r in rows:

                th = await r.query_selector("th")
                td = await r.query_selector("td")

                if th and td:

                    k = (await th.text_content()).strip()
                    v = (await td.text_content()).strip()

                    info[k] = v

            img_url = None

            img = page.locator('img[src*="upload_bg_removed"]').first

            if await img.count() > 0:
                img_url = await img.get_attribute("src")

            if not img_url:

                img = page.locator('img[src*="cdn.snkrdunk.com"]').first

                if await img.count() > 0:
                    img_url = await img.get_attribute("src")

            await browser.close()

            return {
                "ID": product_code,
                "NAME": name.strip() if name else "",
                "NAME_JP": name_jp,
                "BRAND": info.get("ブランド", ""),
                "MODEL": info.get("モデル", ""),
                "RELEASE": info.get("発売日", ""),
                "PRICE": info.get("定価", ""),
                "IMG": img_url or ""
            }

    except Exception as e:

        print("[ERROR]", e)
        return None


# =====================
# main
# =====================

async def main():

    creds = Credentials.from_service_account_info(
        SERVICE_ACCOUNT_INFO,
        scopes=SCOPES
    )

    gc = gspread.authorize(creds)

    ws = gc.open_by_url(
        SPREADSHEET_URL
    ).get_worksheet_by_id(TARGET_GID)

    rows = ws.get_all_values()

    targets = []
    row_nums = []

    for i, r in enumerate(rows[1:], start=2):

        code = r[0].strip() if len(r) > 0 else ""
        img = r[7].strip() if len(r) > 7 else ""

        if code and not img:

            targets.append(code)
            row_nums.append(i)

    print("targets:", len(targets))

    results = await asyncio.gather(
        *[fetch_product(x) for x in targets]
    )

    for row, res in zip(row_nums, results):

        if not res:
            continue

        ws.update(f"A{row}:G{row}", [[

            res["ID"],
            res["NAME"],
            res["BRAND"],
            res["MODEL"],
            res["RELEASE"],
            res["PRICE"],
            1

        ]])

        ws.update(f"H{row}", [[res["IMG"]]])
        ws.update(f"I{row}", [[res["NAME_JP"]]])

        print("updated:", res["ID"])


if __name__ == "__main__":

    asyncio.run(main())
