# Sample labels

A ready-to-run batch: `batch_example.csv` maps each image below to its
application data. In the app's **Batch import** tab, upload `batch_example.csv`
and select all four images.

| Image | Source | Expected verdict |
|---|---|---|
| `lamberts.png` | Real TTB-approved wine label | **PASS** |
| `prinsi.jpg` | Real TTB-approved imported wine (tests country of origin) | **PASS** |
| `fail_titlecase.png` | Warning header in Title Case instead of ALL CAPS | **FAIL** |
| `lamberts_mismatch.png` | Same image as `lamberts.png`, but the CSV lists a wrong ABV (40%) | **FAIL** |

The two `PASS` labels are genuine records from the public
[TTB COLA Registry](https://www.ttbonline.gov/colasonline/publicSearchColasBasic.do);
the `FAIL` cases demonstrate the strict Government Warning check and the
application-vs-label mismatch check.

For single-label testing, upload any one image and enter its fields on the
**Single label** tab.
