# Shared Action localization contract

Version: 2026-09-21

The target field's own Spanish source is authoritative. Other fields are
context only and cannot add facts. Empty source means `NO_SOURCE` and an empty
target. Field provenance must include source hash, policy version, decision,
review note, and production-write flags. Candidate generation, deterministic
Guard checks, semantic review, and controlled Apply are separate stages.
