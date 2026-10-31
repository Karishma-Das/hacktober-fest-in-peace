# Hacktober-Fest-In-Peace

Finds your dead bookmarks, digs up their old copies from the Wayback Machine,
and lets a local Gemma model write an epitaph for each one.

## How it works
1. Reads a bookmarks export or a list of URLs.
2. Checks which links are dead.
3. Looks up each dead link in the Wayback Machine.
4. A local model (Gemma via Ollama) writes a two-line epitaph.
5. Builds `tombstones.html`, a gallery of tombstones.

![best tombstone](Screenshot.png)

## Run it
1. Install Python and Ollama, then run: ollama pull gemma3:4b
2. pip install requests
3. python cemetery.py demo_urls.txt --model gemma3:4b
4. Open tombstones.html

## Privacy, honestly
The AI runs on your own machine. Link checking still contacts each site,
and Wayback lookups send those URLs to archive.org.

Built for Hacktoberfest Week 5: Ghost in the Machine.
