# Schematic Acquisition for Board-Level Repair

Use when a diagnosis needs net names, component designators, test-point
locations or board layout that the FixIndex record does not carry.

## Procedure

1. Take the exact board code, aliases and codename from the MCP device record
   first (`board_identifier`, `aliases`) and search with those, not just the
   marketing name. Same-phone families often share a name but not a board.
2. Search `"<model> <codename> schematic boardview"`. Ask for the full set:
   schematic PDFs, boardview / component-placement diagrams, and a service
   manual. A single "schematic" PDF may contain only RF pages — check the
   page count and described scope before treating it as complete.
3. Prefer an archive of multiple PDF parts over a single viewer link. If a
   direct PDF URL rejects downloads, switch to the archive link instead of
   iterating on user-agent and referer headers.
4. Public Google Drive files can be fetched directly:
   `curl -fL "https://drive.google.com/uc?export=download&id=<file-id>" -o set.bin`
   then check with `file set.bin` before extracting.
5. List archive entries before extracting. If the host publishes an archive
   password, set it explicitly:
   `z = zipfile.ZipFile(path); print(z.namelist()); z.setpassword(b'<published-password>'); z.extractall(dest)`
6. Merge the parts into one PDF with pypdf and verify the result rather than
   assuming the merge worked:
   `w = PdfWriter(); [w.append(f) for f in sorted(files)]; w.write(out); len(PdfReader(out).pages)`
   Trust the reader's page count, not `file`/metadata output, which can report
   stale counts on linearized PDFs.
7. Deliver the merged file into the user's working directory; on Telegram
   attach it with `MEDIA:<absolute path>`. Keep the original parts in a
   subfolder so they can be sent individually if asked.

## Pitfalls

- Never copy generic web voltage or diode-mode ranges into a device-specific
  answer — they are technique, not values for this board. Take values from the
  schematic or a known-good board.
- Third-party archives are not official documentation: name the source and
  flag that the file is unverified rather than presenting it as a service manual.
- Do not tell the user to check a schematic manually while a public archive is
  still untried; try the archive link first.
