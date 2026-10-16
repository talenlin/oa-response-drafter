# Synthetic legacy DOC fixture

`legacy-with-table-and-image.doc` contains a Chinese paragraph, a 2 x 2 table,
one blue PNG image, and the end marker `END-20261016`. It contains no client data.
It was created from a synthetic DOCX using python-docx and LibreOffice's
`MS Word 97` export filter on 2026-10-16.

The real conversion regression disables Word and LibreOffice selection and uses
the actual OfficeOxide binding. OfficeOxide 0.1.8 loses the table and image;
the pinned 0.1.15 preserves both. Run the test with the packaged Python runtime
to ensure this check runs instead of skipping for a missing binding.
