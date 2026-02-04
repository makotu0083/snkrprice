import requests
import json

# ==================================================
# 調査対象（確実に「販売中・未使用」と分かっているID）
# ==================================================
TARGET_ITEM_ID = "z553578806"

DETAIL_API = f"https://paypayfleamarket.yahoo.co.jp/api/v1/item/{TARGET_ITEM_ID}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Referer": "https://paypayfleamarket.yahoo.co.jp/",
}

# ==================================================
# detail API 生ログ出力
# ==================================================
def run():
    print("[DBG] detail API url:")
    print(DETAIL_API)

    r = requests.get(DETAIL_API, headers=HEADERS, timeout=20)

    print("\n[DBG] status:", r.status_code)

    try:
        data = r.json()
    except Exception as e:
        print("[DBG] JSON decode error:", e)
        print("[DBG] raw text:")
        print(r.text[:1000])
        return

    print("\n[DBG] ===== detail API raw json =====")
    print(json.dumps(data, ensure_ascii=False, indent=2))

    # ----------------------------------------------
    # 見やすいように、重要そうなキーだけも抜粋
    # ----------------------------------------------
    print("\n[DBG] ===== extracted candidates =====")

    for key in [
        "itemStatus",
        "saleStatus",
        "isSoldOut",
        "condition",
        "conditionCode",
        "conditionLabel",
    ]:
        if key in data:
            print(f"[DBG] {key} =", data[key])

    # specs にサイズや状態が入るケースもある
    specs = data.get("specs")
    if specs:
        print("\n[DBG] specs:")
        print(json.dumps(specs, ensure_ascii=False, indent=2))


# ==================================================
# 実行
# ==================================================
if __name__ == "__main__":
    run()