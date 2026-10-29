import os
import json
from collections import Counter
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

    # 1. Update version.txt (explicit check to ensure build 0 is written during testing)
    version = payload.get("place_version") if "place_version" in payload else payload.get("placeVersion")
    if version is not None:
        with open("version.txt", "w", encoding="utf-8") as vf:
            vf.write(str(version))
        print(f"[Process] Updated version.txt to v{version}")

    # 2. Parse payload tables into structured DataFrames
    dataframes = {
        "ships": parse_ships(payload.get("ships", {})),
        "weaponry": parse_weaponry(payload.get("weaponry", {})),
        "fighters": parse_fighters(payload.get("fighters", {})),
        "fighterweaponry": parse_fighterweaponry(payload.get("fighterweaponry", {}))
    }

    # 3. Export flat CSVs
    os.makedirs("csv", exist_ok=True)
    for cat, df in dataframes.items():
        if not df.empty:
            df.to_csv(f"csv/{cat}.csv", index=False)
            print(f"[Process] Generated csv/{cat}.csv ({len(df)} rows)")

    # 4. Push updates to Google Sheets
    sync_to_google_sheets(dataframes)

    # 5. Push updates to Fandom MediaWiki Modules
    sync_to_fandom(payload)


def parse_ships(ships_dict):
    if not isinstance(ships_dict, dict):
        return pd.DataFrame()

    records = []
    for ship_name, s_data in ships_dict.items():
        if not isinstance(s_data, dict):
            continue

        row = {"Name": s_data.get("Name", ship_name)}

        # Unpack Lobby Stats
        lobby = s_data.get("Lobby_Stats", {})
        if isinstance(lobby, dict):
            for k, v in lobby.items():
                row[f"Lobby_{k.title()}"] = v

        # Unpack Game Stats
        game_st = s_data.get("Game_Stats", {})
        if isinstance(game_st, dict):
            for k, v in game_st.items():
                if k == "COMPLIMENT":
                    if isinstance(v, dict) and v:
                        items = []
                        for comp_name, comp_info in v.items():
                            if isinstance(comp_info, dict):
                                amt = comp_info.get("AMOUNT", 1)
                                mode = comp_info.get("DEFAULT_MODE", "")
                                items.append(f"{amt}x {comp_name} ({mode})" if mode else f"{amt}x {comp_name}")
                            else:
                                items.append(str(comp_name))
                        row["Compliment"] = ", ".join(items)
                    else:
                        row["Compliment"] = ""
                else:
                    row[k.title()] = v

        # Format Modifiers array
        mods = s_data.get("Modifiers", [])
        row["Modifiers"] = ", ".join(str(m) for m in mods) if isinstance(mods, list) else str(mods)

        records.append(row)

    df = pd.DataFrame(records)
    return clean_dataframe(df)


def parse_weaponry(weaponry_dict):
    if not isinstance(weaponry_dict, dict):
        return pd.DataFrame()

    records = []
    for category_group, weapons in weaponry_dict.items():
        if isinstance(weapons, dict):
            for w_name, w_stats in weapons.items():
                if isinstance(w_stats, dict):
                    row = {
                        "Category": category_group,
                        "Name": w_name,
                        **w_stats
                    }
                    records.append(row)

    df = pd.DataFrame(records)
    return clean_dataframe(df)


def parse_fighters(fighters_dict):
    if not isinstance(fighters_dict, dict):
        return pd.DataFrame()

    records = []
    for f_name, f_data in fighters_dict.items():
        if not isinstance(f_data, dict):
            continue

        row = {"Name": f_name}
        for k, v in f_data.items():
            if k == "Weaponry" and isinstance(v, list):
                counts = Counter(v)
                row["Weaponry"] = ", ".join(f"{cnt}x {item}" if cnt > 1 else item for item, cnt in counts.items())
            else:
                row[k] = v
        records.append(row)

    df = pd.DataFrame(records)
    return clean_dataframe(df)


def parse_fighterweaponry(fighterweaponry_dict):
    if not isinstance(fighterweaponry_dict, dict):
        return pd.DataFrame()

    records = []
    for fw_name, fw_stats in fighterweaponry_dict.items():
        if isinstance(fw_stats, dict):
            row = {"Name": fw_name, **fw_stats}
            records.append(row)

    df = pd.DataFrame(records)
    return clean_dataframe(df)


def clean_dataframe(df):
    if df.empty:
        return df
    # Replace NaNs with empty string
    df = df.fillna("")
    # Ensure nested structures serialize to strings to prevent Sheets API 400 errors
    df = df.map(lambda x: json.dumps(x) if isinstance(x, (dict, list)) else x)
    return df


def sync_to_google_sheets(dataframes):
    service_account_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")

    if not service_account_str or not spreadsheet_id:
        print("[Sheets] Missing GOOGLE_SERVICE_ACCOUNT_JSON or SPREADSHEET_ID. Skipping Google Sheets sync.")
        return

    # Automatically extract ID if a full URL was supplied
    if "/d/" in spreadsheet_id:
        spreadsheet_id = spreadsheet_id.split("/d/")[1].split("/")[0]

    try:
        service_account_info = json.loads(service_account_str)
        gc = gspread.service_account_from_dict(service_account_info)
        spreadsheet = gc.open_by_key(spreadsheet_id)

        for cat, df in dataframes.items():
            if df.empty:
                continue

            sheet_title = f"raw_{cat}"
            values = [df.columns.values.tolist()] + df.values.tolist()
            total_rows = max(len(values), 1)
            total_cols = max(len(values[0]) if values else 1, 1)

            try:
                worksheet = spreadsheet.worksheet(sheet_title)
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
