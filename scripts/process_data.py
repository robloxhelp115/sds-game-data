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
    
    categories = [
        "ships", "weaponry", "fighters", "fighterweaponry",
        "shipWeapons", "shipLoadouts", "shipAirwings",
        "fighterWeapons", "fighterLoadouts"
    ]

    dataframes = {}

    for cat in categories:
        if cat in payload and payload[cat]:
            raw_data = payload[cat]

            # Handle both Lua dictionaries { ["Name"] = {...} } and arrays [ {...}, {...} ]
            if isinstance(raw_data, dict):
                records = []
                for key, val in raw_data.items():
                    if isinstance(val, dict):
                        row = {"id": key, **val} if "id" not in val else val
                    else:
                        row = {"id": key, "value": val}
                    records.append(row)
                df = pd.json_normalize(records)
            else:
                df = pd.json_normalize(raw_data)

            # Clean NaNs and serialise nested objects to avoid Sheets API 400 errors
            df = df.fillna("")
            df = df.map(lambda x: json.dumps(x) if isinstance(x, (dict, list)) else x)

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

            # Prepare values list (headers + rows)
            values = [df.columns.values.tolist()] + df.values.tolist()
            total_rows = max(len(values), 1)
            total_cols = max(len(values[0]) if values else 1, 1)

            # Retrieve existing worksheet or create it if missing
            try:
                worksheet = spreadsheet.worksheet(sheet_title)
                # Expand sheet grid if data exceeds existing bounds
                if total_rows > worksheet.row_count or total_cols > worksheet.col_count:
                    worksheet.resize(
                        rows=max(worksheet.row_count, total_rows + 10),
                        cols=max(worksheet.col_count, total_cols + 5)
                    )
            except gspread.WorksheetNotFound:
                worksheet = spreadsheet.add_worksheet(
                    title=sheet_title,
                    rows=total_rows + 20,
                    cols=total_cols + 5
                )
                print(f"[Sheets] Created new worksheet '{sheet_title}'")

            # Wipe stale cells and update starting at A1 using explicit keyword arguments
            worksheet.clear()
            worksheet.update(range_name="A1", values=values)
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

        # Fandom Module sync targets
        module_targets = {
            "ships": "Module:ShipData",
            "fighters": "Module:FighterData",
            "weaponry": "Module:WeaponData",
            "fighterweaponry": "Module:FighterWeaponData"
        }

        for cat, page_title in module_targets.items():
            if cat in payload and payload[cat]:
                page = site.pages[page_title]
                page.save(f"return {format_lua(payload[cat])}", summary="Automated sync from Roblox")
                print(f"[Fandom] Successfully updated {page_title}")

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
