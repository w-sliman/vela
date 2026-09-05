Both fail with the gold patch applied and without it, so neither can measure the
agent's work here. `test_pyopenssl_redirect` reaches an external HTTPS host and
`test_mixed_case_scheme_acceptable` depends on the real httpbin service rather
than the local one; the container has no egress by design.

Caveat on this instance, recorded rather than hidden: 5 of its 6 FAIL_TO_PASS
tests already pass at base, so only one of them actually discriminates. Gold
still reaches 6/6 and base does not, so the instance grades correctly, but it is
a weaker test than the dataset intends.
