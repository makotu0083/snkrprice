import requests
import urllib.parse
from datetime import datetime
import json
import gspread
from google.oauth2.service_account import Credentials
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
API_BASE = "https://paypayfleamarket.yahoo.co.jp/api/v1/search"

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
# Yahoo API 取得
# ==================================================
def fetch_min_price(keyword, size, size_id):
    params = {
        "query": keyword,
        "sort": "price",
        "order": "asc",
        "conditions": "NEW",
        "specs": f"C_{FACET_ID}:{size_id}",
        "page": 1,
        "limit": 1,
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    try:
        r = requests.get(API_BASE, params=params, headers=headers, timeout=20)
        if r.status_code != 200:
            return None, None

        data = r.json()
        items = data.get("items", [])

        if not items:
            return None, None

        item = items[0]
        return item.get("price"), f"https://paypayfleamarket.yahoo.co.jp/item/{item.get('id')}"

    except Exception as e:
        print(f"[ERROR] {keyword} {size}: {e}")
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

            time.sleep(0.3)  # 念のためのレート制御

# ==================================================
# 実行
# ==================================================
if __name__ == "__main__":
    run()
