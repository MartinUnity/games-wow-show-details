# TODO

## Task 1: Backup & store logs compressed.

### Sub-task 1:
Since the raw logs takes up a lot of space, lets move them from WoW folder into our own data-folder and keep them here in a compressed state. It is pure text, so they should compress very nicely.

Logs are stored in:
"/home/martin/.local/share/Steam/steamapps/compatdata/4076040504/pfx/drive_c/Program Files (x86)/World of Warcraft/_retail_/Logs"

Since we tail the the latest logs, then lets keep the last 3-5 in the wow dir, but move the rest to our own dir (datadir?) and compress.

When using --full-import now change the behaviour to:

- Ask the user if wow has been shutdown - if no, abort, if yes, continue.
- If continue, move _all_ WowLogs into the datadir
- Only use all the logfiles here
  - Ideally unpack within python directly, read file content, iterate to next file etc.

## Task 2: Tidy up repo

- Ensure we only have 1x parsed_combat_data.csv in the root dir; move/archive the old .bak.* in an archived/ dir or the like; delete when more than 10.