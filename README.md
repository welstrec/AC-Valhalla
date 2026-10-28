# Free Assassin's Creed Valhalla Trainer for PC | Valhalla Companion

**English** | [Español](README.es.md)

**Valhalla Companion** is a **free Assassin's Creed Valhalla trainer for Windows x64**, with an inventory editor, resource quantity controls and gameplay helpers. Browse 2,327 items and 290 packs in a catalogue organized by category.

**Free for everyone to download and use, with no payments or subscriptions.** The portable EXE works without Cheat Engine or a separate Python installation.

**Version 0.5.0 · in-game validation pending.** Automated tests use simulated data and dedicated test processes. Finding a matching signature does not guarantee compatibility with every game version. See [docs/VALIDATION.md](docs/VALIDATION.md) for the scope of verification.

## Download the free trainer

Download and extract the portable package from [the latest release](https://github.com/welstrec/AC-Valhalla/releases/latest). Keep the included notices and licenses.

1. Open Valhalla and load a save.
2. Run `ValhallaCompanion-0.5.0.exe`.
3. Click **Find game**, select the process and click **Attach**.
4. Check **Compatibility** before using a feature.
5. Browse **Items and packs** or open **My inventory**.
6. Click **Disconnect** to restore temporary helpers before closing the application.

Only the `ACValhalla.exe` and `ACValhalla_plus.exe` processes are accepted. If Windows denies process access, reopen the application using **Run as administrator**.

## Choose English or Spanish

Open **Language / Idioma** and choose **English** or **Español**. Your choice is saved locally for future launches. Application controls, categories, item types, quantities, helper descriptions and notices are available in both languages.

Item and pack names retain the reference catalogue's original spelling so they can be matched with the source table and searched consistently. Identifiers do not change with the interface language. Proper names are not given invented translations or presented as the game's official localized names.

## Trainer and inventory editor features

- **Catalogue:** 2,327 items and 290 packs, organized into Store and events, Base game, and Expansions and extras. Includes search, item types, favorites and a list of each pack's contents.
- **Lists and images:** compact rows with icons, optional cards and an expandable detail panel. The original illustrations interpret names and categories; they are not official images or proof of an item's appearance in the game. You can import local PNG, JPEG or WebP images.
- **Quantities:** shows the latest inventory reading, its time and a refresh button. Distinguishes **Not read**, **No record** and a measured zero. It does not continuously monitor memory.
- **Requests:** prepare and review a list before submitting it. Identifiers are deduplicated; adding one again replaces its quantity. A failure stops the list, and attempted requests are not retried automatically.
- **Missing resources ("??"):** an explicit repair action requests one unit of each of thirteen missing basic resources, then checks the result. Expansion, event and challenge resources, and rations, are excluded. A missing record is not treated as a zero balance.
- **Gameplay helpers:** persistence, horse, drinking, Orlog, stealth, silver and experience multipliers, and map requests. Each feature has its own compatibility diagnosis. Remote shops and equipped-item editing remain disabled.

The interface supports keyboard navigation, visible focus, accent-insensitive search and adjustable text size in **View**. `Ctrl+F` opens search; `Ctrl++`, `Ctrl+-` and `Ctrl+0` change text size.

## Quantities and compatibility

Identified equipment is limited to one unit and skipped if already present, with a fresh inventory reading before requesting it. This is a duplicate-prevention rule, **not a native capacity discovered in the game**. Mixed packs retain one unit per piece of equipment and the selected quantity for resources. Existing duplicates are displayed as read and are not removed automatically.

Resources accept values such as **9,999** and **100,000**, within the conservative editing range of **0 to 2,147,483,647**; requests start at one. This does not establish that every item supports that range in the game. The source table does not expose each item's internal capacity: a current quantity of 99 is not taken to mean a maximum of 99. Submitting a request requires a valid reading and checks the sum of current and requested quantities. Rations cannot be edited directly.

Quantities and addresses are checked again before a write. Temporary patches are verified and restored on disconnect; the game's executable on disk is not changed. Items or rewards saved in a game save may persist after disconnecting. Crash-free operation is not guaranteed. If a remote call times out, the session is blocked and the game must be restarted before reconnecting.

The source table's filename says `1.6.2`, but its internal notes declare game `1.5.3`, table `1.1` and Cheat Engine `7.4`. The application retains that discrepancy in its diagnostics.

## Development and building

Requires **64-bit Python 3.13** on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

Verify without attaching to the game:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe run.py --self-test
```

Create the EXE and packages:

```powershell
.\build.ps1
```

The build runs the tests, verifies the actual EXE and its Windows icon, and writes the executable, portable package, source ZIP and `release.json` to `dist/`. The manifest records the size, SHA-256 and scope of validation. The executable has no publisher digital signature. Generated files and the local development environment are excluded from the repository.

The catalogue is imported statically from `data/source.CT`; Lua, Auto Assembler and instructions in the document are not executed. Regenerate it while retaining the original filename and provenance metadata with:

```powershell
.\.venv\Scripts\python.exe tools\import_ct.py data\source.CT --source-name "AC Valhalla Inventory Editor - 1.6.2.CT"
```

User documentation is available in [English](README.md) and [Spanish](README.es.md). Technical validation is described in [English](docs/VALIDATION.md) and [Español](docs/VALIDACION.md).

## Local data and credits

Favorites and imported images are stored in `%LOCALAPPDATA%/ValhallaCompanion/library/`; rotating local logs are stored in `%LOCALAPPDATA%/ValhallaCompanion/app.log`. Diagnostics are exported manually. The application does not send data or download updates.

The original table credits **budabum** and its contributors. Credits, provenance and dependency licenses are in [THIRD_PARTY.md](THIRD_PARTY.md) and [third_party/](third_party/). Third-party licenses remain separate; this repository does not assign them a new blanket license. The project is not affiliated with Ubisoft or Cheat Engine.
