---
tags: [jevgate/component]
aliases: [renderer, invoices]
path: src/ledger/api/invoices.py
provides: Renders invoices to HTML and PDF from a period's entries using server-side templates.
interface: "render_invoice(invoice_id, fmt='html') -> bytes"
---

# Invoice renderer

Server rendering was decided in [[0002-server-rendered-invoices]]. Reads through the reporting views, never the entries table.
