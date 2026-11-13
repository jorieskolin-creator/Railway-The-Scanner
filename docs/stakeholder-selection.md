# Stakeholder selection

Historical reference: scanner_two.py at commit 34b7f92b2f93d3731eadbc29f0dfbe58d939d076, before the November changes. This supplies the six-tier hierarchy and company-size restrictions, not proof that every historical production run worked.

Search current executive leadership first, then each demand-relevant functional leadership family. Both current company ID and role-family clauses are mandatory. Searches run even when the company executive roster contains usable entries. Collection reserves budget for later rounds, with 32 collection attempts at the default eight-candidate setting (absolute maximum 80). Results beyond the bounded collection budget are not exhaustively ranked.

The shared stakeholder_policy module ranks CEO/COO/enterprise chair and managing directors, other C-suite/EVP, VP/board members, directors, heads of function, then relevant managers/leads. It fixes VP titles accidentally matching President. Managers are excluded at 5,000+ employees, and at 200+ when at least three senior stakeholders survive. Unrecognized non-leadership titles are excluded. Within a tier, demand relevance determines priority. The display limit is applied after ranking the collected pool.

Current affiliation must be supported by an active company ID, an entity-confirmed company executive roster, or explicit current-affiliation verification from stored sources. Historical/retired, unrelated and unverified profiles are excluded. Cross-source identity conflicts suppress stale titles unless an active Employee API match supplies current evidence. Provider snapshots still require validation of remit before outreach.

Both Buying Insight sections use the same verified pool. Generated names cannot enter either section. Missing named owners remain role-level discovery targets. Old stakeholder rows lacking current-affiliation evidence require an Evidence DB rebuild. Rebuild the Evidence DB and regenerate Buying Insight after deployment; saved HTML does not update automatically.

Coresignal logs and provider_status record executive counts, returned and unique employee IDs, collection attempts/success/failure, per-round counts, rejection reasons, retained candidates and limit exclusions. Buying Insight logs and report metadata record selected and displayed names. Counts do not log personal names. Negative affiliation records are retained in Evidence DB metadata to prevent stale identities reappearing from another source.

Validation: 39 new regression cases passed. Full suite: 376 passed, with the same 22 pre-existing failures as the prior baseline. Offline replay of the supplied 20-profile Veikkaus export with digital/customer/product/financial demand retained 11 candidates before the configured eight-candidate limit; four profiles had a different active employer and five roles did not match those demand families. This is an offline check, not a live API or Railway deployment test.
