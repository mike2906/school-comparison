# Admission rule watch state

Written by .github/workflows/watch-admission-rules.yml on main. `state.json` holds, per watched source, the hash and cleaned text last seen, the links already seen, and failure counts. Deleting it makes the next run take a fresh baseline (no issues for that run).
