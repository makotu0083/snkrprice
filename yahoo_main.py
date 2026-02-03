import requests
import json
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
API_BASE = "https://paypayfleamarket.yahoo.co.jp/api/v1/search"

INPUT_SHEET_GID = 0

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
# search API 生ログ取得（調査用）
# ==================================================
def fetch_min_price_debug(keyword, size, size_id):
    params = {
        "query": keyword,
        "sort": "price",
        "order": "asc",
        "open": 1,
        "conditions": "NEW",
        "page": 1,
        "limit": 5,  # 調査なので少数
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Accept-Language": "ja-JP,ja;q=0.9",
        "Referer": "https://paypayfleamarket.yahoo.co.jp/",
    }

    r = requests.get(API_BASE, params=params, headers=headers, timeout=20)

    print("\n[DBG] ===== search API raw response =====")
    print("[DBG] status:", r.status_code)
    print("[DBG] request_url:", r.url)

    try:
        data = r.json()
    except Exception as e:
        print("[DBG] JSON decode error:", e)
        print("[DBG] raw text:", r.text[:500])
        return

    items = data.get("items", []) or []

    print("[DBG] items_len:", len(items))

    if not items:
        print("[DBG] items is EMPTY")
        return

    # item[0] を丸裸で出す（最重要）
    print("[DBG] raw item[0]:")
    print(json.dumps(items[0], ensure_ascii=False, indent=2))

# ==================================================
# メイン処理（調査用）
# ==================================================
def run():
    input_ws = gc.open_by_url(SPREADSHEET_URL).get_worksheet_by_id(INPUT_SHEET_GID)
    input_rows = input_ws.get_all_records()

    # 1商品だけでOK
    for row in input_rows:
        keyword = row.get("NAME")
        if not keyword:
            continue

        print(f"\n========== DEBUG TARGET: {keyword} ==========")

        # 27cm だけ調査
        fetch_min_price_debug(keyword, "27cm", SIZE_SPECS_MAP["27cm"])
        break  # ★ 1商品で終了

# ==================================================
# 実行
# ==================================================
if __name__ == "__main__":
    run()
