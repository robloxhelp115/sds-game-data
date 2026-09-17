import os
import json
import pandas as pd
import mwclient

DATA_FILE = "data/raw_payload.json"
WIKI_SUBDOMAIN = "ship-defense-simulator-roblox"

def main():
    if not os.path.exists(DATA_FILE):
        print("No raw data file found. Skipping.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        payload = json.load(f)

    # 1. Update version.txt if placeVersion exists
    if "placeVersion" in payload:
        with open("version.txt", "w", encoding="utf-8") as vf:
            vf.write(str(payload["placeVersion"]))

    # 2. Export Flat CSVs for Google Sheets
    os.makedirs("csv", exist_ok=True)
    categories = [
        "ships", "shipWeapons", "shipLoadouts", "shipAirwings",
        "fighters", "fighterWeapons", "fighterLoadouts"
    ]

    for cat in categories:
        if cat in payload and payload[cat]:
            df = pd.json_normalize(payload[cat])
            df.to_csv(f"csv/{cat}.csv", index=False)
            print(f"Generated csv/{cat}.csv ({len(df)} rows)")

    # 3. Update Fandom MediaWiki Modules
    bot_user = os.environ.get("FANDOM_USER")
    bot_pass = os.environ.get("FANDOM_PASS")

    if bot_user and bot_pass:
        site = mwclient.Site(f"{WIKI_SUBDOMAIN}.fandom.com", path="/")
        site.login(bot_user, bot_pass)

        if "ships" in payload:
            page = site.pages["Module:ShipData"]
            page.save(f"return {format_lua(payload['ships'])}", summary="Automated sync from Roblox")
            print("Successfully updated Module:ShipData on Fandom")

        if "fighters" in payload:
            page = site.pages["Module:FighterData"]
            page.save(f"return {format_lua(payload['fighters'])}", summary="Automated sync from Roblox")
            print("Successfully updated Module:FighterData on Fandom")

def format_lua(obj):
    if isinstance(obj, list):
        items = ",\n  ".join(format_lua(x) for x in obj)
        return f"{{\n  {items}\n}}"
    elif isinstance(obj, dict):
        entries = []
        for k, v in obj.items():
            key_str = k if k.isidentifier() else f'["{k}"]'
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
