# Validation evidence archives

ZIP files are intentionally ignored by Git to keep generated measurements out
of repository history. Accepted archives are published as immutable assets in
the [validation-2026-08-19 GitHub release](https://github.com/ClementSCH99/nhr-rt/releases/tag/validation-2026-08-19).
The tracked checksum file allows verification after download.

| Archive | Retained evidence |
|---|---|
| `documentation-history-20260819.zip` | Historical roadmap, implementation guides and accepted Session 4/5 narratives before product-documentation cleanup |
| `session2-readonly-acquisitions-20260817.zip` | Two long read-only NHR CSV acquisitions from Session 2 |
| `session3a-validated-20260817.zip` | Final approved 3A run `20260817T200404Z` |
| `session3b-validated-20260817.zip` | Final approved 3B run `20260817T204115Z` |
| `session3c-validated-20260818.zip` | Disabled connection-loss preflight `20260818T175945Z` and final active run `20260818T180011Z` |
| `session4-validated-20260818.zip` | Full Session 4 chronology: 22 reports, 15 CSV files and five approved local profiles |
| `session5-validated-20260819.zip` | Full Session 5 chronology, approved profiles, dynamic CSV inputs, reports and measurement CSV files |

Failed, superseded and empty runs are normally not archived. Session 4 is kept
as a complete exception because its diagnostic runs document the current
settling-time, signed-counter and timeout-boundary behavior. Runner scripts
recreate their normal result directories when future tests are executed.
The corresponding historical narratives are included in
`documentation-history-20260819.zip`.

## SHA-256

```text
C08C7B42B86251F0385D1219819506331A144050595E64A27088048870D083AC  documentation-history-20260819.zip
20CA6FDC321E1B3B60BE919E42BBB1D9D4B6A793E52456A912FE7F1D4A8764A7  session2-readonly-acquisitions-20260817.zip
4D270A876678F6AA13E9DC6AF44E95ECC86A2EEDA682BA37ED04EDC60E4034F2  session3a-validated-20260817.zip
25B4A98A3664472B55E17C22DDE58AA89BF38E8B1E8D625CD3C421DF05B361AD  session3b-validated-20260817.zip
54631589676256AAAB47D395EECA9EC3784068C90DA8A1CEB9545EFBCA61FC96  session3c-validated-20260818.zip
3E7C11C33AA8C3A5E8696CD8A8A26B6A2B0781E20AC9C606846C8FFDB5F18D72  session4-validated-20260818.zip
01C42FD119DB72B67C8F89AD1F24236D878832132CE817CB3FCAABE28B810178  session5-validated-20260819.zip
```
