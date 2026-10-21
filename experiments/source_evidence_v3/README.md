# EXPERIMENTAL — SYNTHETIC, NOT DEPLOYABLE

This isolated proof of concept creates a fictional schema-v2 fixture in SQLite
memory and applies candidate v3 DDL only to that marked in-memory connection.
It refuses file-backed databases. It is not imported by the app and has no
network, migration command, service, or production identity integration.

Run its focused tests with:

```sh
python3 -m unittest tests.test_evidence_v3_spike -v
```

The synthetic collector and owner capabilities are test seams, not credentials
or authentication. Direct access to Python or SQLite is outside this
prototype's security boundary; do not use it with real records or databases.
Production `SCHEMA_VERSION` and migrations remain v2 and unchanged.
