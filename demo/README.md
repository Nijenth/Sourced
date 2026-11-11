# Brightleaf Studio widget demo

Brightleaf Studio and its handbook are fictional. To serve the demo from the Sourced API, create a workspace, upload `data/sample/Brightleaf_Studio_Employee_Handbook.pdf`, and wait for the worker to mark it ready. Create a public widget that selects only that handbook, add the demo’s website origin, and set `SOURCED_DEMO_SITE_KEY` to its public key. Open `/demo/` on the demo host. Enable the widget’s hosted-page setting if the demo shares the Sourced public origin.

Run the Playwright walkthrough from the repository root with Docker and Chromium installed.

```sh
python -m frontend.e2e.run_live --widgets
```

The runner provisions a disposable pgvector database with the bundled handbook and a second PDF excluded from the widget. It runs the demo on a different origin from the widget API. Add `--screenshots` to capture the settings, the open demo widget, and the hosted chat at phone width.
