import requests
import urllib.parse
import json
import time
import os
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

# ==================================================
# 定数
# ==================================================
SEARCH_API = "https://paypayfleamarket.yahoo.co.jp/api/v1/search"

FACET_ID = 27435
SIZE_SPECS_MAP = {
    "23cm": 236665, "23.5cm": 236666, "24cm": 236667, "24.5cm": 236668,
    "25cm": 236669, "25.5cm": 236670, "26cm": 236671, "26.5cm": 236672,
    "27cm": 236673, "27.5cm": 236674, "28cm": 236675, "28.5cm": 236676,
    "29cm": 236677, "29.5cm": 236678, "30cm": 236679, "30.5cm": 260922,
    "31cm": 260923, "31.5cm": 260924, "32cm": 260925,
}

HEADERS_ROW = ["ID", "NAME", "size", "site", "price", "url", "updated_at"]

INPUT_SHEET_GID = 0
OUTPUT_SHEET_GID = 1994370799

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
# Yahoo フリマ search API から最安取得
# ==================================================
def fetch_min_price(keyword: str, size_id: int):
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

    try:
        r = requests.get(SEARCH_API, params=params, headers=headers, timeout=20)
        if r.status_code != 200:
            return None, None

        items = r.json().get("items", []) or []

        # 価格昇順 → 条件一致の最初の1件が最安
        for item in items:
            if item.get("itemStatus") != "OPEN":
                continue
            if item.get("condition") != "new":
                continue

            price = item.get("price")
            item_id = item.get("id")

            if price is None or not item_id:
                continue

            url = f"https://paypayfleamarket.yahoo.co.jp/item/{item_id}"
            return price, url

        return None, None

    except Exception as e:
        print(f"[ERROR] fetch failed: {keyword} size_id={size_id} err={e}")
        return None, None

# ==================================================
# メイン処理
# ==================================================
def run():
    input_ws = gc.open_by_url(SPREADSHEET_URL).get_worksheet_by_id(INPUT_SHEET_GID)
    output_ws = gc.open_by_url(SPREADSHEET_URL).get_worksheet_by_id(OUTPUT_SHEET_GID)

    # 入力（ID / NAME）
    input_rows = input_ws.get_all_records()
    id_name_map = {
        row["NAME"]: row["ID"]
        for row in input_rows
        if row.get("ID") and row.get("NAME")
    }

    # 出力シート初期化
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

    # ==============================
    # 実行
    # ==============================
    for keyword, product_id in id_name_map.items():
        print(f"========== {keyword} ==========")

        for size, size_id in SIZE_SPECS_MAP.items():
            price, url = fetch_min_price(keyword, size_id)
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

            time.sleep(0.3)  # レート制御

# ==================================================
# 実行
# ==================================================
if __name__ == "__main__":
    run()