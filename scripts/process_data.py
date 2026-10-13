import os
import json
import pandas as pd
import mwclient
import gspread

DATA_FILE = "data/raw_payload.json"
WIKI_SUBDOMAIN = "ship-defense-simulator-roblox"

def main():
    if not os.path.exists(DATA_FILE):
        print("[Process] No raw data file found. Skipping.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        payload = json.load(f)

    # 1. Update version.txt
    version = payload.get("place_version") or payload.get("placeVersion")
    if version is not None:
        with open("version.txt", "w", encoding="utf-8") as vf:
            vf.write(str(version))
        print(f"[Process] Updated version.txt to v{version}")

    # 2. Extract DataFrames & Export Flat CSVs
    os.makedirs("csv", exist_ok=True)
    
    # Recognised data categories (covers both schema variations)
    categories = [
        "ships", "weaponry", "fighters", "fighterweaponry",
        "shipWeapons", "shipLoadouts", "shipAirwings",
        "fighterWeapons", "fighterLoadouts"
    ]

    dataframes = {}

    for cat in categories:
        if cat in payload and payload[cat]:
            df = pd.json_normalize(payload[cat])
            # Replace NaN/None values with empty strings for clean presentation
            df = df.fillna("")
            df.to_csv(f"csv/{cat}.csv", index=False)
            dataframes[cat] = df
            print(f"[Process] Generated csv/{cat}.csv ({len(df)} rows)")

    # 3. Update Google Sheets via gspread
    sync_to_google_sheets(dataframes)

    # 4. Update Fandom MediaWiki Modules
    sync_to_fandom(payload)


def sync_to_google_sheets(dataframes):
    service_account_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")

    if not service_account_str or not spreadsheet_id:
        print("[Sheets] Missing GOOGLE_SERVICE_ACCOUNT_JSON or SPREADSHEET_ID. Skipping Google Sheets sync.")
        return

    try:
        service_account_info = json.loads(service_account_str)
        gc = gspread.service_account_from_dict(service_account_info)
        spreadsheet = gc.open_by_key(spreadsheet_id)

        for cat, df in dataframes.items():
            sheet_title = f"raw_{cat}"

            # Retrieve existing worksheet or create it if missing
            try:
                worksheet = spreadsheet.worksheet(sheet_title)
            except gspread.WorksheetNotFound:
                worksheet = spreadsheet.add_worksheet(title=sheet_title, rows=max(100, len(df) + 10), cols=max(20, len(df.columns) + 5))
                print(f"[Sheets] Created new worksheet '{sheet_title}'")

            # Wipe stale cells
            worksheet.clear()

            # Format data into a 2D list: headers row followed by values
            values = [df.columns.values.tolist()] + df.values.tolist()
            worksheet.update(values)
            print(f"[Sheets] Successfully pushed {len(df)} rows to '{sheet_title}'")

    except Exception as e:
        print(f"[Sheets] Failed to update Google Sheets: {e}")


def sync_to_fandom(payload):
    bot_user = os.environ.get("FANDOM_USER")
    bot_pass = os.environ.get("FANDOM_PASS")

    if not bot_user or not bot_pass:
        print("[Fandom] Missing bot credentials. Skipping Fandom sync.")
        return

    try:
        site = mwclient.Site(f"{WIKI_SUBDOMAIN}.fandom.com", path="/")
        site.login(bot_user, bot_pass)

        if "ships" in payload and payload["ships"]:
            page = site.pages["Module:ShipData"]
            page.save(f"return {format_lua(payload['ships'])}", summary="Automated sync from Roblox")
            print("[Fandom] Successfully updated Module:ShipData")

        if "fighters" in payload and payload["fighters"]:
            page = site.pages["Module:FighterData"]
            page.save(f"return {format_lua(payload['fighters'])}", summary="Automated sync from Roblox")
            print("[Fandom] Successfully updated Module:FighterData")

    except Exception as e:
        print(f"[Fandom] Failed to update Fandom: {e}")


def format_lua(obj):
    if isinstance(obj, list):
        items = ",\n  ".join(format_lua(x) for x in obj)
        return f"{{\n  {items}\n}}"
    elif isinstance(obj, dict):
        entries = []
        for k, v in obj.items():
            key_str = k if str(k).isidentifier() else f'["{k}"]'
            entries.append(f"{key_str} = {format_lua(v)}")
        return "{ " + ", ".join(entries) + " }"
    elif isinstance(obj, str):
        return json.dumps(obj)
    elif isinstance(obj, bool):
        return "true" if obj else "false"
    elif obj is None:
        return "nil"
    else:
        return str(obj)


if __name__ == "__main__":
    main()
