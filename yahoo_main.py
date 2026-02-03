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
def _is_on_sale(item: dict) -> bool:
    """
    Yahooフリマの返却フィールドは揺れるので複数候補で判定。
    """
    # よくあるパターン
    if item.get("isSoldOut") is False:
        return True
    if item.get("soldOut") is False:
        return True

    status = (item.get("status") or item.get("itemStatus") or "").lower()
    if status in ("on_sale", "onsale", "selling", "available", "open"):
        return True

    # soldOut系が True なら売り切れ
    if item.get("isSoldOut") is True or item.get("soldOut") is True:
        return False

    # 判定不能なら True 扱いにしない（厳しめ）
    return False


def _is_unused(item: dict) -> bool:
    """
    「未使用」判定（conditions=NEW相当）がどこに入るか揺れるので複数候補で判定。
    """
    cond = (
        item.get("condition")
        or item.get("itemCondition")
        or item.get("productCondition")
        or ""
    )
    cond_s = str(cond).lower()

    # 候補（NEW / 未使用 / unused など）
    if cond_s in ("new", "unused", "未使用"):
        return True

    # 表示名で持っている場合
    cond_label = (item.get("conditionLabel") or item.get("conditionName") or "")
    if "未使用" in str(cond_label):
        return True

    return False


def _has_size_spec(item: dict, facet_id: int, size_value_id: int) -> bool:
    """
    サイズ facet が item の specs に含まれているか。
    これも構造が揺れるので頑健に探す。
    """
    specs = item.get("specs") or item.get("itemSpecs") or []
    # specs が dict の場合もある
    if isinstance(specs, dict):
        specs = [specs]

    for sp in specs:
        if not isinstance(sp, dict):
            continue

        fid = sp.get("facetId") or sp.get("facet_id") or sp.get("id")
        vid = sp.get("valueId") or sp.get("value_id") or sp.get("value")

        try:
            if int(fid) == int(facet_id) and int(vid) == int(size_value_id):
                return True
        except Exception:
            continue

    # specs に無い場合、別フィールドにサイズが入ることもある（例: sizeValueId）
    for k in ("sizeValueId", "shoeSizeValueId", "specValueId"):
        if k in item:
            try:
                if int(item[k]) == int(size_value_id):
                    return True
            except Exception:
                pass

    return False


def fetch_min_price(keyword, size, size_id, debug=False):
    params = {
        "query": keyword,
        "sort": "price",
        "order": "asc",
        # Web側のURLにあった「販売中相当」候補
        "open": 1,
        # 一旦残す（効かないことがあるので後段で自前判定）
        "conditions": "NEW",
        # サーバ側で効けばラッキー、効かなくても自前で弾く
        "specs": f"C_{FACET_ID}:{size_id}",
        "page": 1,
        # ここがポイント：多めに取って自前フィルタ
        "limit": 50,
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://paypayfleamarket.yahoo.co.jp/",
    }

    try:
        r = requests.get(API_BASE, params=params, headers=headers, timeout=20)
        if debug:
            print("[DBG] request_url:", r.url)
            print("[DBG] status:", r.status_code)

        if r.status_code != 200:
            return None, None

        data = r.json()
        items = data.get("items", []) or []

        if debug and items:
            # 先頭アイテムのキーだけ（巨大ログ防止）
            print("[DBG] first_item_keys:", list(items[0].keys())[:40])

        # 価格昇順のまま、条件に合う最初の1件を採用
        for item in items:
            if not _is_on_sale(item):
                continue
            if not _is_unused(item):
                continue
            if not _has_size_spec(item, FACET_ID, size_id):
                continue

            price = item.get("price")
            item_id = item.get("id")
            if price is None or item_id is None:
                continue
            return price, f"https://paypayfleamarket.yahoo.co.jp/item/{item_id}"

        # 条件に合うものが無い場合：デバッグ情報を追加
        if debug:
            print(f"[DBG] no_match for size={size}. items_count={len(items)}")
            # 先頭3件だけ、判定の結果を出す
            for i, it in enumerate(items[:3]):
                print(
                    f"[DBG] item[{i}] "
                    f"on_sale={_is_on_sale(it)} unused={_is_unused(it)} size_ok={_has_size_spec(it, FACET_ID, size_id)} "
                    f"price={it.get('price')} id={it.get('id')}"
                )

        return None, None

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
