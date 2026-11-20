# Menu sources

Original owner-authored SM83 menu from nand2mario at
`510be34b49f5bc666ad828260ed0e193546736dd`. The assembly and five linked
shade assets are unchanged. The layout only renames its source unit.
The menu builds byte-identically with the local `dmg-loader-v1` profile.

Run from the repository root:

```sh
python3 src/nand2mario/tools/sw_games.py build menu --output workdir/games
```

The [menu/library contract](../../../../../wiki/src/nand2mario/sw/HAS.md)
owns execution and verification. The import manifest records every included
file. Unused donor design studies and their renderers are not runtime assets.
