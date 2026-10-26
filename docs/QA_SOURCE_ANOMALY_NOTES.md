# Verbatim source anomaly notes and language QA

Local candidate only; this does not activate code or approve any source facts.

Already approved details retain malformed official attributes as explicit
evidence, e.g. `来源异常：官网字段Incluye oído=Sí`. The language gate previously
treated this evidence as untranslated prose. Language QA now masks only that
complete note when its key, exact Sí/No value and multiplicity match the same
details source. It preserves the annotation in the stored/exported value.

The shared helper is used by field QA, export repair QA and research release.
It does not whitelist individual Spanish words, borrow another field, infer
handle/ear semantics, approve a translation, clear STALE, fix invalid physical
state values, or bypass numeric/source/approval checks. Unknown, partial,
duplicated, changed or embellished notes remain visible to language detection.

`Sustancia: Válido` remains an invalid physical-state source and blocks QA.
An evidence annotation is not a corrected physical state. Source conflicts
remain in the review queue. This change adds no release exception or dictionary
entry and performs no database or schema writes.

The legacy export formatter also matched only `带把手` inside `是否带把手`
and dropped the source Boolean when producing an anomaly note. Its replacement
now consumes the whole pair and preserves the own-source Sí/No value. It only
operates on one source/target pair with agreeing polarity. Unknown, duplicate
or conflicting values are not repaired, and missing notes are not appended.
This is a display repair; it does not approve the legacy stored interpretation.
