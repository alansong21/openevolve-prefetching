# Prefetcher seed programs

Prefetcher-only `.cc` files for seeding the combined OpenEvolve workflow.
Each file implements `openevolve_prefetcher` and can be wrapped with fixed LRU
replacement by `scripts/seed_combined_checkpoint.py`.

| File | Source |
|------|--------|
| `bertigo_l2c.cc` | [CMU-SAFARI DPC4 BertiGo](https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/BertiGO/BertiGo) |
| `anelin_l2c.cc` | [CMU-SAFARI DPC4 ANeLin](https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/BertiGO/ANeLin) |
| `edp_l2c.cc` | [CMU-SAFARI DPC4 EDP](https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/EDP/edp) |
| `berti_plus_l2c.cc` | [CMU-SAFARI DPC4 Berti+ (SPPAM)](https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/SPPAM/berti_plus) |
| `bingo_plus_l2c.cc` | [CMU-SAFARI DPC4 Bingo+ (SPPAM)](https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/SPPAM/bingo_plus) |
| `gberti_l2c.cc` | [CMU-SAFARI DPC4 gBerti](https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/gBerti/gberti) |
| `sberti_l2c.cc` | [CMU-SAFARI DPC4 sBerti](https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/sBerti/sberti) |
| `sms2_l2c.cc` | [CMU-SAFARI DPC4 SMS2 (uMAMA)](https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/uMAMA/umama/sms2) |
| `emender_l2c.cc` | [CMU-SAFARI DPC4 Emender L2](https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/Emender/emender_l2) |

Regenerate from upstream:

```bash
.venv/bin/python scripts/generate_anelin_seed.py
.venv/bin/python scripts/generate_edp_seed.py
.venv/bin/python scripts/generate_berti_plus_seed.py
.venv/bin/python scripts/generate_bingo_plus_seed.py
.venv/bin/python scripts/generate_gberti_seed.py
.venv/bin/python scripts/generate_sberti_seed.py
.venv/bin/python scripts/generate_sms2_seed.py
.venv/bin/python scripts/generate_emender_seed.py
```

## Seed a checkpoint (prefetcher + fixed LRU)

```bash
.venv/bin/python scripts/seed_combined_checkpoint.py \
  --programs openevolve-components/seeds/anelin_l2c.cc \
  --output /tmp/combined_seed_checkpoint \
  --dump-combined /tmp/combined_dump
```

Multiple seeds:

```bash
.venv/bin/python scripts/seed_combined_checkpoint.py \
  --programs openevolve-components/seeds/anelin_l2c.cc \
               openevolve-components/seeds/bertigo_l2c.cc \
  --output /tmp/combined_seed_checkpoint
```

Then resume evolution with `--checkpoint /tmp/combined_seed_checkpoint`.

## Check that every seed compiles

```bash
.venv/bin/python scripts/check_seeds_compile.py
```

This copies each `seeds/*.cc` into `openevolve-components/initial_program.cc`, rebuilds ChampSim, and restores the original `initial_program.cc` when finished. Use `--keep-going` to test every seed even if one fails, and `--object-only` to compile just the prefetcher object.
