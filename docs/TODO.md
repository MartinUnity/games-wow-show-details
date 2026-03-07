# TODO

## Task 1: Backup & store logs compressed. ✓ DONE

### Sub-task 1:
Since the raw logs takes up a lot of space, lets move them from WoW folder into our own data-folder and keep them here in a compressed state. It is pure text, so they should compress very nicely.

Logs are stored in:
"/home/martin/.local/share/Steam/steamapps/compatdata/4076040504/pfx/drive_c/Program Files (x86)/World of Warcraft/_retail_/Logs"

Since we tail the the latest logs, then lets move all but the latest to our own dir (datadir?) and compress.

Moving the files should happen on two occasions - and this is why it should be possible to invoke it as a method:

- Periodically wake up a thread/schedule and check from the wow-parser.py what logfiles are there; if more than the latest -> move files.
- Using --full-import should always move all files but the last one
  - Full import must be aware about dual-placement of files (latest in wow game folder + datadir)

- Interactiving with the new files should ideally unpack them within python directly, read file content, iterate to next file etc - avoid unpacking to disk first if this is possible; otherwise unpack + delete one at a time.
  - We rarerun run --full-impact so we can be less efficient in how we do this

## Task 2: Tidy up repo ✓ DONE

- Ensure we only have 1x parsed_combat_data.csv in the root dir; move/archive the old .bak.* in an archived/parsed-combat_data/ dir or the like; delete when more than 10.
  - `_backup_file()` now writes into `data/csv-backups/` and auto-prunes to `MAX_CSV_BACKUPS = 10`.
  - Existing 6 `.bak.*` files relocated to `data/csv-backups/`.