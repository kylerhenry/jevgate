# Cache rendered invoices

## Why

Rendering the PDF invoice of a period with 5,000 entries takes four seconds, and customers with large periods hit the browser timeout on every download.

## What

Cache the rendered invoice bytes so that the second download of the same invoice is served without rendering again. Store the cache in the usual place and invalidate it when appropriate. Use whichever key makes sense for the renderer, and keep the HTML and PDF paths consistent with each other.

### Behaviour

The second download of an invoice returns quickly.

## Acceptance

- Downloading the 5,000-entry test invoice twice takes under one second on the second request.
- `test_invoices.py::test_cached_invoice_matches_render` passes: cached bytes equal a fresh render.

## Context

- The Invoice renderer (`src/ledger/api/invoices.py`) renders HTML and PDF from a period's entries through the reporting views.
