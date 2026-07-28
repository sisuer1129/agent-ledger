# Source Manifest

This manifest records the public candidate files after sanitization. SHA-256 values are calculated from the candidate files. The generated audit files themselves are intentionally excluded to avoid a self-referential manifest.

| Path | SHA-256 | Status |
| --- | --- | --- |
| `.env.example` | `e3ee297766d8e1c055ccdc0099bb3e432bb41e882608946d71d0a2adb1bcf35a` | rewritten |
| `.gitignore` | `a75c60e2655d77e2f1a112982d592d708cadfd9b69a1c5ec0419dec4ff270301` | rewritten |
| `DOCUMENTATION_REVIEW.md` | `5cd8418a3d00c8e55dcd17fba64b908cbcd5d20163668e86d4ddfaede7272839` | rewritten |
| `LICENSE` | `e65ca8293477682d9e4e31d67269a3fdcffae5d6266043c22f9ab0173537efc5` | rewritten |
| `README.md` | `49ebdd05c2af2bdaf2c1f4d838e3fb51173a2ed84badc5d124b17604ec9d658f` | rewritten |
| `SECURITY.md` | `fc23637d5611207d5f2a0a904565bd252a9d3649f873d512c141b8e8d3251822` | rewritten |
| `docs/DEPLOYMENT.md` | `6a498d3ccf32483f341ebae148e5f542eb3160c94ba7174681e94230430a583e` | rewritten |
| `requirements.txt` | `e9eae9074f097c101b125118dcd281694b2df5425064ab983bb2ca54cf50bea3` | rewritten |
| `release/app/billing.py` | `43561eb5c7ce95477cc3424f4603f360755dc89242dfd2d3f5257ba32a5fa87d` | copied |
| `release/app/budget_service.py` | `d144a5e6bd29fef185de58115c96e20dc13c059e380fc544e927c35427aecf06` | copied |
| `release/app/canonical_taxonomy.py` | `b41b008c68e1a04d2e63c3bcf4405a35202f9c1c1d833b6e2a0edabc440f7f61` | sanitized |
| `release/app/category_colors.json` | `004eefd8ac557477d0ac451f1f910fa6ed6582cc69a9f301479d3a58a9867d15` | copied |
| `release/app/category_seed.json` | `496ea516a252f8656fda6176f9623e3e24bcaffa43c248f1c014459eada85b0c` | copied |
| `release/app/classification_import.py` | `ba8a4a31b5c795bc70a6fe835b68bc461ea9397f91f95d630851afc8ebbfe7d6` | sanitized |
| `release/app/classifier.py` | `2517f79a2c38f586d092606878bcc66eb1b82b20f59a69f428e07e674b0ba0f2` | sanitized |
| `release/app/config.py` | `7e2fcb150fca5912ad6fbdc5d5dff182b66a667b79ed9d262e3bcc58513e4917` | rewritten |
| `release/app/db.py` | `b3ced6dcf4f1365bbf49a28c7a23a85cc51faf278f5ba48064ba4c49890768d2` | copied |
| `release/app/main.py` | `ce170250c1e8e4d7b5defad99b265f3180adef0d57f5e10101f7c59723f4338d` | sanitized |
| `release/app/migrate_history.py` | `a4243a0c709cb5b2e77591545d5465d52cf24e3197179a7c055cb701d17e5e12` | sanitized |
| `release/app/repositories.py` | `ffe6cda2300bc3493bed042088feeb63bf98ab11ec420b929af5f527e1f2d343` | copied |
| `release/app/routes.py` | `82e560ab4803ea3356502f536eaeefd4fdf4d74f26cf4ed25283174651b867c3` | copied |
| `release/app/seed_catalog.py` | `a6081ae2e06312faf16c69d4903b44f363271d35db095d5891874ce14483920e` | copied |
| `release/app/seed_taxonomy_upgrade.py` | `c045079ad1a464f7addc7f24b3f7282485dbc7826cf4af175ccf99840c8d5d08` | sanitized |
| `release/app/tag_seed.json` | `66b84cb6d3944653098aef8df728ddc7deae0e59ad6f6e265b1978987dbfa7da` | sanitized |
| `release/app/taxonomy.py` | `16fa9d248bfdac8e3d6bbf8c2e63ff2dc7f75d6e77883536865b2600711bda7f` | sanitized |
| `release/app/taxonomy_profile.py` | `490c410a97fe1c81d075a2ec4093842407dc2dc95283cf1c014459eada85b0c` | sanitized |
| `release/app/taxonomy_upgrade.py` | `60855d6ca0c856c85b51354ac8bc1194fc12ff663c4c885c7e07e25b44d74d0c` | copied |
| `release/frontend/app.mjs` | `eb0326436d5f863e189c92f4b9561de3f7320202b497123183eb8b4d4c843d6c` | sanitized |
| `release/frontend/charts.mjs` | `ca97f72bc48edb8e18991645af464b7c5eb36551d6840db26b340c1922573ead` | copied |
| `release/frontend/index.html` | `ea9d7cd2a89677015690b3164e25ac31fd0c13a8bb6de8fc78784f59152bc8db` | sanitized |
| `release/frontend/styles.css` | `91e7ab4867f3e0891d5554b374f37617ce9c7d2d7c1bdc715b49e483d418a913` | copied |
| `release/frontend/tokens.css` | `f8648dc43384878581d2b6dfe29d3da918a7487c673edb18a3c0e9312c454703` | copied |
| `release/scripts/verify-release.sh` | `686e1e11e55bc81b176fc885a147062d850c159f21c41cdea33ee6ae20734fca` | sanitized |
| `tests/__init__.py` | `3560f42fa5820ee02bd105bbf49b040ee08d5703b0e148faf4856e05c91620f1` | copied |
| `tests/frontend_contract.test.mjs` | `d32e43b049b69f6833fc770d289ae515aca7d0b9fca598d97b92f38404b8cce1` | sanitized |
| `tests/helpers.py` | `24ec6c3ae893498a69104bdac5bf8b1ea542bd567357edd8225da3004a096a88` | copied |
| `tests/test_accounts_budgets_api.py` | `7cd6dee575bc91457109f22e5652e88e1b065016a2699e9f95338e454ff407a3` | sanitized |
| `tests/test_billing.py` | `4d4259c2c5147bbace546cc1ad9427ed0dbaad50735661f57c8be16183a23bfd` | copied |
| `tests/test_classification_import.py` | `72beb319dcd49a51bb3c92e9d0d10f0748ccc5915f7e507f79b4f590afb5ba67` | sanitized |
| `tests/test_classifier.py` | `9d46883ad31f554916402ea1340f0bacbb699001085a4ca267893caea9f1e57e` | sanitized |
| `tests/test_history_migration.py` | `a2f80476e81db81c6797480be5ebf36e39f927fa7689e894f0b9a7c2bf55880d` | sanitized |
| `tests/test_public_config.py` | `3c14fda02dc705ef321680fbf0a46ce75d925aaaaf9896a51866a13b0aad8eb4` | rewritten |
| `tests/test_schema_migration.py` | `6d98c6ee94bceba83bea6d05b8b90d365f4451a90bf18f49ba726cfe2fbc3b19` | sanitized |
| `tests/test_seed_catalog.py` | `516d03d87403fac2f0813486d4dc109af31a18be237e181d66a5d063afa10c65` | copied |
| `tests/test_seed_taxonomy_upgrade.py` | `790d009543ac768338f4d1a948a9933dc260003c94ec4ec69d07b5a2b2481a76` | sanitized |
| `tests/test_smoke.py` | `a50dfe07dee0dfbab67b9df880dea7ee6c57324d0c61e91ff1e73f94ea180022` | copied |
| `tests/test_taxonomy_profile.py` | `515bcca4f5b606adda2a3fdc6046db3560665fcc41dc48b76c273ecc19b46000` | sanitized |
| `tests/test_taxonomy_upgrade.py` | `f1a866d2e2af8deb6e2745eee734e79c6df214533e636d913c712b6db54ada22` | copied |
| `tests/test_transactions_stats_api.py` | `4c79aadd5c6c945e09058a92824a16dca07eac79eb64163070d4a95224e50d92` | sanitized |

`release/scripts/retire_accounts.py` and `tests/test_retire_accounts.py` are intentionally absent.
