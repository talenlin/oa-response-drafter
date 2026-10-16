# OfficeOxide build input

The small OA阅答 build bundles the verified Windows x86-64 `office_oxide` wheel
here. It is intentionally not committed as a binary source artifact.

Run the following from the project root on a network that can reach PyPI:

```powershell
.\desktop\download_office_oxide.ps1
```

The script downloads version `0.1.15` (Python 3.10+, Windows x86-64) and verifies SHA-256
`6af23b79a5e037e027eadc684bf48cf4b91238d7d8c8e86109cf7cfeb1ca4657` before a
release build can use it.

Version 0.1.8 is no longer accepted: a real binary DOC fixture showed that it
flattened tables and discarded embedded images. Version 0.1.15 preserves the
fixture's Chinese text, table, and image. Small-build filenames include
`-轻量版` so they do not replace the complete edition.
