# Original-price absence contract

Current official promotion original prices and historical prices are different
facts. A price above the current selling price does not prove a current offer.
Weekly promotion membership alone is not proof either.

## Collection and merge

Listing and Detail emit a complete-price-observation flag only when the main
price region and a positive numeric selling price were captured. Malformed
optional price/discount text prevents a complete observation. A unit price
fallback cannot authorize clearing an original price.

The common price merge clears an explicitly absent original price and discount
only for a complete observation. Missing keys, unknown observations, legacy
snapshots and failed page reads retain the previous values. A newer complete
Detail price observation takes precedence over the earlier Listing scan.

An original-price/discount change creates a lightweight PRICE_METADATA_CHANGE
plan even if the selling price is unchanged. This prevents unchanged products
from silently retaining expired promotion metadata. The explicit observation
flag is retained in the Listing snapshot; it is not a new database schema field.

## Existing-data reconciliation

The finite runner `scripts/run_verified_original_price_clear.py` defaults to a
preview and uses `database.price_correction.apply_verified_original_price_absence`.
Commit requires the daily lock, verified restorable backup, current formal
business date/head, an exact official product URL and article number, a fully
captured main price region, an unchanged positive selling price, absent original
and discount nodes, a current SKU and an unchanged old original price.

The transaction updates only `products.original_price` and its update timestamp.
It retains Chinese/source text and freshness, selling prices, weekly promotion
membership, Presence, lifecycle and existing price history. Immutable run
evidence contains the captured official observations and before/after values.
ORIGINAL_PRICE_CORRECTION audit events are distinct from price-drop or promotion
events. Repeating already-applied evidence returns NOOP without new events.
The committed head is synchronized to Master/State through the existing sync API.

## Export

Export reads the corrected PRIMARY facts and does not hide invalid historical
original prices by editing Excel. Derived discount remarks use the same current
price pair, so clearing obsolete original prices also removes their derived
discount remarks. Genuine same-day original prices remain unchanged.

This local branch is not deployed to production main. Actual test, backup,
browser evidence, apply and re-export results are recorded under runtime.
