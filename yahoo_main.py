import requests
import json
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import os
import time

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
SEARCH_API = "https://paypayfleamarket.yahoo.co.jp/api/v1/search"
DETAIL_API = "https://paypayfleamarket.yahoo.co.jp/api/v1/item/{}"

INPUT_SHEET_GID = 0
OUTPUT_SHEET_GID = 1994370799

HEADERS = ["ID", "NAME", "size", "site", "price", "url", "updated_at"]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

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
# 判定（detail API 専用）
# ==================================================
def is_on_sale(detail: dict) -> bool:
    return detail.get("itemStatus") == "OPEN"


def is_unused(detail: dict) -> bool:
    return str(detail.get("condition")).lower() == "NEW"

# ==================================================
# サイズ指定 search → detail で最安取得
# ==================================================
def fetch_min_price(keyword, size, size_id):
    params = {
        "query": keyword,
        "sort": "price",
        "order": "asc",
        "specs": f"C_{FACET_ID}:{size_id}",
        "open": 1,
        "page": 1,
        "limit": 30,
    }

    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
        "Referer": "https://paypayfleamarket.yahoo.co.jp/",
    }

    r = requests.get(SEARCH_API, params=params, headers=headers, timeout=20)
    if r.status_code != 200:
        return None, None

    items = r.json().get("items", []) or []

    for item in items:
        item_id = item.get("id")
        if not item_id:
            continue

        dr = requests.get(
            DETAIL_API.format(item_id),
            headers=headers,
            timeout=20,
        )
        if dr.status_code != 200:
            continue

        detail = dr.json()

        if not is_on_sale(detail):
            continue
        if not is_unused(detail):
            continue

        price = detail.get("price")
        if price is None:
            continue

        url = f"https://paypayfleamarket.yahoo.co.jp/item/{item_id}"
        return price, url

    return None, None

# ==================================================
# メイン処理
# ==================================================
def run():
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

    for keyword, product_id in id_name_map.items():
        print(f"========== {keyword} ==========")

        for size, size_id in SIZE_SPECS_MAP.items():
            price, url = fetch_min_price(keyword, size, size_id)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            values = [
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
                    [values],
                    value_input_option="USER_ENTERED",
                )
                print(f"更新 {size} ¥{price}")
            else:
                output_ws.append_row(values, value_input_option="USER_ENTERED")
                print(f"追加 {size} ¥{price}")

            time.sleep(0.4)  # Yahoo側負荷対策

# ==================================================
# 実行
# ==================================================
if __name__ == "__main__":
    run()