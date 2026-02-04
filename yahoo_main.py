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
SEARCH_API = "https://paypayfleamarket.yahoo.co.jp/api/v1/search"

INPUT_SHEET_GID = 0

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ==================================================
# Google Sheets 認証（キーワード取得のみ）
# ==================================================
creds_dict = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
creds = Credentials.from_service_account_info(
    creds_dict,
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
)
gc = gspread.authorize(creds)
SPREADSHEET_URL = os.environ["SPREADSHEET_URL"]

# ==================================================
# サイズ指定 search API デバッグ（10商品分）
# ==================================================
def search_with_size_debug(keyword, size, size_id):
    params = {
        "query": keyword,
        "sort": "price",
        "order": "asc",
        # Web UI と同じ指定
        "specs": f"C_{FACET_ID}:{size_id}",
        "open": 1,
        "page": 1,
        # ★ API側の最大取得数に合わせて多めに
        "limit": 30,
    }

    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Language": "ja-JP,ja;q=0.9",
        "Referer": "https://paypayfleamarket.yahoo.co.jp/",
    }

    r = requests.get(SEARCH_API, params=params, headers=headers, timeout=20)

    print("\n[DBG] ===== SIZE SEARCH DEBUG =====")
    print("[DBG] keyword:", keyword)
    print("[DBG] size:", size)
    print("[DBG] request_url:", r.url)
    print("[DBG] status:", r.status_code)

    try:
        data = r.json()
    except Exception as e:
        print("[DBG] JSON decode error:", e)
        print("[DBG] raw text:", r.text[:500])
        return

    items = data.get("items", []) or []
    print("[DBG] items_len (raw):", len(items))

    if not items:
        print("[DBG] items is EMPTY")
        return

    # ★ 先頭10件を明示的に確認
    print("\n[DBG] ===== FIRST 10 ITEMS =====")
    for i, item in enumerate(items[:10]):
        print(f"\n[DBG] item[{i}] summary:")
        print("  id         :", item.get("id"))
        print("  title      :", item.get("title"))
        print("  price      :", item.get("price"))
        print("  itemStatus :", item.get("itemStatus"))
        print("  condition  :", item.get("condition"))

    # ★ 完全な raw JSON を1件だけ出す（構造確認用）
    print("\n[DBG] ===== RAW ITEM[0] JSON =====")
    print(json.dumps(items[0], ensure_ascii=False, indent=2))

# ==================================================
# メイン（1キーワード・27cmのみ）
# ==================================================
def run():
    input_ws = gc.open_by_url(SPREADSHEET_URL).get_worksheet_by_id(INPUT_SHEET_GID)
    rows = input_ws.get_all_records()

    for row in rows:
        keyword = row.get("NAME")
        if not keyword:
            continue

        print(f"\n========== DEBUG TARGET: {keyword} ==========")

        # ★ 27cm のみ検証
        search_with_size_debug(
            keyword,
            "27cm",
            SIZE_SPECS_MAP["27cm"],
        )
        break  # 1キーワードで終了

# ==================================================
# 実行
# ==================================================
if __name__ == "__main__":
    run()