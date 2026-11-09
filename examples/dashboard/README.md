# Dashboard demo

`demo/` is a synthetic corpus for the Streamlit desk. It is not a live capture
and not an archived Polymarket download. Mention timestamps run from
2026-11-07 15:00 UTC through 2026-11-09 18:00 UTC. Polymarket prices in this
folder use provenance `synthetic_demo`.

Rebuild the JSON from the hardcoded items, still with no network:

```bash
python examples/dashboard/build_demo.py
```

Open the desk on loopback:

```bash
pip install -e '.[dashboard]'
python -m marketpulse.dashboard --demo
```

The server listens on `127.0.0.1:8501`. To read a real `marketpulse` run instead,
generate artifacts first and start the desk without `--demo`:

```bash
python -m marketpulse --tickers JPM --polymarket polymarket.links.yml -o output --data-dir data
python -m marketpulse.dashboard
```

That generation command can call Yahoo, Reddit, and Polymarket. The `--demo`
command does not.
